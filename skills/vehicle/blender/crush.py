"""zstack/vehicle - crash deformation as morph targets (shape keys -> glTF morph targets).

Four keys on every exterior mesh: Crush_Front, Crush_Rear, Crush_Left, Crush_Right. Each is one
world-space displacement field evaluated at every part's rest vertices, so a door, the body and
the lamp lens beside it fold together (no part floats off its neighbour). An engine blends a
key's weight 0..1 with impact energy.

The field is a crumple zone, not a squash:
  - displacement only inside the zone (front: from the nose back `reach` metres), ramping from
    full depth at the face to zero at the zone's inner end with a C1 smoothstep, so the structure
    behind the zone (cabin, wheels) does not move,
  - depth <= CRUMPLE_MAX_FILL of the zone, and slope < 1, so no vertex overtakes its neighbour
    (no inside-out folds),
  - buckling: the compressed zone wrinkles (bonnet peaks up, flanks bulge out) in proportion to
    the local compression.

check() proves: cabin cell displacement <= CABIN_TOL, no face flips beyond FLIP_TOL, wheels untouched.
"""
import math

import bmesh
import bpy
from mathutils import Vector

import common as C

CRUMPLE_MAX_FILL = 0.85      # zone may compress to at most 85% of its length
CABIN_TOL = 0.01             # m: max displacement allowed inside the cabin safety cell
FLIP_TOL = 0.01              # fraction of faces allowed to flip normal (thin trim)


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def fields(spec, cfg=None):
    """Displacement functions for the four impacts, sized from the spec."""
    cfg = cfg or {}
    L, W = spec['length'], spec['width']
    y0, y1 = -L / 2, L / 2
    fo, ro = spec['frontOverhang'], spec['rearOverhang']
    # zones stop short of the tyre's leading face so the wheels stay where they are
    rf = cfg.get('frontReach', fo - spec['wheelRadiusFront'] - 0.05)
    rr = cfg.get('rearReach', ro - spec['wheelRadiusRear'] - 0.05)
    rs = cfg.get('sideReach', 0.30)
    df = min(cfg.get('frontDepth', 0.30), CRUMPLE_MAX_FILL * rf * 0.6)
    dr = min(cfg.get('rearDepth', 0.22), CRUMPLE_MAX_FILL * rr * 0.6)
    ds = min(cfg.get('sideDepth', 0.16), CRUMPLE_MAX_FILL * rs * 0.6)
    side_y = cfg.get('sideCentreY', 0.0)
    wr = W / 2

    def wrinkle(co, amt, freq=17.0):
        return math.sin(co.y * freq + 1.3) * math.cos(co.x * 9.0) * amt

    def front(co):
        u = (co.y - y0) / rf                       # 0 at the nose, 1 at the zone end
        k = 1 - smooth(u)
        if k <= 0:
            return Vector()
        comp = k * df
        up = 0.05 * math.sin(math.pi * min(1, u)) * k if co.z > 0.55 else 0.0
        return Vector((co.x * 0.05 * k, comp, up + wrinkle(co, 0.012 * k)))

    def rear(co):
        u = (y1 - co.y) / rr
        k = 1 - smooth(u)
        if k <= 0:
            return Vector()
        up = 0.035 * math.sin(math.pi * min(1, u)) * k if co.z > 0.6 else 0.0
        return Vector((co.x * 0.04 * k, -k * dr, up + wrinkle(co, 0.010 * k)))

    def side(sign):
        def f(co):
            u = (wr - sign * co.x) / rs              # 0 at the outer skin, 1 at the zone end
            k = (1 - smooth(u)) * math.exp(-((co.y - side_y) / 0.75) ** 2) * math.exp(-((co.z - 0.55) / 0.45) ** 2)
            if k <= 1e-4:
                return Vector()
            return Vector((-sign * k * ds, wrinkle(co, 0.01 * k, 11.0), wrinkle(co, 0.008 * k, 13.0)))
        return f

    return {'Crush_Front': front, 'Crush_Rear': rear, 'Crush_Left': side(1), 'Crush_Right': side(-1)}


def add_keys(objs, spec, cfg=None):
    fs = fields(spec, cfg)
    for ob in objs:
        if ob.type != 'MESH' or not ob.data.vertices:
            continue
        if not ob.data.shape_keys:
            ob.shape_key_add(name='Basis', from_mix=False)
        mw = ob.matrix_world
        inv = mw.inverted().to_3x3()
        for name, f in fs.items():
            key = ob.data.shape_keys.key_blocks.get(name) or ob.shape_key_add(name=name, from_mix=False)
            key.value = 0.0            # new keys can start at weight 1; the rest pose is uncrushed
            key.slider_max = 1.0
            for v, kv in zip(ob.data.vertices, key.data):
                d = f(mw @ v.co)
                kv.co = v.co + inv @ d
    return list(fs)


def check(objs, spec, cabin, wheels=()):
    """cabin: {'y': [y0, y1], 'halfWidth': w, 'z': [z0, z1]} safety cell."""
    fs = fields(spec)
    rep = {'pass': True, 'keys': {}}
    for name in fs:
        worst_cab, flips, faces = 0.0, 0, 0
        for ob in objs:
            if ob.type != 'MESH' or not ob.data.shape_keys:
                continue
            kb = ob.data.shape_keys.key_blocks.get(name)
            if kb is None:
                continue
            mw = ob.matrix_world
            base = [mw @ v.co for v in ob.data.vertices]
            moved = [mw @ k.co for k in kb.data]
            for b, m in zip(base, moved):
                if cabin['y'][0] < b.y < cabin['y'][1] and abs(b.x) < cabin['halfWidth'] and cabin['z'][0] < b.z < cabin['z'][1]:
                    worst_cab = max(worst_cab, (m - b).length)
            for p in ob.data.polygons:
                if len(p.vertices) < 3:
                    continue
                i0, i1, i2 = p.vertices[0], p.vertices[1], p.vertices[2]
                n0 = (base[i1] - base[i0]).cross(base[i2] - base[i0])
                n1 = (moved[i1] - moved[i0]).cross(moved[i2] - moved[i0])
                if n0.length > 1e-10:
                    faces += 1
                    if n0.dot(n1) < 0:
                        flips += 1
        frac = flips / max(1, faces)
        ok = worst_cab <= CABIN_TOL and frac <= FLIP_TOL
        rep['keys'][name] = {'cabinMaxM': round(worst_cab, 4), 'flippedFaces': flips, 'flipFraction': round(frac, 5), 'pass': ok}
        rep['pass'] = rep['pass'] and ok
    wheel_keys = [w.name for w in wheels if w.type == 'MESH' and w.data.shape_keys]
    if wheel_keys:
        rep['pass'] = False
        rep['wheelsKeyed'] = wheel_keys
    return rep
