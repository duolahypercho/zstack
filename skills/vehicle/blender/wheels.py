"""zstack/vehicle - wheels: tyre, rim, brake disc, caliper, and the steer/spin pivots.

Hierarchy per corner (rig contract):
    Wheel_FL (empty at the wheel centre, steers about Z)
      Caliper_FL                      does not spin
      Wheel_FL_Spin (empty, spins about X)
        Tyre_FL, Rim_FL, Disc_FL

Sizes come from spec.json: wheelRadiusFront/Rear, tyreWidthFront/Rear, rimDiameterFront/Rear
(inches, optional: derived from the tyre sidewall when absent), trackFront/Rear, wheelbase.
"""
import math

import bmesh
from mathutils import Matrix, Vector

import common as C


def tyre_profile(R, W, rim_r):
    """(radius, axial) profile of a tyre, outboard face at +W/2, with 4 tread grooves."""
    h = W / 2
    sh = min(0.022, W * 0.09)                      # shoulder radius
    pts = [(rim_r - 0.004, -h + 0.012), (rim_r + 0.012, -h - 0.003)]
    # inner sidewall bulge
    for t in (0.25, 0.5, 0.75):
        pts.append((rim_r + (R - sh - rim_r) * t, -h - 0.006 * math.sin(math.pi * t) - 0.003))
    for k in range(5):                                # inner shoulder
        a = math.pi / 2 * k / 4
        pts.append((R - sh + sh * math.sin(a), -h + sh - sh * math.cos(a)))
    # tread with grooves
    grooves = [-0.30, -0.10, 0.10, 0.30]
    gw, gd = 0.012, 0.008
    x = -h + sh
    step = (W - 2 * sh) / 24
    while x < h - sh - 1e-6:
        depth = gd if any(abs(x / W - g) < gw / W for g in grooves) else 0.0
        pts.append((R - depth, x))
        x += step
    for k in range(5):
        a = math.pi / 2 * k / 4
        pts.append((R - sh + sh * math.cos(a), h - sh + sh * math.sin(a)))
    for t in (0.25, 0.5, 0.75):
        pts.append((R - sh - (R - sh - rim_r) * t, h + 0.006 * math.sin(math.pi * t) + 0.003))
    pts += [(rim_r + 0.012, h + 0.003), (rim_r - 0.004, h - 0.012)]
    return pts


