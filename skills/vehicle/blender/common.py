"""zstack/vehicle - shared Blender helpers: scene, materials, mesh ops without bpy.ops.

Everything here works in the GUI (Blender MCP execute_blender_code) and in background
mode (`blender -b -P`), because nothing depends on a 3D-view context.

Frame: +X = car's LEFT, -Y = forward, +Z = up, metres, ground at z = 0.
"""
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

# ---------------------------------------------------------------- scene


def reset_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves, bpy.data.cameras,
                 bpy.data.lights, bpy.data.images, bpy.data.node_groups, bpy.data.worlds):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)
    for c in list(bpy.context.scene.collection.children):
        bpy.data.collections.remove(c)


def collection(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def link(ob, coll=None):
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def empty(name, loc=(0, 0, 0), coll=None, size=0.1):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = size
    e.location = loc
    link(e, coll)
    update()        # matrix_world is stale until the view layer updates; parenting reads it
    return e


def parent_keep(child, parent):
    update()
    mw = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = mw


def update():
    bpy.context.view_layer.update()


# ---------------------------------------------------------------- materials

def _principled(mat):
    mat.use_nodes = True
    return next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')


def _set(p, **kw):
    names = {'color': 'Base Color', 'metallic': 'Metallic', 'rough': 'Roughness', 'ior': 'IOR',
             'transmission': 'Transmission Weight', 'coat': 'Coat Weight', 'coat_rough': 'Coat Roughness',
             'coat_ior': 'Coat IOR', 'emission': 'Emission Color', 'strength': 'Emission Strength',
             'aniso': 'Anisotropic', 'aniso_rot': 'Anisotropic Rotation', 'spec': 'Specular IOR Level',
             'sheen': 'Sheen Weight', 'alpha': 'Alpha'}
    for k, v in kw.items():
        sock = p.inputs.get(names[k])
        if sock is None:
            continue
        if isinstance(v, (tuple, list)) and len(v) == 3:
            v = (*v, 1.0)
        sock.default_value = v


def _flake_normal(mat, p, scale=2600.0, strength=0.06):
    """Metallic-flake sparkle under the clear coat: a fine Voronoi bump on the BASE normal only,
    so the coat stays mirror-smooth. Render-time only; glTF keeps the constant values."""
    nt = mat.node_tree
    tc = nt.nodes.new('ShaderNodeTexCoord')
    vor = nt.nodes.new('ShaderNodeTexVoronoi')
    vor.inputs['Scale'].default_value = scale
    bump = nt.nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = strength
    bump.inputs['Distance'].default_value = 0.0005
    nt.links.new(tc.outputs['Object'], vor.inputs['Vector'])
    nt.links.new(vor.outputs['Distance'], bump.inputs['Height'])
    nt.links.new(bump.outputs['Normal'], p.inputs['Normal'])


def _brushed(mat, p, scale=(1.0, 1.0, 180.0)):
    """Brushed metal: stretched noise drives roughness so highlights streak along the grain."""
    nt = mat.node_tree
    tc = nt.nodes.new('ShaderNodeTexCoord')
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = scale
    nz = nt.nodes.new('ShaderNodeTexNoise')
    nz.inputs['Scale'].default_value = 40.0
    nz.inputs['Detail'].default_value = 8.0
    ramp = nt.nodes.new('ShaderNodeMapRange')
    ramp.inputs['To Min'].default_value = 0.16
    ramp.inputs['To Max'].default_value = 0.32
    nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
    nt.links.new(mp.outputs['Vector'], nz.inputs['Vector'])
    nt.links.new(nz.outputs['Fac'], ramp.inputs['Value'])
    nt.links.new(ramp.outputs['Result'], p.inputs['Roughness'])


# name -> (principled settings, extra)
PALETTE = {
    # Metallic paint: metal flake base under a glossy clear coat. Reads as painted metal,
    # not plastic (metallic base) and not chrome (rough base + separate coat lobe).
    'Paint': ({'color': (0.30, 0.315, 0.325), 'metallic': 0.62, 'rough': 0.40, 'coat': 1.0,
               'coat_rough': 0.03, 'coat_ior': 1.5}, 'flake'),
    # Second body colour for two-tone schemes (a contrast roof or pillars; parts.json paintZones)
    'Paint_Accent': ({'color': (0.012, 0.012, 0.013), 'metallic': 0.35, 'rough': 0.32, 'coat': 1.0,
                      'coat_rough': 0.03, 'coat_ior': 1.5}, 'flake'),
    'Glass': ({'color': (0.52, 0.56, 0.57), 'metallic': 0.0, 'rough': 0.0, 'ior': 1.52,
               'transmission': 1.0}, 'glass'),                      # light green-grey automotive tint
    'GlassTint': ({'color': (0.14, 0.15, 0.16), 'rough': 0.0, 'ior': 1.52, 'transmission': 1.0}, 'glass'),
    'Lens_Tail': ({'color': (0.55, 0.02, 0.02), 'rough': 0.02, 'ior': 1.49, 'transmission': 1.0}, 'glass'),
    'Trim_Black': ({'color': (0.012, 0.012, 0.013), 'rough': 0.35, 'spec': 0.5}, None),
    'Trim_Satin': ({'color': (0.02, 0.02, 0.022), 'rough': 0.6}, None),
    'Trim_Chrome': ({'color': (0.92, 0.92, 0.93), 'metallic': 1.0, 'rough': 0.04}, None),
    'Metal_Brushed': ({'color': (0.80, 0.80, 0.82), 'metallic': 1.0, 'rough': 0.22, 'aniso': 0.85}, 'brushed'),
    'Metal_Cast': ({'color': (0.42, 0.42, 0.43), 'metallic': 1.0, 'rough': 0.48}, None),
    'Metal_Dark': ({'color': (0.10, 0.10, 0.11), 'metallic': 1.0, 'rough': 0.38}, None),
    'Tyre': ({'color': (0.018, 0.018, 0.018), 'rough': 0.82, 'spec': 0.3}, None),
    'Rim': ({'color': (0.035, 0.035, 0.038), 'metallic': 1.0, 'rough': 0.30, 'coat': 0.6, 'coat_rough': 0.08}, None),
    'Brake': ({'color': (0.45, 0.44, 0.43), 'metallic': 1.0, 'rough': 0.42, 'aniso': 0.5}, None),
    'Caliper': ({'color': (0.55, 0.03, 0.02), 'rough': 0.3, 'coat': 1.0, 'coat_rough': 0.05}, None),
    'Lamp_Head': ({'color': (0.9, 0.93, 1.0), 'emission': (0.85, 0.9, 1.0), 'strength': 18.0}, None),
    'Lamp_Tail': ({'color': (0.5, 0.0, 0.0), 'emission': (1.0, 0.02, 0.01), 'strength': 12.0}, None),
    'Lamp_Indicator': ({'color': (0.9, 0.4, 0.0), 'emission': (1.0, 0.45, 0.0), 'strength': 6.0}, None),
    'Reflector': ({'color': (0.95, 0.95, 0.96), 'metallic': 1.0, 'rough': 0.03}, None),
    'Interior': ({'color': (0.028, 0.026, 0.025), 'rough': 0.58, 'sheen': 0.3}, None),
    'Interior_Accent': ({'color': (0.30, 0.05, 0.04), 'rough': 0.5, 'sheen': 0.3}, None),
    'Interior_Trim': ({'color': (0.06, 0.06, 0.065), 'metallic': 0.6, 'rough': 0.28}, None),
    'Screen': ({'color': (0.01, 0.01, 0.012), 'rough': 0.05, 'emission': (0.35, 0.55, 0.9), 'strength': 1.2}, None),
    'Underbody': ({'color': (0.02, 0.02, 0.02), 'rough': 0.8}, None),
    # the back of a deep opening (grille, intake duct): reads as a hole, not a dark surface
    'Void': ({'color': (0.0, 0.0, 0.0), 'rough': 1.0, 'spec': 0.0}, None),
    'Mirror': ({'color': (0.95, 0.95, 0.95), 'metallic': 1.0, 'rough': 0.0}, None),
}


def materials(overrides=None):
    """Create (or reuse) the contract materials; `overrides` maps name -> principled kwargs."""
    out = {}
    for name, (kw, extra) in PALETTE.items():
        kw = {**kw, **((overrides or {}).get(name, {}))}
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
            p = _principled(mat)
            _set(p, **kw)
            if extra == 'flake':
                _flake_normal(mat, p)
            elif extra == 'brushed':
                _brushed(mat, p)
            if extra == 'glass':
                mat.use_backface_culling = False
                try:
                    mat.surface_render_method = 'BLENDED'
                except (AttributeError, TypeError):
                    pass
            base = kw.get('color', (0.5, 0.5, 0.5))
            mat.diffuse_color = (*base, 0.3 if extra == 'glass' else 1.0)
        out[name] = mat
    return out


# ---------------------------------------------------------------- mesh helpers

def mesh_object(name, bm, mats=(), coll=None, smooth=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    if smooth is not None:
        for p in me.polygons:
            p.use_smooth = smooth
    return link(bpy.data.objects.new(name, me), coll)


def apply_modifiers(ob):
    """Bake the modifier stack into the mesh without bpy.ops (works headless)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return ob


def apply_transform(ob):
    ob.data.transform(ob.matrix_basis)
    ob.matrix_basis = Matrix.Identity(4)


SOLVERS = [i.identifier for i in bpy.types.BooleanModifier.bl_rna.properties['solver'].enum_items]


def _bool_once(target, cutter, op, solver):
    md = target.modifiers.new('bool', 'BOOLEAN')
    md.operation = op
    md.object = cutter
    md.solver = solver
    if solver == 'EXACT' and hasattr(md, 'use_self'):
        md.use_self = False
    apply_modifiers(target)


def _broken(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    bad = any(not e.is_manifold for e in bm.edges)
    bm.free()
    return bad


def boolean(target, cutter, op='DIFFERENCE', keep_cutter=False):
    """target <- target (op) cutter. MANIFOLD solver (fast, needs closed inputs, which every
    shell and cutter here is); falls back to EXACT when the fast result is empty or open."""
    before = target.data.copy()
    fast = 'MANIFOLD' if 'MANIFOLD' in SOLVERS else None
    if fast:
        _bool_once(target, cutter, op, fast)
        empty = len(target.data.polygons) == 0 and op != 'INTERSECT'
        if empty or (len(target.data.polygons) and _broken(target.data)):
            old = target.data
            target.data = before.copy()
            bpy.data.meshes.remove(old)
            fast = None
    if not fast:
        _bool_once(target, cutter, op, 'EXACT')
    bpy.data.meshes.remove(before)
    if not keep_cutter:
        me = cutter.data
        bpy.data.objects.remove(cutter, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
    return target


def duplicate(ob, name, coll=None):
    d = ob.copy()
    d.data = ob.data.copy()
    d.name = name
    d.data.name = name
    for c in d.users_collection:
        c.objects.unlink(d)
    return link(d, coll or (ob.users_collection[0] if ob.users_collection else None))


def split_islands(ob, min_faces=1):
    """Split a mesh into connected islands; returns new objects (sorted big first). `ob` is removed."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    seen, islands = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, isl = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            isl.append(g.index)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        if len(isl) >= min_faces:
            islands.append(isl)
    bm.free()
    out = []
    coll = ob.users_collection[0] if ob.users_collection else None
    for k, isl in enumerate(sorted(islands, key=len, reverse=True)):
        keep = set(isl)
        b2 = bmesh.new()
        b2.from_mesh(ob.data)
        b2.faces.ensure_lookup_table()
        bmesh.ops.delete(b2, geom=[f for f in b2.faces if f.index not in keep], context='FACES')
        me = bpy.data.meshes.new(f'{ob.name}.{k:03d}')
        b2.to_mesh(me)
        b2.free()
        for m in ob.data.materials:
            me.materials.append(m)
        o = bpy.data.objects.new(me.name, me)
        o.matrix_world = ob.matrix_world
        out.append(link(o, coll))
    me = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return out


def world_centroid(ob):
    vs = ob.data.vertices
    c = sum((v.co for v in vs), Vector()) / max(1, len(vs))
    return ob.matrix_world @ c


def bbox_world(ob):
    pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def set_material(ob, mat, slot=0):
    me = ob.data
    while len(me.materials) <= slot:
        me.materials.append(mat)
    me.materials[slot] = mat
    for p in me.polygons:
        p.material_index = slot


# ---------------------------------------------------------------- cutters

AXES = {'YZ': (1, 2, 0), 'XZ': (0, 2, 1), 'XY': (0, 1, 2)}   # (u axis, v axis, extrude axis)


def _to3(plane, u, v, w):
    a, b, c = AXES[plane]
    p = [0.0, 0.0, 0.0]
    p[a], p[b], p[c] = u, v, w
    return p


def _ccw(poly):
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly)))
    return poly if area > 0 else list(reversed(poly))


