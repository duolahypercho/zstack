"""zstack/vehicle - a cabin that reads through the glass and receives the occupants.

Driven by parts.json "cabin":
    {"front": -0.55, "rear": 0.52, "floorZ": 0.17, "hipZ": 0.34, "seatX": 0.37,
     "halfWidth": 0.78, "dashZ": 0.86, "lhd": true, "seats": 2}

Makes: Cabin_Tub (floor, tunnel, bulkhead), Seat_FL/FR pivots at the hip point with bucket
seats, Dash (with screens), SteeringWheel pivot on the column axis, Console, pedals.
Door cards are made per door by door_cards(), parented to the door pivot.
"""
import math

import bmesh
from mathutils import Matrix, Vector

import common as C


def _loft(name, sections, coll=None, cap=True):
    """Loft closed 3D rings (same point count) into a tube; caps both ends."""
    bm = bmesh.new()
    rings = [[bm.verts.new(p) for p in ring] for ring in sections]
    n = len(rings[0])
    for a, b in zip(rings, rings[1:]):
        for j in range(n):
            j2 = (j + 1) % n
            bm.faces.new((a[j], a[j2], b[j2], b[j]))
    if cap:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return C.mesh_object(name, bm, coll=coll, smooth=True)


def _squircle(w, h, n=24, p=4.0):
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        c, s = math.cos(t), math.sin(t)
        pts.append((w / 2 * math.copysign(abs(c) ** (2 / p), c), h / 2 * math.copysign(abs(s) ** (2 / p), s)))
    return pts


def bucket_seat(name, mats, coll=None):
    """Seat in local space: hip point at origin, facing -Y, +Z up."""
    objs = []
    # cushion: lofted squircle sections along Y, bolsters raised at the sides
    secs = []
    for y, w, h, z in ((-0.30, 0.44, 0.07, -0.03), (-0.12, 0.50, 0.09, -0.05), (0.08, 0.52, 0.10, -0.07)):
        ring = []
        for u, v in _squircle(w, h, 28):
            bol = 0.035 * (abs(u) / (w / 2)) ** 6
            ring.append((u, y, z + v + bol))
        secs.append(ring)
    cush = _loft(name + '_Cushion', secs, coll)
    # backrest: reclined ~22 deg, tall with an integrated head restraint, winged bolsters
    secs = []
    rec = math.radians(22)
    for s, w, t in ((0.00, 0.52, 0.10), (0.25, 0.54, 0.11), (0.48, 0.46, 0.09), (0.60, 0.30, 0.08), (0.78, 0.28, 0.08)):
        ring = []
        for u, v in _squircle(w, t, 28):
            wing = 0.06 * (abs(u) / (w / 2)) ** 5 if s < 0.5 else 0.0
            yy = 0.10 + s * math.sin(rec) + v - wing
            zz = -0.02 + s * math.cos(rec)
            ring.append((u, yy, zz))
        secs.append(ring)
    back = _loft(name + '_Back', secs, coll)
    for o in (cush, back):
        C.set_material(o, mats['Interior'])
        objs.append(o)
    # contrast stripe down the backrest centre
    stripe = C.rounded_box(name + '_Stripe', (0.12, 0.012, 0.55), (0, 0.035, 0.26), 0.004, 2, coll)
    stripe.data.transform(Matrix.Rotation(-rec, 4, 'X'))
    C.set_material(stripe, mats['Interior_Accent'])
    objs.append(stripe)
    return objs


def steering_wheel(name, mats, coll=None, width=0.36, height=0.33):
    """Squared-off wheel in local space: rim in the XZ plane, column along +Y (toward the driver)."""
    objs = []
    ring = [(u, 0.0, v) for u, v in _squircle(width, height, 48, 3.2)]
    rim = C.tube(name + '_Rim', ring, 0.016, 12, coll, closed=True)
    C.set_material(rim, mats['Interior'])
    objs.append(rim)
    hub = C.rounded_box(name + '_Hub', (0.13, 0.05, 0.10), (0, 0.03, -0.01), 0.02, 3, coll)
    C.set_material(hub, mats['Interior_Trim'])
    objs.append(hub)
    for a in (0.0, math.pi, -math.pi / 2):
        tip = (math.cos(a) * width * 0.47, 0.0, math.sin(a) * height * 0.47)
        sp = C.tube(f'{name}_Spoke{len(objs)}', [(0, 0.02, -0.01), tip], 0.011, 8, coll)
        C.set_material(sp, mats['Interior_Trim'])
        objs.append(sp)
    col = C.tube(name + '_Column', [(0, 0.05, 0), (0, 0.30, 0)], 0.035, 12, coll)
    C.set_material(col, mats['Trim_Satin'])
    objs.append(col)
    return objs