def rim(name, R, W, spokes=5, twin=True, coll=None, dish=-0.022, split_deg=(6.3, 6.3, 6.3), width=(0.021, 0.014)):
    """Rim barrel + lip + tapered twin spokes + hub, outboard face at +X (local).
    split_deg: half-angle between the two arms of a twin spoke at the hub, mid-spoke and rim (a
    split spoke starts as one and fans out); width: arm half-width at the hub and at the rim."""
    h = W / 2
    lip = 0.018
    bm = bmesh.new()
    # barrel: revolve a profile with a safety hump and a stepped lip
    prof = [(R - 0.035, -h), (R + 0.004, -h + 0.004), (R - 0.012, -h + 0.016), (R - 0.02, 0.0),
            (R - 0.012, h - 0.03), (R, h - 0.012), (R + lip * 0.5, h - 0.004), (R + lip * 0.4, h + 0.002),
            (R - 0.01, h), (R - 0.03, h - 0.004)]
    seg = 96
    rings = []
    for k in range(seg):
        t = 2 * math.pi * k / seg
        rings.append([bm.verts.new((a, r * math.cos(t), r * math.sin(t))) for r, a in prof])
    for k in range(seg):
        a_, b_ = rings[k], rings[(k + 1) % seg]
        for j in range(len(prof) - 1):
            bm.faces.new((a_[j], b_[j], b_[j + 1], a_[j + 1]))
    # spokes: each a tapered box from the hub to the barrel, dished outward at the hub
    hub_r = R * 0.24
    arms = []
    for i in range(spokes):
        base = 2 * math.pi * i / spokes
        arms += [(base, -1), (base, 1)] if twin else [(base, 0)]
    for base, sgn in arms:
        w0, w1 = width
        x_hub, x_rim = h - 0.004 + dish, h - 0.024
        quads = []
        for (r_, w_, x_), sd in zip(((hub_r * 0.9, w0, x_hub), (R * 0.62, (w0 + w1) / 2, (x_hub + x_rim) / 2 + 0.006),
                                     (R - 0.016, w1, x_rim)), split_deg):
            ang = base + sgn * math.radians(sd)
            c, s = math.cos(ang), math.sin(ang)
            d = Vector((0, c, s))
            side = Vector((0, -s, c))
            ctr = d * r_ + Vector((x_, 0, 0))
            depth = 0.03
            quads.append([bm.verts.new(ctr + side * sw + Vector((dx, 0, 0)))
                          for sw, dx in ((-w_, 0), (w_, 0), (w_ * 0.7, -depth), (-w_ * 0.7, -depth))])
        for qa, qb in zip(quads, quads[1:]):
            for j in range(4):
                j2 = (j + 1) % 4
                bm.faces.new((qa[j], qb[j], qb[j2], qa[j2]))
        bm.faces.new(list(reversed(quads[0])))
        bm.faces.new(quads[-1])
    # hub face with lug bolts
    hub = []
    for k in range(40):
        t = 2 * math.pi * k / 40
        hub.append(bm.verts.new((h + dish, hub_r * math.cos(t), hub_r * math.sin(t))))
    back = [bm.verts.new((h + dish - 0.03, v.co.y, v.co.z)) for v in hub]
    bm.faces.new(hub)
    for k in range(40):
        k2 = (k + 1) % 40
        bm.faces.new((hub[k], back[k], back[k2], hub[k2]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = C.mesh_object(name, bm, coll=coll, smooth=True)
    for p in ob.data.polygons:
        p.use_smooth = abs(p.normal.x) < 0.9
    return ob


def _rim_style(spec):
    """spec.json "rim": {"spokes": 5, "twin": true, "splitDeg": [hub, mid, rim], "width": [hub, rim]}."""
    st = spec.get('rim', {})
    out = {}
    if 'spokes' in st:
        out['spokes'] = st['spokes']
    if 'twin' in st:
        out['twin'] = st['twin']
    if 'splitDeg' in st:
        out['split_deg'] = tuple(st['splitDeg'])
    if 'width' in st:
        out['width'] = tuple(st['width'])
    return out


def disc(name, r, coll=None, thick=0.032, holes=True):
    prof = [(r * 0.45, -thick / 2), (r, -thick / 2), (r, thick / 2), (r * 0.62, thick / 2),
            (r * 0.55, thick / 2 + 0.018), (r * 0.30, thick / 2 + 0.018), (r * 0.30, -thick / 2)]
    ob = C.revolve(name, prof, segments=72, axis='X', coll=coll, cap=False)
    if holes:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        for k in range(24):
            t = 2 * math.pi * k / 24
            for rr in (r * 0.72, r * 0.86):
                tt = t + (0.13 if rr > r * 0.8 else 0)
                co = Vector((thick / 2 + 0.0004, rr * math.cos(tt), rr * math.sin(tt)))
                circ = bmesh.ops.create_circle(bm, cap_ends=True, radius=0.0035, segments=8)
                vs = circ['verts']
                bmesh.ops.rotate(bm, verts=vs, cent=Vector(), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
                bmesh.ops.translate(bm, verts=vs, vec=co)
        bm.to_mesh(ob.data)
        bm.free()
    return ob


def caliper(name, r_disc, coll=None):
    """Four-piston caliper straddling the disc at the rear-top (local frame, before the side flip)."""
    bm = bmesh.new()
    segs = 10
    a0, a1 = math.radians(20), math.radians(75)
    rin, rout = r_disc * 0.70, r_disc * 1.08
    for side, (x0, x1) in ((0, (0.020, 0.045)), (1, (-0.045, -0.020))):
        rows = []
        for i in range(segs + 1):
            t = a0 + (a1 - a0) * i / segs
            c, s = math.cos(t), math.sin(t)
            rows.append([bm.verts.new((x, r * c, r * s)) for x, r in ((x0, rin), (x0, rout), (x1, rout), (x1, rin))])
        for ra, rb in zip(rows, rows[1:]):
            for j in range(4):
                j2 = (j + 1) % 4
                bm.faces.new((ra[j], rb[j], rb[j2], ra[j2]))
        bm.faces.new(list(reversed(rows[0])))
        bm.faces.new(rows[-1])
    bridge = C.rounded_box('_tmp', (0.09, 0.04, 0.03), (0, 0, 0), 0.008)
    t = (a0 + a1) / 2
    bm2 = bmesh.new()
    bm2.from_mesh(bridge.data)
    bmesh.ops.rotate(bm2, verts=bm2.verts, cent=Vector(), matrix=Matrix.Rotation(t, 3, 'X'))
    bmesh.ops.translate(bm2, verts=bm2.verts, vec=Vector((0, rout * math.cos(t), rout * math.sin(t))))
    me_tmp = bridge.data
    import bpy
    bpy.data.objects.remove(bridge, do_unlink=True)
    bm2.to_mesh(me_tmp)
    bm2.free()
    bm.from_mesh(me_tmp)
    bpy.data.meshes.remove(me_tmp)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return C.mesh_object(name, bm, coll=coll, smooth=True)


def build(spec, mats, coll=None):
    """Build and rig four wheels. Returns {corner: steer pivot}."""
    L = spec['length']
    y_f = -L / 2 + spec['frontOverhang']
    y_r = y_f + spec['wheelbase']
    out = {}
    for corner in ('FL', 'FR', 'RL', 'RR'):
        front = corner[0] == 'F'
        left = corner[1] == 'L'
        R = spec['wheelRadiusFront' if front else 'wheelRadiusRear']
        W = spec['tyreWidthFront' if front else 'tyreWidthRear']
        track = spec['trackFront' if front else 'trackRear']
        rim_in = spec.get('rimDiameterFront' if front else 'rimDiameterRear')
        rim_r = (rim_in * 0.0254 / 2) if rim_in else R - W * spec.get('aspect', 0.33)
        x = track / 2 * (1 if left else -1)
        centre = Vector((x, y_f if front else y_r, R))
        steer = C.empty(f'Wheel_{corner}', centre, coll, size=R)
        steer['steers'] = front
        spin = C.empty(f'Wheel_{corner}_Spin', centre, coll, size=R * 0.8)
        spin['spinAxis'] = [1.0, 0.0, 0.0]
        C.parent_keep(spin, steer)
        parts = [
            (C.revolve(f'Tyre_{corner}', tyre_profile(R, W, rim_r), 128, 'X', coll), mats['Tyre'], spin),
            (rim(f'Rim_{corner}', rim_r, W * 0.92, coll=coll, **_rim_style(spec)), mats['Rim'], spin),
            (disc(f'Disc_{corner}', rim_r * (0.80 if front else 0.78), coll), mats['Brake'], spin),
            (caliper(f'Caliper_{corner}', rim_r * (0.80 if front else 0.78), coll), mats['Caliper'], steer),
        ]
        for ob, mat, par in parts:
            C.set_material(ob, mat)
            ob['zstack'] = 'wheel'
            if not left:       # outboard face points to the car's outside on both sides
                ob.data.transform(Matrix.Scale(-1, 4, (1, 0, 0)))
                ob.data.flip_normals()
            if ob.name.startswith('Caliper') and front:   # caliper sits behind the front axle line
                ob.data.transform(Matrix.Rotation(math.radians(0), 4, 'X'))
            if ob.name.startswith('Disc') or ob.name.startswith('Caliper'):
                ob.data.transform(Matrix.Translation((-(W * 0.18) if left else W * 0.18, 0, 0)))
            ob.location = centre
            ob.parent = par
            ob.matrix_parent_inverse = par.matrix_world.inverted()
        out[corner] = steer
    return out
