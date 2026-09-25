"""zstack/vehicle - engine bay and cooling fans, in real metals.

parts.json "engine":
    {"layout": "v8", "y": [0.78, 1.52], "crankZ": 0.42, "bay": {"y": [0.60, 1.95], "halfWidth": 0.72, "z": [0.26, 0.98]},
     "fans": [{"at": [0.80, 1.00, 0.62], "r": 0.15, "normal": [1, 0, 0]}]}

Makes Engine_* meshes (cast block, brushed valve covers with fins, polished intake plenum and
runners, strut brace, exhaust headers) inside an EngineBay tub, and Fan_* assemblies (metal
blades in a shroud) that sit behind intakes. Everything is tagged zstack='engine'.
"""
import math

import bmesh
from mathutils import Matrix, Vector

import common as C


def fan(name, centre, normal, r, mats, coll=None, blades=9):
    """Shrouded axial fan: brushed shroud ring, pitched metal blades, cast hub."""
    objs = []
    shroud = C.revolve(name + '_Shroud', [(r + 0.012, -0.03), (r + 0.004, -0.03), (r + 0.004, 0.03), (r + 0.018, 0.035),
                                          (r + 0.018, 0.03), (r + 0.012, 0.03)], 64, 'Z', coll)
    C.set_material(shroud, mats['Metal_Brushed'])
    objs.append(shroud)
    hub = C.revolve(name + '_Hub', [(0.0, 0.02), (r * 0.18, 0.018), (r * 0.22, 0.0), (r * 0.22, -0.02), (0.0, -0.02)], 32, 'Z', coll)
    C.set_material(hub, mats['Metal_Cast'])
    objs.append(hub)
    bm = bmesh.new()
    for i in range(blades):
        a0 = 2 * math.pi * i / blades
        rows = []
        for k in range(6):
            rr = r * 0.2 + (r * 0.97 - r * 0.2) * k / 5
            sweep = 0.35 * (k / 5) ** 1.5
            chord = 0.55 / blades * 2 * math.pi * (0.8 + 0.4 * k / 5)
            pitch = math.radians(38 - 16 * k / 5)
            row = []
            for s in (-0.5, 0.5):
                a = a0 + sweep + s * chord * 0.5
                z = s * chord * rr * math.tan(pitch) * 0.5
                row.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), z)))
            rows.append(row)
        for ra, rb in zip(rows, rows[1:]):
            bm.faces.new((ra[0], ra[1], rb[1], rb[0]))
    blade_ob = C.mesh_object(name + '_Blades', bm, coll=coll, smooth=True)
    sol = blade_ob.modifiers.new('t', 'SOLIDIFY')
    sol.thickness = 0.003
    C.apply_modifiers(blade_ob)
    C.set_material(blade_ob, mats['Metal_Brushed'])
    objs.append(blade_ob)
    guard = []
    for k in range(1, 4):
        ring = [(r * k / 3.2 * math.cos(2 * math.pi * j / 40), r * k / 3.2 * math.sin(2 * math.pi * j / 40), 0.034) for j in range(40)]
        g = C.tube(f'{name}_Guard{k}', ring, 0.0022, 6, coll, closed=True)
        guard.append(g)
    for j in range(6):
        a = 2 * math.pi * j / 6
        guard.append(C.tube(f'{name}_Strut{j}', [(0, 0, 0.034), (r * math.cos(a), r * math.sin(a), 0.034)], 0.0025, 6, coll))
    for g in guard:
        C.set_material(g, mats['Trim_Chrome'])
    objs += guard
    q = Vector((0, 0, 1)).rotation_difference(Vector(normal).normalized())
    for o in objs:
        o.data.transform(q.to_matrix().to_4x4())
        o.location = centre
        C.apply_transform(o)
        o['zstack'] = 'engine'
    return objs


def _finned_cover(name, y0, y1, width, height, fins, coll=None):
    """Valve cover: rounded block with raised longitudinal fins (in local frame, bank axis = Y)."""
    body = C.rounded_box(name, (width, y1 - y0, height), (0, (y0 + y1) / 2, height / 2), 0.02, 3, coll)
    bm = bmesh.new()
    bm.from_mesh(body.data)
    for i in range(fins):
        x = -width / 2 + width * (i + 1) / (fins + 1)
        r = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector((0.006, (y1 - y0) * 0.86, 0.018)), verts=r['verts'])
        bmesh.ops.translate(bm, vec=Vector((x, (y0 + y1) / 2, height + 0.008)), verts=r['verts'])
    bm.to_mesh(body.data)
    bm.free()
    return body


