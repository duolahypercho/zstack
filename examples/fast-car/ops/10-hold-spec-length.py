# hold the spec length: the subdivided nose / tail tips overshoot the published length; move each
# end cap (and its ring) so the surface ends exactly at -length/2 and +length/2
import json, os
spec = json.load(open(os.path.join(RUN, 'spec.json')))
half = spec['length'] / 2
for _ in range(3):
    ob = bpy.data.objects[K.CAGE]
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg); me = ev.to_mesh()
    ys = [v.co.y for v in me.vertices]; lo, hi = min(ys), max(ys); ev.to_mesh_clear()
    front, back = lo + half, hi - half          # >0 front: short; <0 front: overshoot
    def ends(bm, front=front, back=back):
        lr = bm.verts.layers.int['cage_row']
        last = max(v[lr] for v in bm.verts)
        for v in bm.verts:
            if v[lr] in (-1, 0):
                v.co.y -= front
            elif v[lr] in (-2, last):
                v.co.y -= back
    K.edit_cage(ends)
    print(json.dumps({'hold_length_mm': [round(front * 1000, 1), round(back * 1000, 1)]}))