def rounded(poly, r=0.02, seg=4):
    """Round every corner of a 2D polygon by radius r (clamped to a third of the shorter edge)."""
    poly = _ccw([tuple(p) for p in poly])
    n = len(poly)
    out = []
    for i in range(n):
        p0, p1, p2 = Vector(poly[i - 1]), Vector(poly[i]), Vector(poly[(i + 1) % n])
        a, b = (p0 - p1), (p2 - p1)
        la, lb = a.length, b.length
        if la < 1e-6 or lb < 1e-6:
            continue
        rr = min(r, la / 3, lb / 3)
        a.normalize()
        b.normalize()
        s, e = p1 + a * rr, p1 + b * rr
        for k in range(seg + 1):
            t = k / seg
            q = (1 - t) ** 2 * s + 2 * (1 - t) * t * p1 + t * t * e
            out.append((q.x, q.y))
    return out


def prism(name, poly, plane, lo, hi, coll=None):
    """Closed prism: 2D polygon (u, v) in `plane`, extruded from lo to hi along the third axis."""
    poly = _ccw(poly)
    bm = bmesh.new()
    bot = [bm.verts.new(_to3(plane, u, v, lo)) for u, v in poly]
    top = [bm.verts.new(_to3(plane, u, v, hi)) for u, v in poly]
    n = len(poly)
    bm.faces.new(list(reversed(bot)))
    bm.faces.new(top)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, coll=coll)


