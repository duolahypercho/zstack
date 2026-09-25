"""zstack/vehicle - export the rigged car to GLB, then prove the export on a re-import.

    export(run_dir, vid) -> report dict

1. Hinge metadata is also written in the glTF frame (`hingeAxisGltf`: Blender (x, y, z) ->
   glTF (x, z, -y)) so an engine can use it without knowing Blender's axes.
2. Exports GLB with extras, morph targets (the Crush_* keys) and the node hierarchy.
3. Re-imports the GLB into a scratch scene and re-runs the door check there: the file that ships
   is the file that is proven, not the .blend it came from.
"""
import json
import os

import bpy

import common as C


def silhouette_tris(path, include=None, ratio=None):
    """World-space triangles of every visible car mesh, for scripts/critique.py.
    ratio: decimate each mesh to this fraction first (a light copy for the camera search)."""
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    V, F, off = [], [], 0
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or ob.get('zstack') in ('stage', 'interior', 'engine') or ob.hide_render:
            continue
        if include and not include(ob):
            continue
        md = None
        if ratio and len(ob.data.polygons) > 400:
            md = ob.modifiers.new('_sil', 'DECIMATE')
            md.ratio = ratio
            dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        mw = ob.matrix_world
        V += [tuple(mw @ v.co) for v in me.vertices]
        F += [tuple(off + i for i in t.vertices) for t in me.loop_triangles]
        off += len(me.vertices)
        ev.to_mesh_clear()
        if md:
            ob.modifiers.remove(md)
    np.savez_compressed(path, verts=np.array(V, dtype=np.float32), faces=np.array(F, dtype=np.int32))
    return len(F)


def export(run_dir, vid, pivots=None):
    for ob in bpy.data.objects:
        if 'hingeAxis' in ob:
            x, y, z = ob['hingeAxis']
            ob['hingeAxisGltf'] = [x, z, -y]
        if 'spinAxis' in ob:
            x, y, z = ob['spinAxis']
            ob['spinAxisGltf'] = [x, z, -y]
    stage_objs = [o for o in bpy.data.objects if o.get('zstack') == 'stage']
    for o in stage_objs:
        o.hide_set(True)
        o.hide_render = True
    path = os.path.join(run_dir, f'{vid}.glb')
    for o in bpy.data.objects:
        o.select_set(o.get('zstack') != 'stage')
    kw = dict(filepath=path, export_format='GLB', use_selection=True, export_extras=True,
              export_yup=True, export_apply=False, export_morph=True, export_morph_normal=True,
              export_animations=False)
    try:
        bpy.ops.export_scene.gltf(**kw)
    except TypeError:
        kw.pop('export_morph_normal', None)
        bpy.ops.export_scene.gltf(**kw)
    for o in stage_objs:
        o.hide_set(False)
        o.hide_render = False
    size = os.path.getsize(path)
    rep = {'glb': os.path.basename(path), 'bytes': size, 'triangles': C.tri_count([o for o in bpy.data.objects if o.get('zstack') != 'stage'])}
    # round trip: import into a scratch scene and prove the doors again
    main = bpy.context.window.scene if bpy.context.window else bpy.context.scene
    scratch = bpy.data.scenes.new('_reimport')
    win = bpy.context.window
    if win:
        win.scene = scratch
    else:
        bpy.context.window_manager.windows  # background: operate via override below
    with bpy.context.temp_override(scene=scratch):
        bpy.ops.import_scene.gltf(filepath=path)
        import door_check
        imported = [o for o in scratch.objects]
        keyed = [o.name for o in imported if o.type == 'MESH' and o.data.shape_keys
                 and any(k.name.startswith('Crush_') for k in o.data.shape_keys.key_blocks)]
        # door_check scans bpy.data.objects; restrict it to the scratch scene's objects
        r = door_check.check(os.path.join(run_dir, 'checks', 'doors-reimport.json'), objects=imported)
    rep['reimport'] = {'objects': len(imported), 'crushKeyedMeshes': len(keyed), 'doorsPass': r['pass'],
                       'pivots': sorted(r['parts'])}
    if win:
        win.scene = main
    for o in list(scratch.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.scenes.remove(scratch)
    return rep
