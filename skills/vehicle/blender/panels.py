"""zstack/vehicle - turn the lofted solid into a thin-walled, panelled body.

Input: the closed BodyShell from body.py and the run's parts.json. Steps:

  1. shell()      solidify inward to a sheet-metal wall (inner face gets the Interior material)
  2. openings()   cut windows / windscreen / hatch glass; each removed piece of wall becomes
                  the glass pane, so glass sits exactly flush in its aperture
  3. recesses()   lamps and intakes: cut the aperture, keep the removed wall as the lens
                  (lamps) or drop it (intakes), and build a housing that recedes into the body
  4. cut_panels() shut lines: a gap-wide ribbon cutter along each panel outline separates
                  doors / hood / engine cover as their own islands, with a real gap

Every polygon is 2D in a named plane ('YZ' side, 'XZ' front/rear, 'XY' top) and is
extruded over `range` along the third axis. `mirror: true` also builds the car's other
side (+X is LEFT; names ending in _L / FL / RL become _R / FR / RR).
"""
import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

import common as C

SIDE_SWAP = (('_FL', '_FR'), ('_RL', '_RR'), ('_L', '_R'))


def mirrored(part):
    """Right-side copy of a left-side part definition."""
    q = dict(part)
    name = part['name']
    for a, b in SIDE_SWAP:
        if name.endswith(a) or (a + '_') in name:
            name = name.replace(a, b)
            break
    q['name'] = name
    if 'parent' in part:
        p = part['parent']
        for a, b in SIDE_SWAP:
            if p.endswith(a):
                p = p[: -len(a)] + b
                break
        q['parent'] = p
    if part['plane'] == 'YZ':
        lo, hi = part['range']
        q['range'] = [-hi, -lo]
    elif not part.get('auto'):
        q['poly'] = [(-u, v) for u, v in part['poly']]
    if part.get('auto'):
        q.pop('poly', None)
    q['mirror'] = False
    q['side'] = -1
    return q


def expand(parts):
    out = []
    for p in parts:
        p = dict(p)
        p.setdefault('side', 1 if p.get('mirror') else 0)
        out.append(p)
        if p.get('mirror'):
            out.append(mirrored(p))
    return out


AUTO = None     # body.BodySpec of the current run; set by use_body()


def use_body(bodyspec):
    global AUTO
    AUTO = bodyspec


def _auto(part):
    """Outlines derived from the body's own feature curves, so cuts follow the real lines:
    greenhouse  side glass between belt+insetBottom and rail-insetTop, over y
    windscreen  top-view glass inside the A-pillars (rail width - inset), over y
    belt        a door/panel: sill z up to belt+topOffset, over y
    """
    bs = AUTO
    kind = part['auto']
    y0, y1 = part['y']
    n = 14
    ys = [y0 + (y1 - y0) * i / n for i in range(n + 1)]
    if kind == 'greenhouse':
        ib, it = part.get('inset', [0.015, 0.025])
        top = [(y, bs.keys(y)[6][1] - it) for y in ys]
        bot = [(y, bs.keys(y)[5][1] + ib) for y in ys]
        # keep a minimum height; where the greenhouse pinches out, the outline closes to a point
        pts = [(y, max(z, b + 0.004)) for (y, z), (_, b) in zip(top, bot)]
        return pts + list(reversed(bot))
    if kind == 'windscreen':
        inset = part.get('inset', 0.035)
        right = [(bs.keys(y)[6][0] - inset, y) for y in ys]
        return right + [(-x, y) for x, y in reversed(right)]
    if kind == 'belt':
        # Top edge: inside the side-window opening it rides above the belt (the cut passes through
        # glass that is already gone); ahead of the window it runs just BELOW the belt, on the steep
        # flank, because a cut band that grazes a near-flat surface never severs the wall.
        sill = part['sill']
        off = part.get('topOffset', 0.03)
        drop = part.get('frontDrop', -0.015)
        wy = part.get('windowFrom', y0)
        ramp = part.get('ramp', 0.03)
        yy = sorted(set(ys + [wy - ramp, wy]))
        top = []
        for y in yy:
            k = 0.0 if y <= wy - ramp else (1.0 if y >= wy else (y - (wy - ramp)) / ramp)
            top.append((y, bs.keys(y)[5][1] + drop + (off - drop) * k))
        return [(y0, sill)] + top + [(y1, sill)]
    raise ValueError('unknown auto outline ' + kind)


