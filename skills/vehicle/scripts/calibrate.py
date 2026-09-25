#!/usr/bin/env python3
"""zstack/vehicle - calibrate orthographic reference views to real dimensions.

    python3 calibrate.py <run> [--refs DIR]

For every view in <refs>/views.json with "kind": "ortho" and "axis" one of
side_left, side_right, front, rear, top: finds the car (plain background -> threshold),
measures its pixel extents, maps them to metres with spec.json (side: length x height,
front/rear: width x height, top: length x width) and writes

    <run>/blueprint.json        per view: px per metre, origin pixel, aspect check
    <run>/checks/cameras.json   orthographic cameras for critique.py score

A view whose aspect ratio disagrees with the spec by more than 3% is flagged: the image model
drew a different car. Regenerate or re-crop that view; never stretch it to fit.
Photos (kind "photo") are calibrated by critique.py fit instead.
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import critique  # noqa: E402

TOL = 0.03
# axis -> (image-right axis, image-up axis) in the Blender frame (+X left, -Y forward, +Z up)
AXES = {
    'side_left': ('-y', 'z'),     # camera at +X looking at the car's left side: nose on the right
    'side_right': ('y', 'z'),
    'front': ('x', 'z'),          # camera ahead of the car: the car's left (+X) on image right
    'rear': ('-x', 'z'),
    'top': ('-x', '-y'),          # camera above, nose up the image
}


def extents(spec, axis):
    L, W, H = spec['length'], spec['width'], spec['height']
    return {'side_left': (L, H), 'side_right': (L, H), 'front': (W, H), 'rear': (W, H), 'top': (W, L)}[axis]


def main(argv):
    a = critique.args(['calibrate'] + argv)
    spec = json.load(open(os.path.join(a['run'], 'spec.json')))
    views = critique.load_views(a)
    cam_path = os.path.join(a['run'], 'checks', 'cameras.json')
    cams = json.load(open(cam_path)) if os.path.exists(cam_path) else {}
    bp, bad = {}, []
    for name, v in views.items():
        if v.get('kind') != 'ortho':
            continue
        axis = v['axis']
        _, m = critique.photo_mask(a, name, v)
        ys, xs = np.where(m)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        wm, hm = extents(spec, axis)
        sx, sy = (x1 - x0) / wm, (y1 - y0) / hm
        aspect_err = abs(sx / sy - 1)
        # origin: the pixel of the car's centre in the image plane; ground (z = 0) at the bottom edge
        if axis == 'top':
            origin = [(x0 + x1) / 2, (y0 + y1) / 2]
        else:
            origin = [(x0 + x1) / 2, float(y1)]
        s = (sx + sy) / 2
        bp[name] = {'axis': axis, 'pxPerM': round(float(s), 3), 'pxPerM_xy': [round(float(sx), 3), round(float(sy), 3)],
                    'origin': [float(origin[0]), float(origin[1])], 'bboxPx': [int(x0), int(y0), int(x1), int(y1)],
                    'aspectError': round(float(aspect_err), 4), 'ok': bool(aspect_err <= TOL)}
        cams[name] = {'ortho': True, 'axis': axis, 'pxPerM': float(s), 'origin': [float(origin[0]), float(origin[1])],
                      'size': [int(m.shape[1]), int(m.shape[0])], 'anchorErrPx': 0.0}
        print(f"{name:10s} {axis:10s} {s:8.2f} px/m  aspect error {aspect_err * 100:5.2f}%  {'ok' if aspect_err <= TOL else 'REGENERATE'}")
        if aspect_err > TOL:
            bad.append(name)
    json.dump(bp, open(os.path.join(a['run'], 'blueprint.json'), 'w'), indent=1)
    json.dump(cams, open(cam_path, 'w'), indent=1)
    if bad:
        print('views that disagree with the spec:', ', '.join(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