def build(spec, cab, mats, coll=None):
    """Returns dict of pivots: seats and steering wheel, plus mesh list."""
    y0, y1 = cab['front'], cab['rear']
    fz, hz = cab['floorZ'], cab['hipZ']
    hw = cab['halfWidth']
    sx = cab['seatX']
    lhd = cab.get('lhd', True)
    drv = 1 if lhd else -1           # +X is the car's LEFT
    out = {'meshes': []}
    # tub: floor, sills, tunnel, firewall, bulkhead
    parts = [
        ('Cabin_Floor', (2 * hw, y1 - y0 + 0.30, 0.02), (0, (y0 + y1) / 2 + 0.05, fz - 0.01)),
        ('Cabin_Tunnel', (0.22, y1 - y0 + 0.2, cab.get('tunnelH', 0.26)), (0, (y0 + y1) / 2, fz + cab.get('tunnelH', 0.26) / 2)),
        ('Cabin_Bulkhead', (2 * hw, 0.03, cab.get('bulkheadH', 0.72)), (0, y1 + 0.02, fz + cab.get('bulkheadH', 0.72) / 2)),
        ('Cabin_Firewall', (2 * hw, 0.03, 0.45), (0, y0 - 0.32, fz + 0.22)),
        ('Cabin_Sill_L', (0.12, y1 - y0 + 0.1, 0.22), (hw - 0.06, (y0 + y1) / 2, fz + 0.11)),
        ('Cabin_Sill_R', (0.12, y1 - y0 + 0.1, 0.22), (-hw + 0.06, (y0 + y1) / 2, fz + 0.11)),
    ]
    for n, size, ctr in parts:
        o = C.rounded_box(n, size, ctr, 0.01, 2, coll)
        C.set_material(o, mats['Interior'] if 'Tunnel' in n else mats['Trim_Satin'])
        out['meshes'].append(o)
    # console ridge rising to the dash
    ridge = _loft('Console', [
        [(u, y, fz + zz + v) for u, v in _squircle(0.14, 0.06, 16)]
        for y, zz in ((y1 - 0.20, 0.30), (y0 + 0.25, 0.36), (y0 + 0.02, 0.50))
    ], coll)
    C.set_material(ridge, mats['Interior_Trim'])
    out['meshes'].append(ridge)
    # dash: a swept loft across the car under the windscreen base
    dz = cab['dashZ']
    secs = []
    for x in [-hw + 0.03 + (2 * hw - 0.06) * i / 12 for i in range(13)]:
        hood = 0.05 * math.exp(-((x - drv * sx) / 0.14) ** 2)      # instrument binnacle over the driver
        ring = [(x, y0 + 0.02 + dy, dz + dzz) for dy, dzz in
                ((0.00, 0.00), (0.10, 0.02 + hood), (0.24, -0.02 + hood * 0.6), (0.28, -0.10), (0.18, -0.26), (0.02, -0.22))]
        secs.append(ring)
    dash = _loft('Dash', secs, coll)
    C.set_material(dash, mats['Interior'])
    out['meshes'].append(dash)
    for n, w, ctr in (('Screen_Cluster', 0.26, (drv * sx, y0 + 0.21, dz - 0.02)),
                      ('Screen_Centre', 0.20, (drv * 0.12, y0 + 0.24, dz - 0.08))):
        s = C.rounded_box(n, (w, 0.008, 0.09), ctr, 0.004, 2, coll)
        s.data.transform(Matrix.Translation(-Vector(ctr)))
        s.data.transform(Matrix.Rotation(math.radians(-25), 4, 'X'))
        s.data.transform(Matrix.Translation(Vector(ctr)))
        C.set_material(s, mats['Screen'])
        out['meshes'].append(s)
    # seats
    seats = [('FL', 1), ('FR', -1)] if cab.get('seats', 2) >= 2 else [('FL' if lhd else 'FR', drv)]
    for tag, sgn in seats:
        piv = C.empty(f'Seat_{tag}', (sgn * sx, cab.get('hipY', y1 - 0.30), hz), coll, 0.15)
        piv['occupant'] = 'driver' if sgn == drv else 'passenger'
        for o in bucket_seat(f'SeatMesh_{tag}', mats, coll):
            o.location = piv.location
            o.parent = piv
            o.matrix_parent_inverse = piv.matrix_world.inverted()
            out['meshes'].append(o)
        out[f'Seat_{tag}'] = piv
    # steering wheel on the column: rim plane faces the driver, tilted back from vertical
    tilt = math.radians(cab.get('wheelTiltDeg', 22))
    wc = Vector((drv * sx, cab.get('wheelY', y0 + 0.40), cab.get('wheelZ', dz - 0.06)))
    sw = C.empty('SteeringWheel', wc, coll, 0.2)
    axis = Vector((0, math.cos(tilt), math.sin(tilt)))       # column axis toward the driver
    sw['spinAxis'] = list(axis)
    for o in steering_wheel('SteeringWheelMesh', mats, coll):
        o.data.transform(Matrix.Rotation(tilt, 4, 'X'))
        o.location = wc
        o.parent = sw
        o.matrix_parent_inverse = sw.matrix_world.inverted()
        out['meshes'].append(o)
    out['SteeringWheel'] = sw
    # pedals
    for i, px in enumerate((-0.09, 0.0, 0.08)):
        p = C.rounded_box(f'Pedal{i}', (0.05, 0.012, 0.08), (drv * sx + px, y0 - 0.20, fz + 0.12), 0.006, 2, coll)
        C.set_material(p, mats['Metal_Brushed'])
        out['meshes'].append(p)
    for o in out['meshes']:
        o['zstack'] = 'interior'
    return out


def door_card(name, skin, mats, coll=None):
    """An armrest + pull on the inside of a door skin (parented by the caller)."""
    lo, hi = C.bbox_world(skin)
    side = 1 if lo.x + hi.x > 0 else -1
    x_in = (lo.x if side > 0 else hi.x) + side * 0.015
    y = (lo.y + hi.y) / 2
    z = lo.z + (hi.z - lo.z) * 0.45
    # armrest in the rear half of the door, clear of the dash when the door swings
    ln = (hi.y - lo.y) * 0.42
    arm = C.rounded_box(name + '_Armrest', (0.05, ln, 0.05), (x_in - side * 0.03, hi.y - 0.06 - ln / 2, z), 0.015, 3, coll)
    C.set_material(arm, mats['Interior'])
    pull = C.rounded_box(name + '_Pull', (0.02, 0.14, 0.025), (x_in - side * 0.02, hi.y - 0.30, z + 0.12), 0.008, 2, coll)
    C.set_material(pull, mats['Metal_Brushed'])
    for o in (arm, pull):
        o['zstack'] = 'interior'
    return [arm, pull]
