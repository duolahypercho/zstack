"""zstack/vehicle - renders: standard views, photo-matched views, and feature shots.

    render(run_dir, views, mode)   mode 'preview' (Workbench, seconds) or 'beauty' (Cycles)

Standard views: side_left, side_right, front, rear, top (orthographic, framed from the spec),
front34, rear34 (perspective), plus feature shots doors_open, interior, engine, crush_front,
night (lamps lit). Photo-matched views come from checks/cameras.json (written by
scripts/critique.py fit): the camera reproduces the reference photo's intrinsics and pose, so
the render can be laid over the photo.
"""
import json
import math
import os

import bpy
from mathutils import Matrix, Vector

import common as C

HDRI_DIR = os.path.join(os.path.dirname(bpy.app.binary_path), '..', 'Resources',
                        f'{bpy.app.version[0]}.{bpy.app.version[1]}', 'datafiles', 'studiolights', 'world')


def _hdri(name='courtyard.exr'):
    for base in (HDRI_DIR, os.path.join(bpy.utils.resource_path('LOCAL'), 'datafiles', 'studiolights', 'world')):
        p = os.path.normpath(os.path.join(base, name))
        if os.path.exists(p):
            return p
    return None


def stage(strength=1.0, hdri='courtyard.exr', floor=True, sun=True):
    sc = bpy.context.scene
    w = bpy.data.worlds.get('zstack_world') or bpy.data.worlds.new('zstack_world')
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.inputs['Strength'].default_value = strength
    path = _hdri(hdri)
    if path:
        env = nt.nodes.new('ShaderNodeTexEnvironment')
        env.image = bpy.data.images.load(path, check_existing=True)
        mp = nt.nodes.new('ShaderNodeMapping')
        mp.inputs['Rotation'].default_value = (0, 0, math.radians(110))
        tc = nt.nodes.new('ShaderNodeTexCoord')
        nt.links.new(tc.outputs['Generated'], mp.inputs['Vector'])
        nt.links.new(mp.outputs['Vector'], env.inputs['Vector'])
        nt.links.new(env.outputs['Color'], bg.inputs['Color'])
    else:
        bg.inputs['Color'].default_value = (0.6, 0.65, 0.7, 1)
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    if floor and not bpy.data.objects.get('_floor'):
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=30)
        fl = C.mesh_object('_floor', bm)
        m = bpy.data.materials.get('_floor') or bpy.data.materials.new('_floor')
        m.use_nodes = True
        p = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        p.inputs['Base Color'].default_value = (0.32, 0.32, 0.31, 1)
        p.inputs['Roughness'].default_value = 0.7
        fl.data.materials.append(m)
        fl['zstack'] = 'stage'
    if sun and not bpy.data.objects.get('_sun'):
        ld = bpy.data.lights.new('_sun', 'SUN')
        ld.energy = 3.0
        ld.angle = math.radians(2.5)
        s = bpy.data.objects.new('_sun', ld)
        s.rotation_euler = (math.radians(50), 0, math.radians(35))
        C.link(s)
        s['zstack'] = 'stage'


def camera(name='_cam'):
    cam = bpy.data.objects.get(name)
    if cam is None:
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        C.link(cam)
        cam['zstack'] = 'stage'
    bpy.context.scene.camera = cam
    return cam


def look_at(cam, eye, target, lens=50.0, ortho=None):
    cam.location = eye
    d = Vector(target) - Vector(eye)
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = d.to_track_quat('-Z', 'Y')
    cd = cam.data
    cd.shift_x = cd.shift_y = 0.0
    if ortho:
        cd.type = 'ORTHO'
        cd.ortho_scale = ortho
    else:
        cd.type = 'PERSP'
        cd.lens = lens
        cd.sensor_fit = 'HORIZONTAL'
        cd.sensor_width = 36.0
    cd.clip_start, cd.clip_end = 0.05, 200


def cv_camera(cam, K, R, t, size):
    """Place a Blender camera from OpenCV intrinsics K (fx, fy, cx, cy), rotation R (3x3), t."""
    w, h = size
    fx, fy, cx, cy = K
    Rm = Matrix(R)
    tv = Vector(t)
    Rw = Rm.transposed()
    cam.location = -(Rw @ tv)
    flip = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, -1)))
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = (Rw @ flip).to_quaternion()
    cd = cam.data
    cd.type = 'PERSP'
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = 36.0
    cd.lens = fx * 36.0 / w
    m = max(w, h)
    cd.shift_x = (w / 2 - cx) / m
    cd.shift_y = (cy - h / 2) / m
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.pixel_aspect_x = 1.0
    sc.render.pixel_aspect_y = fx / fy if fy else 1.0


