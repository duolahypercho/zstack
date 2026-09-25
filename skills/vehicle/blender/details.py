"""zstack/vehicle - bolt-on details: door mirrors, exhaust tips, splitter / diffuser blades.

parts.json:
    "mirrors":  [{"at": [0.93, -0.42, 0.98], "parent": "Door_FL", "mirror": true}]
    "exhaust":  {"tips": [[0.07, 2.30, 0.30], ...], "r": 0.042, "length": 0.14}
    "blades":   [{"name": "Splitter", "poly": [[x, y], ...], "z": 0.12, "t": 0.012, "material": "Trim_Black"}]
"""
import math

from mathutils import Matrix, Vector

import common as C


def _mirror_one(name, at, side, mats, coll):
    objs = []
    # teardrop housing: lofted squircles along the car's X, facing forward
    secs = []
    for k, (dx, w, h) in enumerate(((0.0, 0.06, 0.05), (0.05, 0.17, 0.085), (0.13, 0.19, 0.095), (0.19, 0.16, 0.08))):
        ring = []
        for j in range(24):
            t = 2 * math.pi * j / 24
            c, s = math.cos(t), math.sin(t)
            u = w / 2 * math.copysign(abs(c) ** 0.6, c)
            v = h / 2 * math.copysign(abs(s) ** 0.6, s)
            ring.append((side * dx, u * 0.55 + 0.02 * (dx / 0.19), v))
        secs.append(ring)
    from interior import _loft
    house = _loft(name + '_Housing', secs, coll)
    C.set_material(house, mats['Paint'])
    glass = C.rounded_box(name + '_Glass', (0.17, 0.004, 0.07), (side * 0.11, 0.07, 0.0), 0.012, 3, coll)
    C.set_material(glass, mats['Mirror'])
    stalk = C.tube(name + '_Stalk', [(0, 0.0, -0.02), (side * 0.03, -0.01, -0.035), (side * 0.07, 0.0, -0.02)], 0.012, 10, coll)
    C.set_material(stalk, mats['Trim_Black'])
    for o in (house, glass, stalk):
        o.location = at
        C.apply_transform(o)
        o['zstack'] = 'mirror'
        objs.append(o)
    return objs


def mirrors(defs, mats, coll=None):
    out = {}
    for d in defs:
        at = Vector(d['at'])
        out.setdefault(d.get('parent', ''), []).extend(_mirror_one('Mirror_L', at, 1, mats, coll))
        if d.get('mirror'):
            par = d.get('parent', '').replace('FL', 'FR').replace('RL', 'RR')
            out.setdefault(par, []).extend(_mirror_one('Mirror_R', Vector((-at.x, at.y, at.z)), -1, mats, coll))
    return out


def exhaust(defn, mats, coll=None):
    objs = []
    r, ln = defn.get('r', 0.042), defn.get('length', 0.14)
    for i, tip in enumerate(defn.get('tips', [])):
        o = C.revolve(f'Exhaust_{i}', [(r * 0.82, 0.0), (r, 0.004), (r, ln), (r * 0.88, ln), (r * 0.86, 0.01)], 40, 'Y', coll)
        o.location = (tip[0], tip[1] - ln, tip[2])
        C.apply_transform(o)
        C.set_material(o, mats['Trim_Chrome'])
        cap = C.revolve(f'Exhaust_{i}_Inner', [(0.0, ln * 0.4), (r * 0.8, ln * 0.4)], 24, 'Y', coll)
        cap.location = (tip[0], tip[1] - ln, tip[2])
        C.apply_transform(cap)
        C.set_material(cap, mats['Underbody'])
        for x in (o, cap):
            x['zstack'] = 'exterior'
        objs += [o, cap]
    return objs


def spoiler(d, mats, coll=None):
    """Rear wing: a cambered blade across the tail on uprights rising from the deck.
    d: {y: [lead, trail], z, halfSpan, chord, thick, pitchDeg, uprights: [x, ...], material}"""
    if not d:
        return []
    import bmesh
    y0, y1 = d['y']
    hs, t = d['halfSpan'], d.get('thick', 0.018)
    pitch = math.radians(d.get('pitchDeg', -6))
    bm = bmesh.new()
    prof = []
    for k in range(9):               # thin cambered aerofoil, leading edge at y0
        u = k / 8
        camber = 0.012 * math.sin(math.pi * u)
        prof.append((y0 + (y1 - y0) * u, camber + t * 0.5 * (1 - u) ** 0.5))
    prof += [(y, zz - t * 0.9 * (1 - (i / 8)) ** 0.5) for i, (y, zz) in reversed(list(enumerate(prof)))]
    rings = []
    for x in (-hs, -hs * 0.5, 0.0, hs * 0.5, hs):
        rise = 0.02 * (abs(x) / hs) ** 2            # tips sweep up slightly
        rings.append([bm.verts.new((x, y, d['z'] + zz + rise)) for y, zz in prof])
    n = len(prof)
    for a, b in zip(rings, rings[1:]):
        for j in range(n):
            j2 = (j + 1) % n
            bm.faces.new((a[j], a[j2], b[j2], b[j]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    bmesh.ops.rotate(bm, verts=bm.verts, cent=Vector((0, y0, d['z'])), matrix=Matrix.Rotation(pitch, 3, 'X'))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    wing = C.mesh_object('Spoiler', bm, [mats[d.get('material', 'Trim_Black')]], coll, smooth=True)
    objs = [wing]
    for i, x in enumerate(d.get('uprights', [])):
        for s in (1, -1):
            up = C.rounded_box(f'Spoiler_Upright{i}{"L" if s > 0 else "R"}', (0.014, (y1 - y0) * 0.55, 0.12),
                               (s * x, (y0 + y1) / 2 + 0.01, d['z'] - 0.05), 0.004, 2, coll)
            C.set_material(up, mats[d.get('material', 'Trim_Black')])
            objs.append(up)
    for o in objs:
        o['zstack'] = 'exterior'
    return objs


def blades(defs, mats, coll=None):
    objs = []
    for d in defs:
        z, t = d['z'], d.get('t', 0.012)
        o = C.prism(d['name'], C.rounded(d['poly'], d.get('round', 0.02)), 'XY', z - t / 2, z + t / 2, coll)
        C.set_material(o, mats[d.get('material', 'Trim_Black')])
        o['zstack'] = 'exterior'
        objs.append(o)
    return objs