def poly_of(part):
    pts = _auto(part) if part.get('auto') else part['poly']
    r = part.get('round', 0.0)
    return C.rounded(pts, r) if r > 0 else [tuple(p) for p in pts]


def shell(body, mats, wall=0.004):
    """Solid loft -> two nested surfaces `wall` apart. Outer keeps slot 0 (Paint), inner slot 1."""
    me = body.data
    me.materials.clear()
    me.materials.append(mats['Paint'])
    me.materials.append(mats['Interior'])
    md = body.modifiers.new('wall', 'SOLIDIFY')
    md.thickness = wall
    md.offset = -1.0
    md.use_even_offset = False      # even offset explodes at the pinched end-cap centres
    md.material_offset = 1
    md.use_rim = True
    C.apply_modifiers(body)
    return body


def _cutter(part, gap=None):
    lo, hi = part['range']
    poly = poly_of(part)
    if gap:
        return C.ribbon(part['name'] + '_cut', poly, part['plane'], lo, hi, gap=gap)
    return C.prism(part['name'] + '_cut', poly, part['plane'], lo, hi)


def take(body, part, material=None):
    """Cut `part` out of `body`; return the removed piece of wall as its own object."""
    cut = _cutter(part)
    piece = C.duplicate(body, part['name'])
    C.boolean(piece, cut, 'INTERSECT', keep_cutter=True)
    C.boolean(body, cut, 'DIFFERENCE')
    if material is not None:
        C.set_material(piece, material)
    return piece if len(piece.data.polygons) else None


def openings(body, parts, mats):
    out = {}
    for part in expand(parts):
        glass = take(body, part, mats[part.get('glass', 'Glass')])
        if glass is None:
            print('opening cut nothing:', part['name'])
            continue
        glass['zstack'] = 'glass'
        glass['parentPanel'] = part.get('parent', '')
        out[glass.name] = glass
    return out


# ---------------------------------------------------------------- recesses (lamps, intakes)

def _layer(ob, inner):
    """Faces of one wall layer: inner (slot 1) or outer (slot 0)."""
    return [p for p in ob.data.polygons if (p.material_index == 1) == inner]


def housing(piece, part, mats, depth):
    """Open box behind an aperture: copy the aperture's inner wall and push it `depth` into the car."""
    bm = bmesh.new()
    bm.from_mesh(piece.data)
    keep = {p.index for p in _layer(piece, inner=True)}
    bm.faces.ensure_lookup_table()
    if not keep:       # boolean lost the slot split: fall back to faces facing the car centre
        keep = {f.index for f in bm.faces if f.normal.dot(-f.calc_center_median()) > 0}
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context='FACES')
    # push straight into the car along the cut axis. (The mean face normal of an aperture that wraps
    # a corner points diagonally; a deep duct pushed along it exits through the neighbouring panel.)
    lo, hi = part['range']
    if part['plane'] == 'YZ':
        n = Vector((-1.0, 0.0, 0.0)) if part.get('side', 0) >= 0 else Vector((1.0, 0.0, 0.0))
    elif part['plane'] == 'XZ':
        n = Vector((0.0, 1.0, 0.0)) if (lo + hi) / 2 < 0 else Vector((0.0, -1.0, 0.0))
    else:
        n = Vector((0.0, 0.0, -1.0))
    ext = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    moved = [g for g in ext['geom'] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=n * depth, verts=moved)
    # back plate slightly smaller so the walls taper like a moulded housing
    ctr = sum((v.co for v in moved), Vector()) / max(1, len(moved))
    for v in moved:
        v.co = ctr + (v.co - ctr) * part.get('taper', 0.9)
    if part.get('openBack'):     # walls only: see-through, so only for openings nothing looks through
        # (an open-backed intake punches a hole in the car's silhouette; prefer a deep closed duct
        #  with the fan inside it, see parts.json 'depth')
        back = {f for f in bm.faces if all(v in set(moved) for v in f.verts)}
        bmesh.ops.delete(bm, geom=list(back), context='FACES')
    bmesh.ops.reverse_faces(bm, faces=bm.faces)
    ob = C.mesh_object(part['name'] + '_Housing', bm, [mats[part.get('housing', 'Trim_Black')]], smooth=False)
    return ob, n


