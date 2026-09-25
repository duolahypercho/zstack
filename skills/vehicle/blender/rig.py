"""zstack/vehicle - hinge pivots for every opening panel (rig contract in reference/rig-contract.md).

The hinge is placed ON the panel's own geometry, at the edge the part really hinges from, and
the opening sense is measured rather than assumed: a tiny trial rotation each way, keep the one
that moves the panel outward (doors) or upward (lids). door_check.py then proves the full swing.

Panel definition fields used here (parts.json "panels"):
    type:      conventional | scissor | butterfly | gullwing | lid
    hinge:     front | rear | top | bottom          (edge the panel hinges from)
    openLimit: radians                              (full open angle)
    tiltDeg:   inward tilt of a conventional door's axis at the top (default 2.5)
"""
import math

from mathutils import Quaternion, Vector

import common as C


def _verts_world(ob):
    mw = ob.matrix_world
    return [mw @ v.co for v in ob.data.vertices]


def hinge_frame(part, skin):
    vs = _verts_world(skin)
    side = part.get('side', 0)
    kind = part.get('type', 'conventional')
    edge = part.get('hinge', 'front' if kind != 'lid' else 'rear')
    ys = [v.y for v in vs]
    zs = [v.z for v in vs]
    band = 0.04
    if edge in ('front', 'rear'):
        ye = min(ys) if edge == 'front' else max(ys)
        edge_vs = [v for v in vs if abs(v.y - ye) < band]
    elif edge == 'top':
        ze = max(zs)
        edge_vs = [v for v in vs if abs(v.z - ze) < band]
    else:
        ze = min(zs)
        edge_vs = [v for v in vs if abs(v.z - ze) < band]
    if kind in ('conventional', 'scissor', 'butterfly'):
        # the OUTERMOST point of the hinge edge: every other point of the edge is then inboard of
        # the axis and swings back into the gap, never forward into the fender
        piv = max(edge_vs, key=lambda v: v.x * side)
    else:
        # lids: the HIGHEST point of the whole hinge edge. Anything above the axis swings back into
        # the scuttle as the lid rises; a bonnet sitting in a valley between the fenders has its
        # corners higher than its centre, so the centre is the wrong place for the hinge
        piv = max(edge_vs, key=lambda v: v.z)
    t = math.radians(part.get('tiltDeg', 2.5))
    if kind == 'conventional':
        axis = Vector((-side * math.sin(t), 0.0, math.cos(t)))
    elif kind == 'scissor':
        axis = Vector((1.0, 0.0, 0.0))
    elif kind == 'butterfly':
        axis = Vector((0.35 * side, -0.55, 0.76)).normalized()
    elif kind == 'gullwing':
        axis = Vector((0.0, 1.0, 0.0))
    else:
        axis = Vector((1.0, 0.0, 0.0))
    if part.get('axis'):
        axis = Vector(part['axis']).normalized()
        axis.x *= side if side else 1
    return Vector(piv), axis.normalized(), side


def _trial(vs, piv, axis, ang):
    q = Quaternion(axis, ang)
    c0 = sum(vs, Vector()) / len(vs)
    c1 = sum(((q @ (v - piv)) + piv for v in vs), Vector()) / len(vs)
    return c1 - c0


def open_sign(part, skin, piv, axis, side):
    vs = _verts_world(skin)
    best, sgn = None, 1
    for s in (1, -1):
        d = _trial(vs, piv, axis, s * 0.05)
        score = d.x * side + max(d.z, 0.0) if part.get('type', 'conventional') in ('conventional', 'butterfly') and side else d.z
        if best is None or score > best:
            best, sgn = score, s
    return sgn


def build(panels, skins, extras, coll=None):
    """panels: expanded panel defs; skins: {name: skin}; extras: {panel name: [objects to carry]}."""
    pivots = {}
    for part in panels:
        skin = skins.get(part['name'])
        if skin is None:
            continue
        piv, axis, side = hinge_frame(part, skin)
        pv = C.empty(part['name'], piv, coll, 0.12)
        pv['hingeAxis'] = list(axis)
        pv['openSign'] = open_sign(part, skin, piv, axis, side)
        pv['openLimit'] = part.get('openLimit', math.radians(65))
        pv['doorType'] = part.get('type', 'conventional')
        for ob in [skin] + extras.get(part['name'], []):
            C.parent_keep(ob, pv)
        pivots[part['name']] = pv
    return pivots


def pose(pivots, amount=1.0):
    """Open every pivot to `amount` of its limit (for renders); amount=0 restores."""
    for pv in pivots.values():
        pv.rotation_mode = 'QUATERNION'
        pv.rotation_quaternion = Quaternion(Vector(pv['hingeAxis']), pv['openSign'] * pv['openLimit'] * amount)
    C.update()
