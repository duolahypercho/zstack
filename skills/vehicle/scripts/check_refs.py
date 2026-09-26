#!/usr/bin/env python3
"""zstack/vehicle - check that a reference set is usable before anything is built.

    python3 check_refs.py <run> [--refs DIR] [--min-margin 8]

The critique calibrates pixels to metres by thresholding each view against its own
border colour, and scores the silhouette through that same mask. So a reference
only works if the car is the one thing that differs from the background. This
script checks exactly that, and it is the gate that decides whether an image
model's output can be modelled from or has to be rejected and regenerated.

For every view in <refs>/views.json with "kind": "ortho":

  background   flat enough that one border colour describes it (a gradient or a
               vignette breaks the threshold and the mask swallows the studio)
  margin       the car does not touch the frame edge, and keeps at least
               --min-margin px of clear background on every side
  foreground   the car is a plausible fraction of the frame, not 5% and not 80%
  aspect       the measured length:height matches the spec within 3% (calibrate.py
               re-checks this; failing here means the image drew a different car)

Writes <refs>/../critique/refcheck_<view>.jpg previews of each mask, and prints
one line per view. Exit 1 if any view must be regenerated. Fix the image, never
the threshold.
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import critique  # noqa: E402

ASPECT_TOL = 0.03
FG_MIN = 0.04
FG_MAX = 0.55
# A flat seamless sweep has almost no spread across its border. A studio gradient,
# a vignette or a sweep falloff gives tens of levels; measured on a rejected
# generated view the border ring spread 18 levels against 1-2 for a flat sweep.
BG_STD_MAX = 6.0


def extents(spec, axis):
    L, W, H = spec['length'], spec['width'], spec['height']
    return {'side_left': (L, H), 'side_right': (L, H), 'front': (W, H), 'rear': (W, H), 'top': (W, L)}[axis]


def main(argv):
    cmd = argv[0] if argv else 'check_refs'
    min_margin = 8
    rest = []
    # critique.args() reads argv[0] as the subcommand and argv[1] as the run directory, so a
    # caller that already supplies a subcommand is honoured, and a direct call still works.
    i = 1 if len(argv) > 1 and not argv[1].startswith('-') and os.path.isdir(argv[1] or '.') else 0
    while i < len(argv):
        if argv[i] == '--min-margin' and i + 1 < len(argv):
            min_margin = int(argv[i + 1])
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    # critique.args() reads argv[0] as the subcommand and argv[1] as the run directory.
    a = critique.args([cmd] + rest)
    os.makedirs(a['private'], exist_ok=True)
    spec = json.load(open(os.path.join(a['run'], 'spec.json')))
    views = critique.load_views(a)
    os.makedirs(a['private'], exist_ok=True)
    fails = []
    for name, v in views.items():
        if v.get('kind') != 'ortho':
            continue
        axis = v['axis']
        img, m = critique.photo_mask(a, name, v)
        h, w = m.shape
        border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).astype(np.float32)
        bg = np.median(border, axis=0)
        bg_std = float(np.linalg.norm(border - bg, axis=1).mean())
        ys, xs = np.where(m)
        if len(xs) == 0:
            print(f'{name:12s} {axis:10s} FAIL  no car found against the background')
            fails.append(name)
            continue
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        margins = [x0, y0, w - 1 - x1, h - 1 - y1]
        touching = min(margins) <= 0
        fg = float(m.mean())
        wm, hm = extents(spec, axis)
        sx, sy = (x1 - x0) / wm, (y1 - y0) / hm
        aspect_err = abs(sx / sy - 1)
        why = []
        if bg_std > BG_STD_MAX:
            why.append(f'background is not flat (spread {bg_std:.1f} > {BG_STD_MAX:.1f})')
        if touching:
            why.append(f'car touches the frame edge (margins L{x0} T{y0} R{w - 1 - x1} B{h - 1 - y1})')
        elif min(margins) < min_margin:
            why.append(f'only {min(margins)} px of margin (need {min_margin})')
        if fg < FG_MIN or fg > FG_MAX:
            why.append(f'car covers {fg * 100:.0f}% of the frame (want {FG_MIN * 100:.0f}-{FG_MAX * 100:.0f}%)')
        if aspect_err > ASPECT_TOL:
            why.append(f'aspect is {aspect_err * 100:.1f}% off the spec (image drew a different car)')
        preview = np.dstack([(m * 255).astype(np.uint8)] * 3)
        cv2.imwrite(os.path.join(a['private'], f'refcheck_{name}.jpg'), preview)
        detail = f'bg {bg_std:4.1f}  margin {min(margins):3d}  fg {fg * 100:3.0f}%  aspect {aspect_err * 100:4.1f}%'
        if why:
            print(f'{name:12s} {axis:10s} REGENERATE  {detail}')
            for w_ in why:
                print(f'{"":24s}- {w_}')
            fails.append(name)
        else:
            print(f'{name:12s} {axis:10s} ok           {detail}')
    if fails:
        print('\nregenerate these views: ' + ', '.join(fails))
        return 1
    print('\nall orthographic references are usable')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