def standard_views(spec):
    L, W, H = spec['length'], spec['width'], spec['height']
    m = max(L, W) * 1.12
    c = Vector((0, 0, H / 2))
    return {
        'side_left': dict(eye=(12, 0, H / 2), target=c, ortho=m),
        'side_right': dict(eye=(-12, 0, H / 2), target=c, ortho=m),
        'front': dict(eye=(0, -12, H / 2), target=c, ortho=W * 1.25),
        'rear': dict(eye=(0, 12, H / 2), target=c, ortho=W * 1.25),
        'top': dict(eye=(0, 0.0001, 12), target=(0, 0, 0), ortho=m),
        'front34': dict(eye=(5.2, -6.4, 1.6), target=(0, -0.2, 0.55), lens=55),
        'rear34': dict(eye=(-5.0, 6.6, 1.9), target=(0, 0.3, 0.6), lens=55),
        'doors_open': dict(eye=(5.6, -4.8, 2.4), target=(0, 0, 0.6), lens=45),
        'interior': dict(eye=(2.4, 0.6, 1.55), target=(0, -0.35, 0.72), lens=26),
        'engine': dict(eye=(1.4, 3.4, 2.6), target=(0, 1.15, 0.6), lens=40),
        'crush_front': dict(eye=(4.4, -5.6, 1.7), target=(0, -1.0, 0.5), lens=50),
        'night': dict(eye=(4.8, -6.6, 1.2), target=(0, -0.4, 0.55), lens=50),
    }


def _engine(mode, samples):
    sc = bpy.context.scene
    if mode == 'preview':
        sc.render.engine = 'BLENDER_WORKBENCH'
        sh = sc.display.shading
        sh.light = 'STUDIO'
        sh.color_type = 'MATERIAL'
        sh.show_specular_highlight = True
        sh.show_cavity = True
        return
    try:
        sc.render.engine = 'CYCLES'
    except TypeError:
        return
    cy = sc.cycles
    cy.samples = samples
    cy.use_denoising = True
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        for dt in ('METAL', 'OPTIX', 'CUDA', 'HIP', 'ONEAPI'):
            try:
                prefs.compute_device_type = dt
                prefs.get_devices()
                if any(d.type == dt for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = True
                    cy.device = 'GPU'
                    break
            except TypeError:
                continue
    except (KeyError, AttributeError):
        pass
    cy.max_bounces = 8
    cy.transmission_bounces = 8
    cy.glossy_bounces = 6
    sc.view_settings.view_transform = 'AgX' if 'AgX' in [i.identifier for i in sc.view_settings.bl_rna.properties['view_transform'].enum_items] else 'Filmic'
    sc.view_settings.look = 'None'


def render(run_dir, views=None, mode='beauty', size=(1280, 800), samples=96, pivots=None, out_dir=None):
    out_dir = out_dir or os.path.join(run_dir, 'renders')
    os.makedirs(out_dir, exist_ok=True)
    spec = json.load(open(os.path.join(run_dir, 'spec.json')))
    stage()
    _engine(mode, samples)
    sc = bpy.context.scene
    cam = camera()
    std = standard_views(spec)
    matched = {}
    cam_file = os.path.join(run_dir, 'checks', 'cameras.json')
    if os.path.exists(cam_file):
        matched = json.load(open(cam_file))
    views = views or list(std)
    written = []
    for v in views:
        sc.render.resolution_x, sc.render.resolution_y = size
        sc.render.pixel_aspect_x = sc.render.pixel_aspect_y = 1.0
        world = sc.world.node_tree.nodes.get('Background')
        if v in matched:
            m = matched[v]
            cv_camera(cam, m['K'], m['R'], m['t'], m['size'])
            sc.render.resolution_percentage = 100 if max(m['size']) <= 1600 else int(160000 / max(m['size']))
        elif v in std:
            sc.render.resolution_percentage = 100
            look_at(cam, **std[v])
        else:
            print('unknown view', v)
            continue
        _feature(v, pivots, True, world)
        path = os.path.join(out_dir, f'{v}.png')
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        _feature(v, pivots, False, world)
        written.append(path)
        print('rendered', path)
    return written


def _feature(view, pivots, on, world):
    """Pose the car for feature shots, and undo afterwards."""
    import rig
    if view in ('doors_open', 'interior', 'engine') and pivots:
        sel = {k: p for k, p in pivots.items() if (view != 'engine' or 'Cover' in k or 'Trunk' in k)
               and (view != 'interior' or k.startswith('Door'))}
        rig.pose(sel, 1.0 if on else 0.0)
    if view == 'crush_front':
        for ob in bpy.data.objects:
            if ob.type == 'MESH' and ob.data.shape_keys:
                kb = ob.data.shape_keys.key_blocks.get('Crush_Front')
                if kb:
                    kb.value = 1.0 if on else 0.0
    if view == 'night' and world is not None:
        world.inputs['Strength'].default_value = 0.04 if on else 1.0
        sun = bpy.data.objects.get('_sun')
        if sun:
            sun.hide_render = on