def shell_between(name, near, far, coll=None):
    """Closed solid between two matching 3D loops (a thin shell swept along view rays): side quads
    plus fan-triangulated caps. The cutter of a photo-traced outline."""
    bm = bmesh.new()
    a = [bm.verts.new(Vector(p)) for p in near]
    b = [bm.verts.new(Vector(p)) for p in far]
    n = len(a)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((a[i], a[j], b[j], b[i]))
    for loop in (a, b):
        c = bm.verts.new(sum((v.co for v in loop), Vector()) / n)
        for i in range(n):
            bm.faces.new((c, loop[i], loop[(i + 1) % n]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, coll=coll)


def _offset(poly, d):
    """Miter-offset a CCW polygon outward by d (negative = inward)."""
    n = len(poly)
    out = []
    for i in range(n):
        p0, p1, p2 = Vector(poly[i - 1]), Vector(poly[i]), Vector(poly[(i + 1) % n])
        e1 = (p1 - p0).normalized()
        e2 = (p2 - p1).normalized()
        n1 = Vector((e1.y, -e1.x))
        n2 = Vector((e2.y, -e2.x))
        m = (n1 + n2)
        if m.length < 1e-6:
            m = n1
        m.normalize()
        k = d / max(0.25, m.dot(n1))
        q = p1 + m * k
        out.append((q.x, q.y))
    return out


def ribbon(name, poly, plane, lo, hi, gap=0.004, coll=None):
    """A closed band of width `gap` along a polygon outline, extruded lo..hi: the shut-line cutter."""
    poly = _ccw(poly)
    outer, inner = _offset(poly, gap / 2), _offset(poly, -gap / 2)
    bm = bmesh.new()
    n = len(poly)
    ob_ = [bm.verts.new(_to3(plane, u, v, lo)) for u, v in outer]
    ot_ = [bm.verts.new(_to3(plane, u, v, hi)) for u, v in outer]
    ib_ = [bm.verts.new(_to3(plane, u, v, lo)) for u, v in inner]
    it_ = [bm.verts.new(_to3(plane, u, v, hi)) for u, v in inner]
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((ob_[i], ob_[j], ot_[j], ot_[i]))       # outer wall
        bm.faces.new((it_[i], it_[j], ib_[j], ib_[i]))       # inner wall
        bm.faces.new((ib_[i], ib_[j], ob_[j], ob_[i]))       # bottom
        bm.faces.new((ot_[i], ot_[j], it_[j], it_[i]))       # top
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, coll=coll)


