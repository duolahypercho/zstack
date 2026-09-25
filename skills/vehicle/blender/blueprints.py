"""zstack/vehicle - load calibrated orthographic references into Blender as modelling underlays.

    import blueprints; blueprints.load('runs/<id>', refs='runs/<id>/refs')

Reads <run>/blueprint.json (written by scripts/calibrate.py) and <refs>/views.json, and places
each view as an image empty in metres: side views on the YZ plane, front/rear on XZ, top on XY,
ground at z = 0, nose toward -Y. Underlays are tagged zstack='stage' so exports skip them.
Photo references are not placed here: use the fitted cameras (checks/cameras.json) instead, which
render_views.py applies as matched cameras.
"""
import json
import math
import os

import bpy

import common as C

# axis -> (rotation euler, offset of the image plane from the car centre)
PLACE = {
    'side_left': ((math.pi / 2, 0, math.pi / 2), (2.5, 0, 0)),
    'side_right': ((math.pi / 2, 0, -math.pi / 2), (-2.5, 0, 0)),
    'front': ((math.pi / 2, 0, 0), (0, -3.5, 0)),
    'rear': ((math.pi / 2, 0, math.pi), (0, 3.5, 0)),
    'top': ((0, 0, 0), (0, 0, -0.02)),
}


def load(run, refs=None, opacity=0.5):
    refs = refs or os.path.join(run, 'refs')
    bp = json.load(open(os.path.join(run, 'blueprint.json')))
    views = json.load(open(os.path.join(refs, 'views.json')))
    out = []
    for name, b in bp.items():
        v = views[name]
        img = bpy.data.images.load(os.path.join(refs, v['image']), check_existing=True)
        w, h = img.size
        s = b['pxPerM']
        e = bpy.data.objects.new(f'Blueprint_{name}', None)
        e.empty_display_type = 'IMAGE'
        e.data = img
        e.empty_display_size = max(w, h) / s              # image long side in metres
        e.use_empty_image_alpha = True
        e.color[3] = opacity
        rot, off = PLACE[b['axis']]
        e.rotation_euler = rot
        # shift so the calibrated origin pixel lands on the car's origin (ground / centre)
        ou, ov = b['origin']
        e.empty_image_offset = (-ou / max(w, h), -(h - ov) / max(w, h))
        e.location = off
        e['zstack'] = 'stage'
        C.link(e)
        out.append(e)
    return out