def _surface_hits(bvh, part, pts2d, inset=0.0):
    """Raycast 2D polygon points onto the wall along the extrude axis (from outside inward)."""
    lo, hi = part['range']
    plane = part['plane']
    a, b, c = C.AXES[plane]
    side = part.get('side', 0)
    out = []
    for u, v in pts2d:
        start = [0.0, 0.0, 0.0]
        start[a], start[b] = u, v
        d = [0.0, 0.0, 0.0]
        if plane == 'YZ':                       # side part: shoot from outside toward the centre
            start[c] = hi + 0.5 if side >= 0 else lo - 0.5
            d[c] = -1.0 if side >= 0 else 1.0
        elif plane == 'XZ':                     # front/rear part: shoot along Y toward the centre
            front = (lo + hi) / 2 < 0
            start[c] = lo - 0.5 if front else hi + 0.5
            d[c] = 1.0 if front else -1.0
        else:                                   # top part: shoot down
            start[c] = hi + 0.5
            d[c] = -1.0
        hit = bvh.ray_cast(Vector(start), Vector(d), 3.0)
        if hit[0] is not None:
            out.append((hit[0] + Vector(d) * inset, hit[1]))
    return out


def _inset_poly(poly, f):
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return [(cx + (x - cx) * f, cy + (y - cy) * f) for x, y in poly]


def lamp_insides(part, bvh, mats, coll):
    """Emitters and reflectors behind a lens: a light-guide strip following the lens outline,
    and chrome reflector bowls with emissive cores (projector units)."""
    objs = []
    poly = poly_of(part)
    emit = mats[part.get('emissive', 'Lamp_Head')]
    for k, f in enumerate(part.get('strips', [0.78])):
        pts = [p for p, _ in _surface_hits(bvh, part, _inset_poly(poly, f), inset=part.get('stripDepth', 0.018))]
        if len(pts) > 3:
            t = C.tube(f"{part['name']}_Strip{k}", pts, part.get('stripRadius', 0.0045), 8, coll, closed=True)
            C.set_material(t, emit)
            objs.append(t)
    units = part.get('units', 0)
    if units:
        us = [p[0] for p in poly]
        vs = [p[1] for p in poly]
        cu, cv = sum(us) / len(us), sum(vs) / len(vs)
        span = part.get('unitSpan', 0.5)
        umin, umax = min(us), max(us)
        centres = [(cu + (umin + (umax - umin) * (0.5 + span * (i / max(1, units - 1) - 0.5)) - cu), cv)
                   for i in range(units)]
        r = part.get('unitRadius', 0.028)
        for i, (hit, nrm) in enumerate(_surface_hits(bvh, part, centres, inset=0.0)):
            back = hit - nrm * (r * 1.35)
            bowl = C.revolve(f"{part['name']}_Reflector{i}", [(0.004, 0.0), (r * 0.55, r * 0.25), (r * 0.9, r * 0.75), (r, r * 1.05)],
                             segments=32, axis='Z', coll=coll)
            C.set_material(bowl, mats['Reflector'])
            core = C.revolve(f"{part['name']}_Core{i}", [(0.0, r * 0.62), (r * 0.42, r * 0.62), (r * 0.46, r * 0.5)],
                             segments=24, axis='Z', coll=coll)
            C.set_material(core, emit)
            q = Vector((0, 0, 1)).rotation_difference(nrm)
            for o in (bowl, core):
                o.rotation_mode = 'QUATERNION'
                o.rotation_quaternion = q
                o.location = back
                C.apply_transform(o)
            objs += [bowl, core]
    return objs