def mirror_poly_x(poly, plane):
    """Mirror a polygon to the car's other side (only meaningful where plane has an X axis)."""
    if plane == 'XY' or plane == 'XZ':
        return [(-u, v) for u, v in poly]
    return list(poly)


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def project(plane, co):
    a, b, _ = AXES[plane]
    return (co[a], co[b])


# ---------------------------------------------------------------- primitives

def revolve(name, profile, segments=64, axis='X', coll=None, cap=False):
    """Revolve a (r, a) profile (radius, position along axis) about `axis` (X for wheels)."""
    bm = bmesh.new()
    rings = []
    for k in range(segments):
        t = 2 * math.pi * k / segments
        c, s = math.cos(t), math.sin(t)
        ring = []
        for r, a in profile:
            if axis == 'X':
                co = (a, r * c, r * s)
            elif axis == 'Y':
                co = (r * c, a, r * s)
            else:
                co = (r * c, r * s, a)
            ring.append(bm.verts.new(co))
        rings.append(ring)
    m = len(profile)
    for k in range(segments):
        a_, b_ = rings[k], rings[(k + 1) % segments]
        for j in range(m - 1):
            try:
                bm.faces.new((a_[j], b_[j], b_[j + 1], a_[j + 1]))
            except ValueError:
                pass
    if cap:
        for j in (0, m - 1):
            try:
                bm.faces.new([rings[k][j] for k in range(segments)])
            except ValueError:
                pass
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = mesh_object(name, bm, coll=coll, smooth=True)
    return ob


