"""zstack/vehicle - prove every opening part opens cleanly.

Run inside Blender (MCP execute_blender_code, or `blender -b file.blend -P door_check.py -- out.json`).
For every pivot carrying `hingeAxis` / `openSign` / `openLimit` (see reference/rig-contract.md):

  1. hinge on the panel   the pivot lies within HINGE_TOL of the part's own geometry
  2. opens outward/upward  from the first step the part's centroid moves out (+-X by side) or up
  3. no clash             at no step does the part intersect anything that it did not
                          already touch when shut (body, wheels, other doors, interior)
  4. returns home          the pose at 0 equals the rest pose (rig is not accumulating)

Writes a JSON report and returns it; `pass` is false if any part fails.
Thresholds are part of the contract - fix the model or the rig, never the numbers.
"""
import json
import math
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

STEPS = 24
HINGE_TOL = 0.03       # m: pivot to nearest vertex of the part it moves
OUT_TOL = 0.002        # m: minimum outward/upward travel at the first step


def descendants(ob):
    out = []
    for c in ob.children:
        out.append(c)
        out += descendants(c)
    return out


def world_bvh(objs, dg, owners=None):
    verts, polys, off = [], [], 0
    for o in objs:
        if o.type != 'MESH':
            continue
        me = o.evaluated_get(dg).to_mesh()
        mw = o.matrix_world
        verts += [mw @ v.co for v in me.vertices]
        polys += [[off + i for i in p.vertices] for p in me.polygons]
        if owners is not None:
            owners += [o.name] * len(me.polygons)
        off += len(me.vertices)
        o.evaluated_get(dg).to_mesh_clear()
    if not polys:
        return None, []
    return BVHTree.FromPolygons(verts, polys, epsilon=0.0), verts


def check(report_path=None, objects=None):
    """`objects`: restrict to these (e.g. a re-imported scene); default every object in the file."""
    dg = bpy.context.evaluated_depsgraph_get()
    pool = list(objects) if objects is not None else list(bpy.data.objects)
    pivots = [o for o in pool if 'hingeAxis' in o and 'openLimit' in o]
    meshes = [o for o in pool if o.type == 'MESH' and not o.hide_get() and o.get('zstack') != 'stage']
    report = {'pass': True, 'steps': STEPS, 'parts': {}}
    for pv in pivots:
        moving = [o for o in descendants(pv) if o.type == 'MESH']
        static = [o for o in meshes if o not in moving]
        axis = Vector(pv['hingeAxis']).normalized()
        sign = pv.get('openSign', 1)
        limit = pv['openLimit']
        rest = pv.matrix_basis.copy()
        side = 1 if pv.matrix_world.translation.x > 0.05 else (-1 if pv.matrix_world.translation.x < -0.05 else 0)
        rec = {'type': pv.get('doorType', '?'), 'fail': []}
        bpy.context.view_layer.update()
        owners = []
        static_bvh, _ = world_bvh(static, dg, owners)
        mv_bvh, mv_verts = world_bvh(moving, dg)
        if mv_bvh is None:
            rec['fail'].append('pivot moves no geometry')
            report['parts'][pv.name] = rec
            report['pass'] = False
            continue
        # 1. hinge on the panel
        p = pv.matrix_world.translation
        near = min((v - p).length for v in mv_verts)
        rec['hingeToPanelM'] = round(near, 4)
        if near > HINGE_TOL:
            rec['fail'].append(f'floating hinge: pivot {near:.3f} m from its panel')
        c0 = sum(mv_verts, Vector()) / len(mv_verts)
        base = set(static_bvh.overlap(mv_bvh)) if static_bvh else set()
        worst, travel, prev_out = 0, [], None
        for s in range(STEPS + 1):
            ang = sign * limit * s / STEPS
            pv.matrix_basis = rest @ Quaternion(rest.to_quaternion().inverted() @ axis, ang).to_matrix().to_4x4()
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            mv_owners = []
            bvh, verts = world_bvh(moving, dg, mv_owners)
            c = sum(verts, Vector()) / len(verts)
            d = c - c0
            out = d.x * side + max(d.z, 0) if side else d.z
            travel.append(round(out, 4))
            if s == 1 and out < OUT_TOL:
                rec['fail'].append(f'opens inward: first-step outward travel {out:.4f} m (check openSign / hingeAxis)')
            if s > 0 and static_bvh:
                hits = set(static_bvh.overlap(bvh)) - base
                if len(hits) > worst:
                    worst = len(hits)
                    rec['worstClashStep'] = s
                    rec['clashWith'] = sorted({owners[a] for a, _ in hits})
                    rec['clashBy'] = sorted({mv_owners[b] for _, b in hits})
            prev_out = out
        pv.matrix_basis = rest
        bpy.context.view_layer.update()
        # 4. returns home
        _, back = world_bvh(moving, bpy.context.evaluated_depsgraph_get())
        if (sum(back, Vector()) / len(back) - c0).length > 1e-4:
            rec['fail'].append('rest pose not restored')
        rec['clashFaces'] = worst
        if worst > 0:
            rec['fail'].append(f'clash: {worst} face pairs intersect during the swing (step {rec.get("worstClashStep")})')
        rec['outwardTravelM'] = travel[::6] + [travel[-1]]
        rec['pass'] = not rec['fail']
        report['parts'][pv.name] = rec
        report['pass'] = report['pass'] and rec['pass']
    if not pivots:
        report['pass'] = False
        report['error'] = 'no hinged pivots found (hingeAxis/openLimit custom properties)'
    if report_path:
        with open(report_path, 'w') as fh:
            json.dump(report, fh, indent=2)
    return report


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    r = check(argv[0] if argv else None)
    print(json.dumps({k: (v['pass'], v['fail']) for k, v in r['parts'].items()}, indent=1))
    print('DOORS', 'PASS' if r['pass'] else 'FAIL')
