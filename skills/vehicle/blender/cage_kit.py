"""zstack/vehicle - hand-shaping kit: build and edit a car-body subdivision cage by hand, through
Blender MCP (execute_blender_code) or headless (`blender -b -P`).

The professional method (see reference/hand-shaping.md): one sparse, mirrored, all-quad cage under
Subdivision Surface, shaped row by row against the spec box and the references, judged only by
reflections, and never cut until the reflections pass. The build then cuts openings and shut lines
into the dense subdivided shell, so the cuts cannot disturb the surface (the guide-mesh outcome).

Session start (idempotent):  K.setup(RUN)  ->  K.cage_from_curves(RUN)  (or K.load_cage(RUN))

Every MCP call after that is ONE small, re-runnable step of this shape:

    import sys; sys.path.insert(0, '<skill>/blender'); import cage_kit as K
    K.checkpoint(RUN, 'before-hood-crown')          # 1. save state outside the session
    K.edit_cage(lambda bm: K.move(bm, rows=range(3, 8), cols=range(16, 22), dz=+0.01))   # 2. edit
    K.metrics(RUN)                                   # 3. print JSON facts
    K.render_checks(RUN, 'r012')                     # 4. fixed-camera MatCap / stripe renders
    K.photo_overlays(RUN, REFS, 'r012')              #    cage over each solved reference photo
    K.aim_viewport('side'); then get_viewport_screenshot   (GUI only)
    K.export_cage(RUN)                               # 5. accepted: write runs/<id>/cage.json

Tools: move / space / relax / circle on rows, columns and loops; insert_loop('row'|'col', i) adds
a loop without changing the surface; fit_surface() makes the subdivided surface pass through
target points (the seed uses it: a subdivision surface shrinks inside its cage).

Objects are always re-fetched by name (undo and mode switches invalidate references):
    CAR_cage   the editable half cage (x >= 0), Mirror + Subdivision modifiers, never applied
    REF_*      spec box, wheel markers, reference images (collection REF, unselectable)
    CAM_*      fixed check cameras (collection CHECK)

Cage vertices carry integer attributes `cage_row` (station along the car, nose = 0) and `cage_col`
(position round the half section, bottom centre = 0, top centre = last); end-cap vertices have
cage_row = -1 (nose) / -2 (tail). Edit by row and column, not by vertex index.

Frame: +X is the car's LEFT, -Y is forward, +Z up, metres (driver's right is -X).
Never use AI 3D generators (Tripo, Hunyuan3D, Hyper3D Rodin, ...) for a car body: their meshes
cannot be rigged or panelled. The cage is built here, by hand.
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CAGE = 'CAR_cage'
SEAM_TOL = 1e-3


# ---------------------------------------------------------------- scene

def _coll(name, hide_select=False):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(c)
    c.hide_select = hide_select
    return c


def _link(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    return ob


def setup(run, refs=None):
    """Metric scene, spec box, wheel markers, reference images and fixed check cameras.
    Idempotent: safe to re-run at the start of every session."""
    spec = json.load(open(os.path.join(run, 'spec.json')))
    sc = bpy.context.scene
    sc.unit_settings.system = 'METRIC'
    sc.unit_settings.scale_length = 1.0
    ref = _coll('REF', hide_select=True)
    L, W, H = spec['length'], spec['width'], spec['height']
    # spec box: the car's published envelope, wire display
    box = bpy.data.objects.get('REF_box')
    if box is None:
        me = bpy.data.meshes.new('REF_box')
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        box = bpy.data.objects.new('REF_box', me)
    box.scale = (W, L, H)
    box.location = (0, 0, H / 2)
    box.display_type = 'WIRE'
    box.hide_render = True
    _link(box, ref)
    # wheel markers: circle empties at the axle centres, radius = tyre radius
    yf = -L / 2 + spec['frontOverhang']
    yr = yf + spec['wheelbase']
    for c in ('FL', 'FR', 'RL', 'RR'):
        front, left = c[0] == 'F', c[1] == 'L'
        r = spec['wheelRadiusFront' if front else 'wheelRadiusRear']
        x = (1 if left else -1) * spec['trackFront' if front else 'trackRear'] / 2
        e = bpy.data.objects.get('REF_wheel_' + c) or bpy.data.objects.new('REF_wheel_' + c, None)
        e.empty_display_type = 'CIRCLE'
        e.empty_display_size = r
        e.location = (x, yf if front else yr, r)
        e.rotation_euler = (0, math.radians(90), 0)          # circle in the YZ plane
        _link(e, ref)
    # reference images from a calibrated blueprint (orthographic image-model views)
    if os.path.exists(os.path.join(run, 'blueprint.json')):
        import blueprints
        for e in blueprints.load(run, refs, opacity=0.2):
            e.empty_image_depth = 'FRONT'
            e.show_empty_image_perspective = False
            _link(e, ref)
    cameras(spec)
    photo_cameras(run)
    print(json.dumps({'setup': True, 'box_m': [W, L, H], 'wheels': 4,
                      'cameras': sorted(o.name for o in bpy.data.objects if o.name.startswith('CAM_'))}))


def _cam(name, loc, target=None, rot_deg=None, ortho=None, lens=50.0, coll=None):
    cd = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    if ortho:
        cd.type, cd.ortho_scale = 'ORTHO', ortho
    else:
        cd.type, cd.lens = 'PERSP', lens
    cd.clip_start, cd.clip_end = 0.05, 200
    c = bpy.data.objects.get(name) or bpy.data.objects.new(name, cd)
    c.location = loc
    if target is not None:
        c.rotation_mode = 'QUATERNION'
        c.rotation_quaternion = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y')
    else:
        c.rotation_mode = 'XYZ'
        c.rotation_euler = [math.radians(a) for a in rot_deg]
    if coll:
        _link(c, coll)
    return c


# fixed check views: (name, location, target, ortho scale or None, lens)
def _views(spec):
    L, W, H = spec['length'], spec['width'], spec['height']
    s = max(L, W) * 1.12
    return {
        'side': ((12, 0, H / 2), (0, 0, H / 2), s, 50),
        'front': ((0, -12, H / 2), (0, 0, H / 2), W * 1.3, 50),
        'rear': ((0, 12, H / 2), (0, 0, H / 2), W * 1.3, 50),
        'top': ((0, 0.0001, 12), (0, 0, 0), s, 50),
        'front34': ((5.2, -6.4, 1.8), (0, -0.2, 0.55), None, 55),
        'rear34': ((-5.0, 6.6, 1.9), (0, 0.3, 0.6), None, 55),
        'eye': ((6.5, -1.0, 1.1), (0, 0.0, 0.6), None, 40),
    }


def cameras(spec=None, run=None):
    spec = spec or json.load(open(os.path.join(run, 'spec.json')))
    chk = _coll('CHECK')
    for k, (loc, tgt, ortho, lens) in _views(spec).items():
        _cam('CAM_' + k, loc, target=tgt, ortho=ortho, lens=lens, coll=chk)


def photo_cameras(run):
    """CAM_photo_<view> for every reference photo whose camera critique.py solved
    (checks/cameras.json). No background image is attached: a .blend must never carry the path
    of a private photo. photo_overlays() does the comparison instead."""
    path = os.path.join(run, 'checks', 'cameras.json')
    if not os.path.exists(path):
        return []
    import render_views
    sc = bpy.context.scene
    keep = (sc.render.resolution_x, sc.render.resolution_y, sc.render.pixel_aspect_y)
    chk = _coll('CHECK')
    out = []
    for v, m in json.load(open(path)).items():
        name = 'CAM_photo_' + v
        cd = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
        c = bpy.data.objects.get(name) or bpy.data.objects.new(name, cd)
        _link(c, chk)
        render_views.cv_camera(c, m['K'], m['R'], m['t'], m['size'])
        c['photoSize'] = list(m['size'])
        c['pixelAspectY'] = sc.render.pixel_aspect_y
        out.append(name)
    sc.render.resolution_x, sc.render.resolution_y, sc.render.pixel_aspect_y = keep
    return out


# ---------------------------------------------------------------- the cage

def _coons(A, B, Cc, D):
    """Coons patch grid from four boundary polylines sharing corners:
    A(v) at u=0, Cc(v) at u=1 (both indexed by v), B(u) at v=1, D(u) at v=0 (both indexed by u)."""
    U, V = len(B) - 1, len(A) - 1
    P00, P10, P01, P11 = D[0], D[U], B[0], B[U]
    grid = []
    for i in range(U + 1):
        u = i / U
        col = []
        for j in range(V + 1):
            v = j / V
            p = ((1 - v) * D[i] + v * B[i] + (1 - u) * A[j] + u * Cc[j]
                 - ((1 - u) * (1 - v) * P00 + u * (1 - v) * P10 + (1 - u) * v * P01 + u * v * P11))
            col.append(p)
        grid.append(col)
    return grid


def _stations(spec, n, end_blend=0.2):
    """Cage stations along Y: nearly even (pros keep control points evenly spaced), only a light
    pull towards the nose and tail where the plan turns. body.cage_stations packs the ends for the
    procedural loft; a hand cage built on that packing starts with 12 mm rows next to 250 mm ones."""
    y0, y1 = -spec.L / 2, spec.L / 2
    return [y0 + (y1 - y0) * ((1 - end_blend) * t + end_blend * (1 - math.cos(math.pi * t)) / 2)
            for t in (i / (n - 1) for i in range(n))]


def _ring_allocation(spec, total, turn_len=0.3):
    """Columns per key span (bottom centre .. top centre). Weight = arc length + turn_len metres per
    radian of turning, averaged over five stations: density follows curvature, flat floor and roof
    get few columns, every span gets at least one, and every design-line key (floor edge, rocker,
    flank, shoulder, belt, rail, roof) stays a vertex column."""
    import body
    w = [0.0] * 8
    for f in (-0.4, -0.2, 0.0, 0.2, 0.4):
        sec, keys = body.section(spec, f * spec.L)
        idx = [keys[k] for k in range(9)]
        for k, (a, b) in enumerate(zip(idx, idx[1:])):
            ln = sum((sec[i + 1] - sec[i]).length for i in range(a, b))
            turn = 0.0
            for i in range(a + 1, b):
                d0, d1 = sec[i] - sec[i - 1], sec[i + 1] - sec[i]
                if d0.length > 1e-9 and d1.length > 1e-9:
                    turn += d0.angle(d1, 0.0)
            w[k] += ln + turn_len * turn
    tot = sum(w)
    alloc = [max(1, round(total * x / tot)) for x in w]
    while sum(alloc) > total:
        k = max((i for i in range(8) if alloc[i] > 1), key=lambda i: alloc[i] - total * w[i] / tot)
        alloc[k] -= 1
    while sum(alloc) < total:
        k = max(range(8), key=lambda i: total * w[i] / tot - alloc[i])
        alloc[k] += 1
    return alloc


def _ring(spec, y, alloc):
    """Half-section points at station y: each key span split into its allocated number of
    segments at equal arc length."""
    import body
    sec, keys = body.section(spec, y)
    idx = [keys[k] for k in range(9)]
    out = []
    for (a, b), m in zip(zip(idx, idx[1:]), alloc):
        pts = sec[a:b + 1]
        acc = [0.0]
        for i in range(len(pts) - 1):
            acc.append(acc[-1] + (pts[i + 1] - pts[i]).length)
        for j in range(m):
            t = acc[-1] * j / m
            i = max(k for k in range(len(acc) - 1) if acc[k] <= t) if t > 0 else 0
            f = 0.0 if acc[i + 1] == acc[i] else (t - acc[i]) / (acc[i + 1] - acc[i])
            p = pts[i] + (pts[i + 1] - pts[i]) * f
            out.append((p[0], p[1]))
    out.append((sec[-1][0], sec[-1][1]))
    return out


def cage_from_curves(run, name=CAGE, stations=None, cols=None, cap_rows=None, crease=0.8, fit_iters=12):
    """Seed the hand-shaping cage from the run's design lines (curves.json): one quad row per
    control station, one column per control point round the half section, end caps filled with
    a Coons quad grid, shoulder-crease edges given a crease weight. The shape starts where the fit
    left it; from here it is edited by hand. Replaces an existing CAR_cage."""
    import body
    cfg = json.load(open(os.path.join(run, 'curves.json')))
    spec = body.BodySpec(cfg)
    ys = _stations(spec, stations or cfg.get('cageStations', 24))
    alloc = _ring_allocation(spec, cols or cfg.get('cageCols', 24))
    rings = [[Vector((max(0.0, x), y, z)) for x, z in _ring(spec, y, alloc)] for y in ys]
    n = len(rings[0])
    crease_col = sum(alloc[:4])                   # key 4 (the shoulder crease) is a column: a design line
    bm = bmesh.new()
    lay_r = bm.verts.layers.int.new('cage_row')
    lay_c = bm.verts.layers.int.new('cage_col')
    grid = []
    for r, ring in enumerate(rings):
        row = []
        for c, p in enumerate(ring):
            if c in (0, n - 1):
                p = Vector((0.0, p.y, p.z))         # section ends sit on the mirror plane
            v = bm.verts.new(p)
            v[lay_r], v[lay_c] = r, c
            row.append(v)
        grid.append(row)
    for a, b in zip(grid, grid[1:]):
        for c in range(n - 1):
            bm.faces.new((a[c], a[c + 1], b[c + 1], b[c]))
    # end caps: Coons quad fill between the half ring and the centre line (x = 0)
    s = cap_rows or max(2, n // 5)
    for ring_vs, tag, sign in ((grid[0], -1, -1), (grid[-1], -2, 1)):
        pts = [v.co.copy() for v in ring_vs]
        A = pts[:s + 1]                           # bottom part of the ring (u = 0)
        Cc = list(reversed(pts[n - 1 - s:]))      # top part (u = 1), from top centre outwards
        B = pts[s:n - s]                          # outer part (v = 1)
        U = len(B) - 1
        D = [pts[0].lerp(pts[n - 1], i / U) for i in range(U + 1)]   # centre line (v = 0)
        for p in D:
            p.x = 0.0
        G = _coons(A, B, Cc, D)
        dome = cfg.get('noseDome' if tag == -1 else 'tailDome', 0.0)
        verts = []
        for i in range(U + 1):
            col = []
            for j in range(s + 1):
                # boundary points reuse the ring's vertices; interior points are new
                if j == s:
                    col.append(ring_vs[s + i])
                elif i == 0:
                    col.append(ring_vs[j])
                elif i == U:
                    col.append(ring_vs[n - 1 - j])
                else:
                    p = G[i][j]
                    p.y += sign * dome * math.sin(math.pi * i / U) * math.sin(math.pi * j / (2 * s))
                    if j == 0:
                        p.x = 0.0
                    v = bm.verts.new(p)
                    v[lay_r], v[lay_c] = tag, -1
                    col.append(v)
            verts.append(col)
        for i in range(U):
            for j in range(s):
                q = (verts[i][j], verts[i + 1][j], verts[i + 1][j + 1], verts[i][j + 1])
                try:
                    bm.faces.new(q if sign > 0 else tuple(reversed(q)))
                except ValueError:
                    pass
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # make the normals point out of the car: the half cage is open on x = 0, so test one face
    f0 = max(bm.faces, key=lambda f: f.calc_center_median().x)
    if f0.normal.x < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    old = bpy.data.objects.get(name)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if old is not None:
        om = old.data
        old.data = me
        if om.users == 0:
            bpy.data.meshes.remove(om)
        ob = old
    else:
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
    for p in me.polygons:
        p.use_smooth = True
    # preview crease along the shoulder line (final export applies subdivision, so glTF needs no crease)
    cr = me.edge_creases_ensure()
    rows = me.attributes['cage_row'].data
    cols = me.attributes['cage_col'].data
    vals = [0.0] * len(me.edges)
    for e in me.edges:
        a, b = e.vertices
        if cols[a].value == cols[b].value == crease_col and rows[a].value >= 0 and rows[b].value >= 0:
            vals[e.index] = crease
    cr.data.foreach_set('value', vals)
    ensure_modifiers(ob, cfg.get('subdivLevels', 2))
    if fit_iters:
        # the seeded points lie ON the design surface, and a subdivision surface shrinks inside its
        # cage: move the cage until the subdivided surface (creases included) passes through them
        fit_surface(name, iters=fit_iters)
    return ob


def fit_surface(name=CAGE, targets=None, iters=8, damping=1.0, verbose=True):
    """Move cage vertices until the subdivided surface (Mirror + Subsurf, creases included, as
    evaluated) passes through `targets` ({vertex index: Vector}, default: where the vertices are
    now). Each step finds the evaluated surface point nearest each target and moves that cage
    vertex along the surface normal by the miss; seam vertices stay on x = 0. Use it after seeding,
    and after dragging vertices to reference points (the dragged point is where the surface must
    pass, not where the cage vertex should sit). Returns the remaining miss in mm (p50, max)."""
    ob = bpy.data.objects[name]
    me = ob.data
    if targets is None:
        targets = {v.index: v.co.copy() for v in me.vertices}
    res = None
    for it in range(iters + 1):
        dg = bpy.context.evaluated_depsgraph_get()
        tree = BVHTree.FromObject(ob, dg)
        miss, moves = [], {}
        for i, t in targets.items():
            loc, n, _, _ = tree.find_nearest(t)
            if loc is None:
                continue
            d = (t - loc).dot(n)
            miss.append(abs(d))
            moves[i] = n * d
        miss.sort()
        res = {'p50_mm': round(1000 * miss[len(miss) // 2], 2), 'max_mm': round(1000 * miss[-1], 2)} if miss else None
        if it == iters or (res and res['max_mm'] < 0.5):
            break
        for i, m in moves.items():
            co = me.vertices[i].co + m * damping
            if abs(targets[i].x) < SEAM_TOL or co.x < 0:
                co.x = 0.0
            me.vertices[i].co = co
        me.update()
    if verbose:
        print(json.dumps({'fit': name, 'iterations': it, 'miss': res}))
    return res


def ensure_modifiers(ob, levels=2):
    m = ob.modifiers.get('Mirror') or ob.modifiers.new('Mirror', 'MIRROR')
    m.use_axis = (True, False, False)
    m.use_clip = True
    m.use_mirror_merge = True
    m.merge_threshold = 1e-4
    s = ob.modifiers.get('Subdiv') or ob.modifiers.new('Subdiv', 'SUBSURF')
    s.levels = levels
    s.render_levels = max(levels, 3)
    s.quality = 3
    s.use_limit_surface = True
    s.use_creases = True
    return ob


# ---------------------------------------------------------------- editing

def edit_cage(fn, name=CAGE):
    """Run fn(bm) on the cage in object-mode bmesh, weld the mirror seam, write back. Re-fetches
    the object by name, so it is safe after undo, mode switches or a reconnect."""
    ob = bpy.data.objects[name]
    if ob.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
    fn(bm)
    for v in bm.verts:
        if abs(v.co.x) < SEAM_TOL:
            v.co.x = 0.0
        elif v.co.x < 0:
            v.co.x = 0.0                            # clipping: nothing crosses the mirror plane
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob


def verts(bm, rows=None, cols=None):
    """Cage vertices by row / column (ranges, lists or ints); end caps are rows -1 and -2."""
    lr = bm.verts.layers.int.get('cage_row')
    lc = bm.verts.layers.int.get('cage_col')
    as_set = lambda x: None if x is None else ({x} if isinstance(x, int) else set(x))   # noqa: E731
    R, Cs = as_set(rows), as_set(cols)
    return [v for v in bm.verts if (R is None or v[lr] in R) and (Cs is None or v[lc] in Cs)]


def move(bm, rows=None, cols=None, dx=0.0, dy=0.0, dz=0.0, falloff_rows=0, scale_x=None):
    """Move a block of cage vertices; `falloff_rows` fades the move over that many neighbouring
    rows on each side (proportional editing by rows), `scale_x` scales their half-width."""
    lr = bm.verts.layers.int.get('cage_row')
    target = set(verts(bm, rows, cols))
    if not target:
        print(json.dumps({'move': 'no vertices matched', 'rows': str(rows), 'cols': str(cols)}))
        return
    rows_set = {v[lr] for v in target}
    lo, hi = min(rows_set), max(rows_set)
    for v in verts(bm, None, cols):
        r = v[lr]
        if v in target:
            w = 1.0
        elif falloff_rows and r >= 0 and (lo - falloff_rows <= r < lo or hi < r <= hi + falloff_rows):
            d = (lo - r) if r < lo else (r - hi)
            w = 0.5 * (1 + math.cos(math.pi * d / (falloff_rows + 1)))
        else:
            continue
        v.co += Vector((dx, dy, dz)) * w
        if scale_x is not None:
            v.co.x *= 1 + (scale_x - 1) * w


def loop_verts(e0):
    """Ordered vertices of the edge loop through edge e0; stops at poles, borders or when closed."""
    def walk(e, v):
        chain = []
        while len(v.link_edges) == 4:
            fs = set(e.link_faces)
            nxt = [x for x in v.link_edges if x is not e and not (set(x.link_faces) & fs)]
            if len(nxt) != 1:
                break
            e = nxt[0]
            v = e.other_vert(v)
            if v in chain or v in e0.verts:
                break
            chain.append(v)
        return chain
    a, b = e0.verts
    return list(reversed(walk(e0, a))) + [a, b] + walk(e0, b)


def space(vs):
    """LoopTools Space: equal arc-length spacing along an open loop, endpoints fixed."""
    pts = [v.co.copy() for v in vs]
    seg = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    acc = [0.0]
    for s_ in seg:
        acc.append(acc[-1] + s_)
    for k in range(1, len(pts) - 1):
        t = acc[-1] * k / (len(pts) - 1)
        i = max(j for j in range(len(seg)) if acc[j] <= t)
        vs[k].co = pts[i].lerp(pts[i + 1], (t - acc[i]) / seg[i] if seg[i] else 0.0)


def relax(vs, iterations=3, factor=0.5):
    """LoopTools Relax: pull each vertex toward the midpoint of its loop neighbours (ends fixed)."""
    for _ in range(iterations):
        pts = [v.co.copy() for v in vs]
        for i in range(1, len(vs) - 1):
            vs[i].co = pts[i] + factor * ((pts[i - 1] + pts[i + 1]) / 2 - pts[i])


def circle(vs):
    """LoopTools Circle: fit a plane and radius to a closed ring, place vertices at equal angles."""
    c = sum((v.co for v in vs), Vector()) / len(vs)
    n = Vector()
    for i in range(len(vs)):
        n += (vs[i].co - c).cross(vs[(i + 1) % len(vs)].co - c)
    n.normalize()
    r = sum((v.co - c).length for v in vs) / len(vs)
    u = (vs[0].co - c)
    u = (u - n * u.dot(n)).normalized()
    w = n.cross(u)
    for k, v in enumerate(vs):
        t = 2 * math.pi * k / len(vs)
        v.co = c + (u * math.cos(t) + w * math.sin(t)) * r


def row_loop(bm, row):
    """The cage vertices of one station row, ordered bottom centre -> top centre."""
    lc = bm.verts.layers.int.get('cage_col')
    return sorted(verts(bm, rows=row), key=lambda v: v[lc])


def col_loop(bm, col):
    """The vertices of one column along the car, nose -> tail (a design line, e.g. the shoulder)."""
    lr = bm.verts.layers.int.get('cage_row')
    return sorted((v for v in verts(bm, cols=col) if v[lr] >= 0), key=lambda v: v[lr])


def ring_edges(e0):
    """The edge ring through e0 (edges a loop cut across e0 would split): walks opposite edges of
    quads in both directions, stopping at a border, a non-quad or when the ring closes."""
    ring, seen = [e0], {e0}
    for l0 in e0.link_loops:
        loop = l0
        while len(loop.face.verts) == 4:
            e = loop.link_loop_next.link_loop_next.edge
            if e in seen:
                break
            seen.add(e)
            ring.append(e)
            nxt = [x for x in e.link_loops if x.face is not loop.face]
            if not nxt:
                break
            loop = nxt[0]
    return ring


def _relabel_cols(bm):
    """Recount cage_col along every station row: walk the row's own edges from its bottom-centre
    vertex (x = 0, lowest) to the top centre."""
    lr = bm.verts.layers.int['cage_row']
    lc = bm.verts.layers.int['cage_col']
    rows = {}
    for v in bm.verts:
        if v[lr] >= 0:
            rows.setdefault(v[lr], []).append(v)
    for r, vs in rows.items():
        members = set(vs)
        seam = [v for v in vs if abs(v.co.x) < SEAM_TOL]
        v = min(seam or vs, key=lambda u: u.co.z)
        chain, prev = [v], None
        while True:
            nxt = [e.other_vert(v) for e in v.link_edges if e.other_vert(v) in members and e.other_vert(v) is not prev
                   and e.other_vert(v) not in chain]
            if not nxt:
                break
            prev, v = v, nxt[0]
            chain.append(v)
        for i, u in enumerate(chain):
            u[lc] = i


def insert_loop(kind, index, name=CAGE, keep_shape=True):
    """Add one loop the way a loop cut does, keeping the surface where it was.
    kind='row': a new station between rows index and index+1 (runs round the half section);
    kind='col': a new column between columns index and index+1 in every row (runs along the car,
    through the end caps; a column in the lower or upper band may come out through the cap at the
    other band, which is correct topology and also adds a column there).
    With keep_shape the cage is then refitted so the subdivided surface stays on the old one: a
    new loop pulls the surface toward the cage, and a dent from adding a loop is exactly what the
    pros warn about. Rows and columns are renumbered. Returns the new vertex count."""
    ob = bpy.data.objects[name]
    dg = bpy.context.evaluated_depsgraph_get()
    old = BVHTree.FromObject(ob, dg) if keep_shape else None
    added = []

    def fn(bm):
        lr = bm.verts.layers.int['cage_row']
        lc = bm.verts.layers.int['cage_col']
        if kind == 'row':
            e0 = next(e for e in bm.edges if {e.verts[0][lr], e.verts[1][lr]} == {index, index + 1}
                      and e.verts[0][lc] == e.verts[1][lc])
        else:
            e0 = next(e for e in bm.edges if e.verts[0][lr] == e.verts[1][lr] >= 0
                      and {e.verts[0][lc], e.verts[1][lc]} == {index, index + 1})
        ring = ring_edges(e0)
        # new vertices come back from the op (they inherit interpolated attributes, so a tag
        # layer cannot tell them apart); they sit on the split ring edges
        ret = bmesh.ops.subdivide_edges(bm, edges=ring, cuts=1, use_grid_fill=False, quad_corner_type='STRAIGHT_CUT')
        new = list({g for g in ret['geom_split'] + ret['geom_inner'] if isinstance(g, bmesh.types.BMVert)})
        fresh = set(new)
        if kind == 'row':
            # the ring runs bottom seam -> top seam between the two stations, never into a cap
            for v in bm.verts:
                if v not in fresh and v[lr] > index:
                    v[lr] += 1
            for v in new:
                v[lr] = index + 1
        else:
            for v in new:
                side = [e.other_vert(v) for e in v.link_edges if e.other_vert(v) not in fresh]
                rs = {u[lr] for u in side}
                v[lr] = next(iter(rs)) if len(rs) == 1 else min(rs)
        _relabel_cols(bm)
        added.extend(new)

    edit_cage(fn, name)
    if keep_shape and old is not None:
        me = ob.data
        targets = {}
        # a cage vertex is not ON its surface: target the old surface point nearest each vertex's
        # current surface point (the fit then drives the new surface back onto the old one)
        dg = bpy.context.evaluated_depsgraph_get()
        cur = BVHTree.FromObject(ob, dg)
        for v in me.vertices:
            lp, _, _, _ = cur.find_nearest(v.co)
            if lp is not None:
                q, _, _, _ = old.find_nearest(lp)
                if q is not None:
                    targets[v.index] = q
        fit_surface(name, targets=targets, iters=8)
    print(json.dumps({'insert_loop': kind, 'after': index, 'newVerts': len(added)}))
    return len(added)


# ---------------------------------------------------------------- measuring

def metrics(run=None, name=CAGE, flat_deg=5.0):
    """Print (and return) the facts every call should end with: counts, poles on curved areas,
    non-quads, mirror-seam drift, non-manifold edges, subdivided dimensions vs the spec, and the
    fairness of the subdivided surface (normal deviation between neighbours, degrees)."""
    ob = bpy.data.objects[name]
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.normal_update()
    nonquad = sum(len(f.verts) != 4 for f in bm.faces)
    poles = curved = 0
    for v in bm.verts:
        border = any(e.is_boundary for e in v.link_edges)
        if border and abs(v.co.x) < 1e-6:
            continue                                # seam vertices become valence 4 after Mirror
        val = len(v.link_edges)
        if (not border and val != 4) or (border and val not in (2, 3)):
            poles += 1
            ns = [f.normal for f in v.link_faces]
            if max((a.angle(b, 0.0) for a in ns for b in ns), default=0) > math.radians(flat_deg):
                curved += 1
    drift = sum(1 for v in bm.verts if 0 < abs(v.co.x) < 0.005)
    nonmanifold = sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary)
    ratio, at = 1.0, None
    lr = bm.verts.layers.int.get('cage_row')
    lc = bm.verts.layers.int.get('cage_col')
    for v in bm.verts:
        ls = [e.calc_length() for e in v.link_edges]
        if len(ls) >= 2 and min(ls) > 1e-6 and max(ls) / min(ls) > ratio:
            ratio = max(ls) / min(ls)
            at = [v[lr], v[lc]] if lr and lc else v.index
    bm.free()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    em = ev.to_mesh()
    pts = [ev.matrix_world @ v.co for v in em.vertices]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    # fairness on the subdivided surface
    bm2 = bmesh.new()
    bm2.from_mesh(em)
    bm2.normal_update()
    dev = []
    for v in bm2.verts:
        if v.is_boundary or len(v.link_edges) < 3:
            continue
        mean = Vector()
        for e in v.link_edges:
            mean += e.other_vert(v).normal
        if mean.length > 1e-9:
            a = math.degrees(v.normal.angle(mean.normalized(), 0.0))
            if a < 25:
                dev.append(a)
    bm2.free()
    ev.to_mesh_clear()
    dev.sort()
    out = {'obj': name, 'verts': len(ob.data.vertices), 'faces': len(ob.data.polygons), 'nonquad': nonquad,
           'poles': poles, 'curvedPoles': curved, 'seamDrift': drift, 'nonManifold': nonmanifold,
           'maxEdgeRatio': round(ratio, 2), 'maxEdgeRatioAt': at, 'dims_m': [round(d, 4) for d in hi - lo],
           'fairness': {'p50Deg': round(dev[len(dev) // 2], 3), 'p95Deg': round(dev[int(len(dev) * 0.95)], 3)}
           if dev else None}
    if run:
        spec = json.load(open(os.path.join(run, 'spec.json')))
        # overall width and length of the shell, and its top against the overall height (the
        # shell sits on the ground-referenced frame, so hi.z is the roof height above the ground)
        out['specError_mm'] = {'width': round((hi.x - lo.x - spec['width']) * 1000, 1),
                               'length': round((hi.y - lo.y - spec['length']) * 1000, 1),
                               'top': round((hi.z - spec['height']) * 1000, 1)}
    print(json.dumps(out))
    return out


# ---------------------------------------------------------------- state and checks

def checkpoint(run, tag):
    """Save a copy of the .blend and the cage JSON under runs/<id>/cage/history/ (undo is not
    reliable over MCP; this is the way back)."""
    d = os.path.join(run, 'cage', 'history')
    os.makedirs(d, exist_ok=True)
    n = len([f for f in os.listdir(d) if f.endswith('.json')])
    stem = os.path.join(d, f'{n:03d}-{tag}')
    export_cage(run, path=stem + '.json')
    # scene data only: a regular save also stores UI state such as the last browsed directory
    bpy.data.libraries.write(stem + '.blend', {bpy.context.scene}, path_remap='RELATIVE_ALL', fake_user=True,
                             compress=True)
    print(json.dumps({'checkpoint': stem}))
    return stem


def restore(run, which=-1, name=CAGE):
    """The undo that works over MCP: reload the cage from a checkpoint (index into the history,
    e.g. -1 = latest, or a tag substring) and keep the modifiers. Returns the checkpoint used."""
    d = os.path.join(run, 'cage', 'history')
    snaps = sorted(f for f in os.listdir(d) if f.endswith('.json'))
    pick = snaps[which] if isinstance(which, int) else next(f for f in reversed(snaps) if which in f)
    ob = bpy.data.objects.get(name)
    levels = ob.modifiers['Subdiv'].levels if ob and ob.modifiers.get('Subdiv') else 2
    load_cage(run, name=name, path=os.path.join(d, pick), levels=levels)
    print(json.dumps({'restored': pick}))
    return pick


def export_cage(run, name=CAGE, path=None):
    """Write the half cage (verts, quads, creases, row/col tags) to runs/<id>/cage.json: the body
    source for build.py when curves.json has "surface": "mesh"."""
    ob = bpy.data.objects[name]
    me = ob.data
    cr = me.attributes.get('crease_edge')
    creases = []
    if cr:
        creases = [[e.vertices[0], e.vertices[1], round(cr.data[e.index].value, 3)]
                   for e in me.edges if cr.data[e.index].value > 0]
    rows = me.attributes.get('cage_row')
    cols = me.attributes.get('cage_col')
    data = {'frame': '+X left, -Y forward, +Z up, metres; half cage x >= 0, mirrored on X',
            'verts': [[round(c, 6) for c in v.co] for v in me.vertices],
            'faces': [list(p.vertices) for p in me.polygons],
            'creases': creases,
            'rows': [r.value for r in rows.data] if rows else None,
            'cols': [c.value for c in cols.data] if cols else None}
    path = path or os.path.join(run, 'cage.json')
    json.dump(data, open(path, 'w'))
    return path


def load_cage(run, name=CAGE, path=None, levels=2):
    """Bring cage.json back into the scene as CAR_cage (a fresh session continues the work)."""
    data = json.load(open(path or os.path.join(run, 'cage.json')))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in data['verts']], [], [tuple(f) for f in data['faces']])
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    for key in ('rows', 'cols'):
        if data.get(key):
            a = me.attributes.new('cage_row' if key == 'rows' else 'cage_col', 'INT', 'POINT')
            a.data.foreach_set('value', data[key])
    cr = me.edge_creases_ensure()
    lookup = {tuple(sorted(e.vertices)): e.index for e in me.edges}
    vals = [0.0] * len(me.edges)
    for a, b, w in data.get('creases', []):
        i = lookup.get(tuple(sorted((a, b))))
        if i is not None:
            vals[i] = w
    cr.data.foreach_set('value', vals)
    old = bpy.data.objects.get(name)
    if old is not None:
        om = old.data
        old.data = me
        if om.users == 0:
            bpy.data.meshes.remove(om)
        ob = old
    else:
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
    return ensure_modifiers(ob, levels)


def render_checks(run, tag, views=('side', 'front', 'top', 'front34', 'rear34'),
                  matcaps=('check_reflection_horizontal.exr', 'check_reflection_vertical.exr'),
                  stripes=True, res=(1280, 720), name=CAGE):
    """Reflection checks from the fixed cameras, headless-safe: Workbench MatCap renders (the stripe
    MatCaps follow the camera) plus an EEVEE render in a world-locked stripe environment on a chrome
    cage (the highlight is fixed in the world, so it sweeps the panels as views change). Only the
    cage is shown. Writes runs/<id>/cage/checks/<tag>_<view>_<kind>.png and returns the paths."""
    sc = bpy.context.scene
    out_dir = os.path.join(run, 'cage', 'checks')
    os.makedirs(out_dir, exist_ok=True)
    if not bpy.data.objects.get('CAM_side'):
        cameras(run=run)
    hidden = []
    for ob in sc.objects:
        if ob.type == 'MESH' and ob.name != name and not ob.hide_render:
            ob.hide_render = True
            hidden.append(ob)
    engine, world = sc.render.engine, sc.world
    paths = []
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    try:
        sc.render.engine = 'BLENDER_WORKBENCH'
        sh = sc.display.shading
        sh.light = 'MATCAP'
        sh.color_type = 'SINGLE'
        sh.show_xray = False
        for mc in matcaps:
            sh.studio_light = mc
            for v in views:
                sc.camera = bpy.data.objects['CAM_' + v]
                p = os.path.join(out_dir, f'{tag}_{v}_{mc.split(".")[0]}.png')
                sc.render.filepath = p
                bpy.ops.render.render(write_still=True)
                paths.append(p)
        if stripes:
            ob = bpy.data.objects[name]
            chrome = bpy.data.materials.get('_check_chrome') or bpy.data.materials.new('_check_chrome')
            chrome.use_nodes = True
            pb = next(n for n in chrome.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
            pb.inputs['Metallic'].default_value = 1.0
            pb.inputs['Roughness'].default_value = 0.02
            pb.inputs['Base Color'].default_value = (0.95, 0.95, 0.95, 1)
            saved = list(ob.data.materials)
            ob.data.materials.clear()
            ob.data.materials.append(chrome)
            sc.world = stripe_world()
            try:
                sc.render.engine = 'BLENDER_EEVEE'
            except TypeError:
                sc.render.engine = 'CYCLES'
            for v in views:
                sc.camera = bpy.data.objects['CAM_' + v]
                p = os.path.join(out_dir, f'{tag}_{v}_stripes.png')
                sc.render.filepath = p
                bpy.ops.render.render(write_still=True)
                paths.append(p)
            ob.data.materials.clear()
            for m in saved:
                ob.data.materials.append(m)
    finally:
        sc.render.engine, sc.world = engine, world
        for o in hidden:
            o.hide_render = False
    print(json.dumps({'renders': paths}))
    return paths


def photo_overlays(run, refs, tag, alpha=0.55, matcap='check_rim_light.exr', name=CAGE, max_px=1600):
    """The subdivided cage rendered through each solved photo camera and blended over that photo:
    the perspective check that orthographic views cannot give (where the shoulder, belt and arches
    sit on the real car). Photos are private, so the sheets go beside the refs, in
    <refs>/../critique/cage/<tag>_<view>.png, never into the run. Returns the paths."""
    import numpy as np
    views = {k: v for k, v in json.load(open(os.path.join(refs, 'views.json'))).items() if not v.get('skip')}
    out_dir = os.path.join(os.path.dirname(os.path.abspath(refs).rstrip('/')), 'critique', 'cage')
    os.makedirs(out_dir, exist_ok=True)
    photo_cameras(run)
    sc = bpy.context.scene
    keep = (sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage,
            sc.render.pixel_aspect_y, sc.render.film_transparent, sc.camera)
    hidden = [o for o in sc.objects if o.type == 'MESH' and o.name != name and not o.hide_render]
    for o in hidden:
        o.hide_render = True
    paths = []
    try:
        sc.render.engine = 'BLENDER_WORKBENCH'
        sc.render.film_transparent = True
        sh = sc.display.shading
        sh.light, sh.color_type, sh.show_xray, sh.studio_light = 'MATCAP', 'SINGLE', False, matcap
        for cam in sorted((o for o in sc.objects if o.name.startswith('CAM_photo_')), key=lambda o: o.name):
            v = cam.name[len('CAM_photo_'):]
            if v not in views or not os.path.exists(os.path.join(refs, views[v]['image'])):
                continue
            w, h = cam['photoSize']
            f = min(1.0, max_px / max(w, h))
            sc.camera = cam
            sc.render.resolution_x, sc.render.resolution_y = int(w), int(h)
            sc.render.resolution_percentage = int(round(100 * f))
            sc.render.pixel_aspect_y = cam.get('pixelAspectY', 1.0)
            tmp = os.path.join(out_dir, f'_{tag}_{v}_cage.png')
            sc.render.filepath = tmp
            bpy.ops.render.render(write_still=True)
            ren = bpy.data.images.load(tmp)
            pho = bpy.data.images.load(os.path.join(refs, views[v]['image']))
            rw, rh = ren.size
            pho.scale(rw, rh)
            R = np.empty(rw * rh * 4, np.float32)
            P = np.empty(rw * rh * 4, np.float32)
            ren.pixels.foreach_get(R)
            pho.pixels.foreach_get(P)
            R, P = R.reshape(rh, rw, 4), P.reshape(rh, rw, 4)
            # cage tinted cyan (MatCap shading kept as brightness), silhouette outlined magenta
            a = R[..., 3:4] * alpha
            tint = R[..., :3].mean(axis=2, keepdims=True) * np.array([0.35, 0.9, 1.0], np.float32)
            P[..., :3] = P[..., :3] * (1 - a) + tint * a
            m = R[..., 3] > 0.5
            edge = np.zeros_like(m)
            for ax in (0, 1):
                d = m != np.roll(m, 1, axis=ax)
                edge |= d | np.roll(d, -1, axis=ax)
            P[edge, :3] = (1.0, 0.1, 0.9)
            P[..., 3] = 1.0
            outp = os.path.join(out_dir, f'{tag}_{v}.png')
            img = bpy.data.images.new(f'_ov_{v}', rw, rh, alpha=False)
            img.pixels.foreach_set(P.ravel())
            img.filepath_raw = outp
            img.file_format = 'PNG'
            img.save()
            for im in (ren, pho, img):
                bpy.data.images.remove(im)
            os.remove(tmp)
            paths.append(outp)
    finally:
        (sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage,
         sc.render.pixel_aspect_y, sc.render.film_transparent, sc.camera) = keep
        for o in hidden:
            o.hide_render = False
    print(json.dumps({'overlays': paths}))
    return paths


def stripe_world(bands=24.0, rot_z_deg=0.0, name='CHECK_stripes'):
    """Horizontal black/white bands fixed in the world (rotate rot_z_deg to sweep highlights)."""
    w = bpy.data.worlds.get(name) or bpy.data.worlds.new(name)
    w.use_nodes = True
    n, l = w.node_tree.nodes, w.node_tree.links
    n.clear()
    tc = n.new('ShaderNodeTexCoord')
    mp = n.new('ShaderNodeMapping')
    mp.inputs['Rotation'].default_value = (0.0, 0.0, math.radians(rot_z_deg))
    sep = n.new('ShaderNodeSeparateXYZ')
    mul = n.new('ShaderNodeMath')
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = bands
    sin = n.new('ShaderNodeMath')
    sin.operation = 'SINE'
    gt = n.new('ShaderNodeMath')
    gt.operation = 'GREATER_THAN'
    gt.inputs[1].default_value = 0.0
    bg = n.new('ShaderNodeBackground')
    out = n.new('ShaderNodeOutputWorld')
    l.new(tc.outputs['Generated'], mp.inputs['Vector'])
    l.new(mp.outputs['Vector'], sep.inputs[0])
    l.new(sep.outputs['Z'], mul.inputs[0])
    l.new(mul.outputs[0], sin.inputs[0])
    l.new(sin.outputs[0], gt.inputs[0])
    l.new(gt.outputs[0], bg.inputs['Color'])
    l.new(bg.outputs[0], out.inputs['Surface'])
    return w


def aim_viewport(view='side', matcap='check_reflection_horizontal.exr', wire=True, run=None):
    """GUI only (MCP): point the first 3D view at a fixed check view and set MatCap shading, in the
    same call as get_viewport_screenshot, so every screenshot is comparable. Headless sessions have
    no viewport: use render_checks instead."""
    screen = bpy.context.screen
    if screen is None:
        print(json.dumps({'aim_viewport': 'no UI (background mode): use render_checks'}))
        return False
    area = next((a for a in screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        return False
    cam = bpy.data.objects.get('CAM_' + view)
    sp = area.spaces.active
    r3 = sp.region_3d
    if cam is not None:
        r3.view_perspective = 'ORTHO' if cam.data.type == 'ORTHO' else 'PERSP'
        r3.view_rotation = cam.matrix_world.to_quaternion()
        fwd = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        dist = 6.0 if cam.data.type == 'ORTHO' else (cam.location.length if cam.location.length > 1 else 6.0)
        r3.view_location = cam.location + fwd * dist
        r3.view_distance = dist if cam.data.type != 'ORTHO' else cam.data.ortho_scale * 0.9
    sp.shading.type = 'SOLID'
    sp.shading.light = 'MATCAP'
    sp.shading.studio_light = matcap
    sp.overlay.show_wireframes = wire
    area.tag_redraw()
    return True