def rounded_box(name, size, center=(0, 0, 0), bevel=0.02, segs=3, coll=None):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=min(bevel, min(size) * 0.45), segments=segs,
                        profile=0.5, affect='EDGES', clamp_overlap=True)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return mesh_object(name, bm, coll=coll, smooth=True)


def tube(name, pts, radius, segments=10, coll=None, closed=False):
    """A round tube along a 3D polyline (DRL strips, pipes, stalks)."""
    pts = [Vector(p) for p in pts]
    bm = bmesh.new()
    rings = []
    n = len(pts)
    for i, p in enumerate(pts):
        if closed:
            t = (pts[(i + 1) % n] - pts[i - 1])
        else:
            t = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        t = t.normalized() if t.length > 1e-9 else Vector((0, 1, 0))
        ref = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
        u = t.cross(ref).normalized()
        v = t.cross(u).normalized()
        rings.append([bm.verts.new(p + (u * math.cos(2 * math.pi * k / segments) + v * math.sin(2 * math.pi * k / segments)) * radius)
                      for k in range(segments)])
    last = n if closed else n - 1
    for i in range(last):
        a, b = rings[i], rings[(i + 1) % n]
        for k in range(segments):
            k2 = (k + 1) % segments
            bm.faces.new((a[k], a[k2], b[k2], b[k]))
    if not closed:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, coll=coll, smooth=True)


def tri_count(objs):
    n = 0
    for o in objs:
        if o.type == 'MESH':
            n += sum(len(p.vertices) - 2 for p in o.data.polygons)
    return n