def build(eng, mats, coll=None):
    objs = []
    y0, y1 = eng['y']
    cz = eng.get('crankZ', 0.42)
    bay = eng.get('bay')
    if bay:
        by0, by1 = bay['y']
        bz0, bz1 = bay['z']
        hw = bay['halfWidth']
        t = 0.012
        for n, size, ctr in (
                ('EngineBay_Floor', (2 * hw, by1 - by0, t), (0, (by0 + by1) / 2, bz0)),
                ('EngineBay_Front', (2 * hw, t, bz1 - bz0), (0, by0, (bz0 + bz1) / 2)),
                ('EngineBay_Rear', (2 * hw, t, bz1 - bz0), (0, by1, (bz0 + bz1) / 2)),
                ('EngineBay_L', (t, by1 - by0, bz1 - bz0), (hw, (by0 + by1) / 2, (bz0 + bz1) / 2)),
                ('EngineBay_R', (t, by1 - by0, bz1 - bz0), (-hw, (by0 + by1) / 2, (bz0 + bz1) / 2))):
            o = C.rounded_box(n, size, ctr, 0.004, 1, coll)
            C.set_material(o, mats['Trim_Satin'])
            objs.append(o)
    # crankcase / block (cast), sump
    block = C.rounded_box('Engine_Block', (0.46, y1 - y0, 0.26), (0, (y0 + y1) / 2, cz), 0.03, 3, coll)
    C.set_material(block, mats['Metal_Cast'])
    sump = C.rounded_box('Engine_Sump', (0.36, (y1 - y0) * 0.8, 0.10), (0, (y0 + y1) / 2, cz - 0.17), 0.02, 2, coll)
    C.set_material(sump, mats['Metal_Dark'])
    objs += [block, sump]
    # two banks at +-45 deg, each with a finned brushed valve cover
    for side in (1, -1):
        bank = C.rounded_box(f'Engine_Head_{"L" if side > 0 else "R"}', (0.20, y1 - y0 - 0.02, 0.20), (0, (y0 + y1) / 2, 0.10), 0.02, 2, coll)
        cover = _finned_cover(f'Engine_ValveCover_{"L" if side > 0 else "R"}', y0 + 0.02, y1 - 0.03, 0.17, 0.06, 5, coll)
        cover.data.transform(Matrix.Translation((0, 0, 0.20)))
        for o, m in ((bank, 'Metal_Cast'), (cover, 'Metal_Brushed')):
            o.data.transform(Matrix.Rotation(-side * math.radians(45), 4, 'Y'))
            o.data.transform(Matrix.Translation((side * 0.12, 0, cz + 0.08)))
            C.set_material(o, mats[m])
            objs.append(o)
        # exhaust headers: four polished primaries per bank sweeping down and out
        for k in range(4):
            yy = y0 + 0.08 + (y1 - y0 - 0.16) * k / 3
            pts = [(side * 0.30, yy, cz + 0.16), (side * 0.40, yy + 0.02, cz + 0.08), (side * 0.44, yy + 0.06, cz - 0.05),
                   (side * 0.40, y1 + 0.10, cz - 0.12)]
            h = C.tube(f'Engine_Header_{side}_{k}', pts, 0.02, 10, coll)
            C.set_material(h, mats['Metal_Brushed'])
            objs.append(h)
    # intake plenum in the valley (polished), eight runners, twin throttle body
    plen = C.rounded_box('Engine_Plenum', (0.16, (y1 - y0) * 0.9, 0.10), (0, (y0 + y1) / 2, cz + 0.32), 0.04, 4, coll)
    C.set_material(plen, mats['Trim_Chrome'])
    objs.append(plen)
    for side in (1, -1):
        for k in range(4):
            yy = y0 + 0.10 + (y1 - y0 - 0.2) * k / 3
            r = C.tube(f'Engine_Runner_{side}_{k}', [(side * 0.06, yy, cz + 0.33), (side * 0.13, yy, cz + 0.36), (side * 0.19, yy, cz + 0.30)],
                       0.018, 10, coll)
            C.set_material(r, mats['Metal_Brushed'])
            objs.append(r)
    tb = C.revolve('Engine_Throttle', [(0.045, 0.0), (0.05, 0.01), (0.05, 0.08), (0.04, 0.085)], 32, 'Y', coll)
    tb.location = (0, y0 - 0.05, cz + 0.32)
    C.apply_transform(tb)
    C.set_material(tb, mats['Metal_Brushed'])
    objs.append(tb)
    # strut brace across the bay (polished bar with cast end plates)
    if bay:
        bz = bay['z'][1] - 0.06
        brace = C.tube('Engine_StrutBrace', [(-hw + 0.06, (y0 + y1) / 2, bz), (hw - 0.06, (y0 + y1) / 2, bz)], 0.018, 16, coll)
        C.set_material(brace, mats['Trim_Chrome'])
        objs.append(brace)
    # accessory drive at the front of the block
    for k, (x, z, r) in enumerate(((0.0, cz - 0.02, 0.09), (0.14, cz + 0.12, 0.05), (-0.14, cz + 0.10, 0.05))):
        p = C.revolve(f'Engine_Pulley{k}', [(0.0, -0.02), (r, -0.02), (r, 0.02), (0.0, 0.02)], 32, 'Y', coll)
        p.location = (x, y0 - 0.03, z)
        C.apply_transform(p)
        C.set_material(p, mats['Metal_Brushed'])
        objs.append(p)
    for f in eng.get('fans', []):
        objs += fan(f.get('name', f'Fan{len(objs)}'), f['at'], f['normal'], f['r'], mats, coll)
        if f.get('mirror'):
            at = [-f['at'][0], f['at'][1], f['at'][2]]
            nm = [-f['normal'][0], f['normal'][1], f['normal'][2]]
            objs += fan(f.get('name', 'Fan') + '_R', at, nm, f['r'], mats, coll)
    for o in objs:
        o['zstack'] = 'engine'
    return objs