def grille(part, bvh, mats, coll):
    """Fins or a lattice spanning an intake, set back behind the surface."""
    kind = part.get('grille')
    if not kind:
        return []
    poly = poly_of(part)
    plane = part['plane']
    a, b, c = C.AXES[plane]
    pitch = part.get('pitch', 0.028)
    setback = part.get('grilleDepth', 0.035)
    vs = [p[1] for p in poly]
    bars = []
    angles = {'fins': [0.0], 'lattice': [0.6, -0.6]}.get(kind, [0.0])
    import math
    for ang in angles:
        ca, sa = math.cos(ang), math.sin(ang)
        # rotate polygon so the bars run along the rotated u axis
        rp = [(u * ca + v * sa, -u * sa + v * ca) for u, v in poly]
        rv = [p[1] for p in rp]
        w = min(rv) + pitch / 2
        while w < max(rv):
            xs = []
            for i in range(len(rp)):
                (u1, v1), (u2, v2) = rp[i], rp[(i + 1) % len(rp)]
                if (v1 > w) != (v2 > w):
                    xs.append(u1 + (u2 - u1) * (w - v1) / (v2 - v1))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                seg = [(xs[i] + 0.004, w), (xs[i + 1] - 0.004, w)]
                pts2 = [(u * ca - v * sa, u * sa + v * ca) for u, v in seg]
                mid = ((pts2[0][0] + pts2[1][0]) / 2, (pts2[0][1] + pts2[1][1]) / 2)
                hits = _surface_hits(bvh, part, [pts2[0], mid, pts2[1]], inset=setback)
                if len(hits) == 3:
                    bars.append([h for h, _ in hits])
            w += pitch
    if not bars:
        return []
    bm = bmesh.new()
    t = part.get('barThickness', 0.004)
    for pts in bars:
        d = (pts[-1] - pts[0]).normalized()
        nrm = Vector((0, 0, 0))
        nrm[c] = 1.0
        up = d.cross(nrm).normalized() * t
        deep = nrm * part.get('barDepth', 0.02)
        for p, q in zip(pts, pts[1:]):
            vs_ = [bm.verts.new(x) for x in (p - up, q - up, q + up, p + up)]
            vb_ = [bm.verts.new(x.co + deep) for x in vs_]
            bm.faces.new(vs_)
            bm.faces.new(list(reversed(vb_)))
            for i in range(4):
                j = (i + 1) % 4
                bm.faces.new((vs_[i], vb_[i], vb_[j], vs_[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = C.mesh_object(part['name'] + '_Grille', bm, [mats[part.get('grilleMaterial', 'Trim_Satin')]], coll, smooth=False)
    return [ob]


def recesses(body, parts, mats, coll=None):
    """Lamps and intakes. Returns {name: [objects]}; objects carry 'zstack' role tags."""
    src = C.duplicate(body, '_bvh_src')
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(src, dg)
    out = {}
    for part in expand(parts):
        piece = take(body, part)
        if piece is None:
            print('recess cut nothing:', part['name'])
            continue
        objs = []
        hs, _ = housing(piece, part, mats, part.get('depth', 0.06))
        hs['zstack'] = 'housing'
        objs.append(hs)
        if part.get('lens'):
            C.set_material(piece, mats[part['lens']])
            piece.name = part['name']
            piece['zstack'] = 'lens'
            objs.append(piece)
            objs += lamp_insides(part, bvh, mats, coll)
        else:
            bpy.data.objects.remove(piece, do_unlink=True)
        objs += grille(part, bvh, mats, coll)
        for o in objs:
            o['recess'] = part['name']
        out[part['name']] = objs
    bpy.data.objects.remove(src, do_unlink=True)
    return out


# ---------------------------------------------------------------- shut lines

def _inside(part, co):
    a, b, c = C.AXES[part['plane']]
    lo, hi = part['range']
    return lo - 0.05 <= co[c] <= hi + 0.05 and C.point_in_poly((co[a], co[b]), poly_of(part))


def cut_panels(body, parts, gap=0.004, sliver=60):
    """Ribbon-cut each panel free. Returns (body, {panel name: skin object})."""
    exp = expand(parts)
    for part in exp:
        C.boolean(body, _cutter(part, gap=gap), 'DIFFERENCE')
    islands = C.split_islands(body)
    main, rest = islands[0], islands[1:]
    main.name = 'Body'
    main.data.name = 'Body'
    skins, strays = {}, []
    for isl in rest:
        ctr = C.world_centroid(isl)
        owner = next((p for p in exp if _inside(p, ctr)), None)
        if owner is None or len(isl.data.polygons) < sliver:
            strays.append(isl)
            continue
        if owner['name'] in skins:          # a panel cut into several islands: merge
            _join(skins[owner['name']], isl)
        else:
            isl.name = owner['name'] + '_Skin'
            isl.data.name = isl.name
            skins[owner['name']] = isl
    for s in strays:
        _join(main, s)
    missing = [p['name'] for p in exp if p['name'] not in skins]
    if missing:
        print('WARNING: panels that did not separate (outline not closed on the wall?):', missing)
    main['panelsMissing'] = missing
    return main, skins


def _join(dst, src):
    bm = bmesh.new()
    bm.from_mesh(dst.data)
    tmp = src.data.copy()
    tmp.transform(dst.matrix_world.inverted() @ src.matrix_world)
    # remap material slots by name
    slot = {m.name: i for i, m in enumerate(dst.data.materials) if m}
    for p in tmp.polygons:
        m = src.data.materials[p.material_index] if p.material_index < len(src.data.materials) else None
        p.material_index = slot.get(m.name, 0) if m else 0
    bm.from_mesh(tmp)
    bm.to_mesh(dst.data)
    bm.free()
    bpy.data.meshes.remove(tmp)
    me = src.data
    bpy.data.objects.remove(src, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)


# ---------------------------------------------------------------- wheel wells

def arches(body, defs, mats, coll=None):
    """Cut a wheel well per corner (a cylinder across the tyre's X band only) and line it."""
    import math
    out = []
    for d in defs:
        for side in ((1, -1) if d.get('mirror', True) else (1,)):
            x0, x1 = d['x']
            # the liner stops just inside the body's own flank at this axle
            skin = AUTO.keys(d['y'])[3][0] - 0.006 if AUTO else x1
            lo, hi = (x0, x1) if side > 0 else (-x1, -x0)
            llo, lhi = (x0, min(x1, skin)) if side > 0 else (-min(x1, skin), -x0)
            r, yc, zc = d['r'], d['y'], d['z']
            cyl = C.revolve('_arch', [(0.0, lo), (r, lo), (r, hi), (0.0, hi)], 64, 'X')
            cyl.location = (0, yc, zc)
            C.apply_transform(cyl)
            C.boolean(body, cyl, 'DIFFERENCE')
            # liner: the upper part of the well as an open half-cylinder, slightly outside the cut
            bm = bmesh.new()
            rl = r + 0.003
            a0 = math.asin(max(-1.0, min(1.0, (d.get('linerFloor', 0.12) - zc) / rl)))
            seg = 40
            rows = []
            for k in range(seg + 1):
                a = a0 + (math.pi - 2 * a0) * k / seg
                y, z = yc - rl * math.cos(a), zc + rl * math.sin(a)
                rows.append([bm.verts.new((x, y, z)) for x in (llo, lhi)])
            for ra, rb in zip(rows, rows[1:]):
                bm.faces.new((ra[0], ra[1], rb[1], rb[0]))
            name = d.get('name', 'Arch') + ('_L' if side > 0 else '_R')
            ob = C.mesh_object(name + '_Liner', bm, [mats['Underbody']], coll, smooth=True)
            ob['zstack'] = 'exterior'
            out.append(ob)
    return out


def leak_report(body, part, gap=0.004, limit=12):
    """Why did a panel not separate? Cut it alone on a copy and flood-fill from inside its outline;
    returns the places where the fill escapes the outline (where the shut line fails to sever the
    wall, usually because the cut band runs along a surface instead of across it)."""
    import bmesh
    from mathutils import Vector
    tmp = C.duplicate(body, '_leak')
    C.boolean(tmp, _cutter(part, gap=gap), 'DIFFERENCE')
    bm = bmesh.new()
    bm.from_mesh(tmp.data)
    poly = poly_of(part)
    a, b, c = C.AXES[part['plane']]
    lo, hi = part['range']
    inside = lambda co: lo <= co[c] <= hi and C.point_in_poly((co[a], co[b]), poly)
    ins = [f for f in bm.faces if f.material_index == 0 and inside(f.calc_center_median())]
    leaks = []
    if ins:
        cen = sum((Vector(p) for p in poly), Vector((0, 0))) / len(poly)
        start = min(ins, key=lambda f: (Vector((f.calc_center_median()[a], f.calc_center_median()[b])) - cen).length)
        seen, st = {start}, [start]
        while st and len(leaks) < 400:
            g = st.pop()
            for e in g.edges:
                for h in e.link_faces:
                    if h in seen:
                        continue
                    seen.add(h)
                    co = h.calc_center_median()
                    if inside(co):
                        st.append(h)
                    else:
                        leaks.append(tuple(round(x, 3) for x in co))
    bm.free()
    me = tmp.data
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.meshes.remove(me)
    return leaks[:limit]
