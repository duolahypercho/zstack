#!/usr/bin/env python3
"""zstack/vehicle - score the model against the reference views, view by view.

    python3 critique.py mask  <run> [--refs DIR]          photo -> car mask (GrabCut), preview overlays
    python3 critique.py fit   <run> [--refs DIR]          solve each reference's camera -> checks/cameras.json
    python3 critique.py suggest <run> [--refs DIR] [--views v]
                                                          per-station top/bottom corrections (metres),
                                                          pooled over every view -> checks/suggest.json
    python3 critique.py apply <run> [--gain 0.7] [--edges top]
                                                          move curves.json control points by the damped
                                                          suggestions (backup: curves.round<n>.json)
    python3 critique.py fitshape <run> [--refs DIR] [--minutes 20] [--joint] [--densify 20] [--start curves.json]
                                                          fit every body-curve control point to all views
                                                          at once (spec length/width/height are hard limits)
    python3 critique.py trace <run> [--refs DIR]          back-project traced feature lines -> checks/traces.json
    python3 critique.py score <run> [--refs DIR] [--note "what changed"]
                                                          IoU per view, diff sheets, worst regions,
                                                          appends a round to checks/rounds.json

Needs numpy, opencv-python, scipy. Inputs:
  <run>/spec.json
  <run>/checks/model_tris.npz          written by blender/build.py (world triangles, Blender frame)
  <refs>/views.json                    one entry per reference view (default <refs> = <run>/refs):
    {"street": {"image": "photo.jpg",
                "kind": "photo" | "ortho",
                "bbox": [x0, y0, x1, y1],                  car's box, seeds the mask
                "anchors": {"hub_FR": [u, v], "ground_FR": [u, v], "hub_RR": [u, v], ...},
                "maskFix": {"add": [[[u, v], ...]], "sub": [[[u, v], ...]]},   optional polygons
                "groundLine": [[u, v], [u, v]]}}           optional: drop mask below this line
  "ignore": [[[u, v], ...]] + "ignoreReason": "<why>" drops those pixels from scoring for both the
  model and the photo (part of the reference shows a different body, e.g. a convertible's deck).
  A view with "skip": "<reason>" is excluded from fitting and scoring and the reason is printed
  (use it for a reference that shows a different body than the one being built; never to hide
  a view that simply scores badly).
  Anchors name real, spec-known points per visible wheel XX: hub_XX (outer centre), ground_XX (tyre
  contact point), tyreFront_XX / tyreRear_XX (the tyre's fore and aft extremes at hub height).
  Two wheels with all four points each fix a camera well; hub + ground alone leave the yaw loose.

Outputs that contain the photos (masks, sheets) go to <refs>/../critique/, never into <run>:
reference photos stay private. cameras.json and rounds.json hold only numbers.

Gates (SKILL.md step 7): perspective/photo views IoU >= 0.90, orthographic views >= 0.95.
"""
import json
import math
import os
import sys
import time

import cv2
import numpy as np
from scipy.optimize import minimize

GATE = {'photo': 0.90, 'ortho': 0.95}
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK = 520          # long side of the working raster, px


# ---------------------------------------------------------------- io

def args(argv):
    out = {'cmd': argv[0], 'run': os.path.abspath(argv[1]), 'refs': None, 'note': '', 'views': None,
           'gain': 0.7, 'edges': ['top']}
    i = 2
    while i < len(argv):
        if argv[i] == '--refs':
            out['refs'] = os.path.abspath(argv[i + 1])
        elif argv[i] == '--note':
            out['note'] = argv[i + 1]
        elif argv[i] == '--views':
            out['views'] = argv[i + 1].split(',')
        elif argv[i] == '--joint':
            out['joint'] = True
            i -= 1
        elif argv[i] == '--include-skipped':
            out['includeSkipped'] = True
            i -= 1
        elif argv[i] == '--smooth-first':
            out['smoothFirst'] = int(argv[i + 1])
        elif argv[i] == '--smooth-weight':
            out['smoothWeight'] = float(argv[i + 1])
        elif argv[i] == '--trust':
            out['trust'] = float(argv[i + 1])
        elif argv[i] == '--start':
            out['start'] = os.path.abspath(argv[i + 1])
        elif argv[i] == '--densify':
            out['densify'] = int(argv[i + 1])
        elif argv[i] == '--minutes':
            out['minutes'] = float(argv[i + 1])
        elif argv[i] == '--gain':
            out['gain'] = float(argv[i + 1])
        elif argv[i] == '--edges':
            out['edges'] = argv[i + 1].split(',')
        i += 2
    out['refs'] = out['refs'] or os.path.join(out['run'], 'refs')
    out['private'] = os.path.join(os.path.dirname(out['refs'].rstrip('/')), 'critique')
    if os.path.isdir(out['refs']):          # private outputs live beside the private refs only
        os.makedirs(out['private'], exist_ok=True)
    os.makedirs(os.path.join(out['run'], 'checks'), exist_ok=True)
    return out


def load_views(a, include_skipped=False):
    views = json.load(open(os.path.join(a['refs'], 'views.json')))
    skipped = {k: v['skip'] for k, v in views.items() if v.get('skip')}
    if skipped and not include_skipped:
        for k, why in skipped.items():
            print(f'skipping {k}: {why}')
        views = {k: v for k, v in views.items() if not v.get('skip')}
    if a['views']:
        views = {k: v for k, v in views.items() if k in a['views']}
    return views


def wheels3d(spec):
    """Per corner: hub point, and the tyre's two sidewall circles (centre, radius, x planes)."""
    L = spec['length']
    yf = -L / 2 + spec['frontOverhang']
    yr = yf + spec['wheelbase']
    out = {}
    for c in ('FL', 'FR', 'RL', 'RR'):
        front = c[0] == 'F'
        s = 1 if c[1] == 'L' else -1
        R = spec['wheelRadiusFront' if front else 'wheelRadiusRear']
        W = spec['tyreWidthFront' if front else 'tyreWidthRear']
        tr = spec['trackFront' if front else 'trackRear']
        y = yf if front else yr
        out[c] = {'hub': (s * (tr / 2 + W * 0.30), y, R), 'y': y, 'R': R,
                  'planes': (s * (tr / 2 - W / 2), s * (tr / 2 + W / 2))}
    return out


def anchors3d(spec):
    """Point anchors only (the hubs): used to seed PnP."""
    return {'hub_' + c: w['hub'] for c, w in wheels3d(spec).items()}


_T = np.linspace(0, 2 * np.pi, 72, endpoint=False)


def residual(pred, img, ground):
    """Mean anchor error in px. A tyre's lowest point is flat, so a ground anchor's horizontal
    position is ill-defined: only its vertical error counts."""
    d = pred - img
    e = np.sqrt((d ** 2).sum(1))
    e[ground] = np.abs(d[ground, 1])
    return float(e.mean())


def predict_anchors(names, spec, rv, tv, f, cx, cy):
    """Model prediction for each named anchor, in pixels.
    hub_XX       projected hub centre
    ground_XX    lowest point of the projected tyre outline (both sidewall circles)
    tyreFront_XX / tyreRear_XX   the outline's horizontal extremes nearest / farthest from the nose
    Outline anchors are tangency points, not fixed points on the tyre: they move with the view."""
    W3 = wheels3d(spec)
    out = []
    cache = {}
    for n in names:
        kind, c = n.split('_')
        w = W3[c]
        if kind == 'hub':
            uv, _ = project(np.array([w['hub']]), rv, tv, f, cx, cy)
            out.append(uv[0])
            continue
        if c not in cache:
            # which way the tread faces: a camera ahead of the wheel sees the front of the tread, one
            # behind it sees the rear. On the tread-facing side the outline is the hull of both
            # sidewall circles (tread visible); on the far side only the outer sidewall shows.
            Rm = rot(rv)
            cam_pos = -Rm.T @ np.asarray(tv, float).ravel()
            ahead = cam_pos[1] < w['y']                 # -Y is forward
            rings = {}
            for tag, xp in (('outer', w['planes'][1]), ('inner', w['planes'][0])):
                pts = np.stack([np.full_like(_T, xp), w['y'] + w['R'] * np.cos(_T), w['R'] + w['R'] * np.sin(_T)], 1)
                rings[tag], _ = project(pts, rv, tv, f, cx, cy)
            both = np.concatenate([rings['outer'], rings['inner']])
            nose, _ = project(np.array([[0.0, w['y'] - 5.0, w['R']]]), rv, tv, f, cx, cy)
            # measured on the reference set: the outer-sidewall outline predicts the photos' tyre
            # extremes better than a tread-aware hull (the arch hides the tread on most wheels)
            cache[c] = {'front': rings['outer'], 'rear': rings['outer'], 'ground': rings['outer'], 'nose': nose[0]}
        cc = cache[c]
        nose = cc['nose']
        if kind == 'ground':
            uv = cc['ground']
            out.append(uv[np.argmax(uv[:, 1])])
        else:
            # where the tyre outline crosses the hub's pixel row: the same construction used when
            # reading the photo
            uv = cc['front' if kind == 'tyreFront' else 'rear']
            hub_uv, _ = project(np.array([w['hub']]), rv, tv, f, cx, cy)
            hu, hv = hub_uv[0]
            toward = np.sign(nose[0] - hu) or 1.0          # which image direction is forward
            hull = cv2.convexHull(uv.astype(np.float32)).reshape(-1, 2).astype(np.float64)
            xs = []
            for i in range(len(hull)):
                (u0, v0), (u1, v1) = hull[i], hull[(i + 1) % len(hull)]
                if (v0 - hv) * (v1 - hv) <= 0 and v0 != v1:
                    xs.append(u0 + (u1 - u0) * (hv - v0) / (v1 - v0))
            if not xs:
                xs = [hu]
            xs = np.array(xs)
            u = xs[np.argmax(xs * toward)] if kind == 'tyreFront' else xs[np.argmin(xs * toward)]
            out.append(np.array([u, hv]))
    return np.array(out)


# ---------------------------------------------------------------- masks

def photo_mask(a, name, v):
    img = cv2.imread(os.path.join(a['refs'], v['image']))
    h, w = img.shape[:2]
    cache = os.path.join(a['private'], f'mask_{name}.png')
    if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(os.path.join(a['refs'], 'views.json')):
        return img, cv2.imread(cache, cv2.IMREAD_GRAYSCALE) > 127
    if v.get('kind', 'photo') == 'ortho':
        # generated view on a plain background: threshold against the border colour
        border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).astype(np.float32)
        bg = np.median(border, axis=0)
        m = (np.linalg.norm(img.astype(np.float32) - bg, axis=2) > 28).astype(np.uint8)
    else:
        x0, y0, x1, y1 = v['bbox']
        gc = np.zeros((h, w), np.uint8)
        gc[:] = cv2.GC_BGD
        gc[y0:y1, x0:x1] = cv2.GC_PR_FGD
        # the wheels' interiors are certainly car: seed them as definite foreground
        for k, (u, vv) in v.get('anchors', {}).items():
            if k.startswith('hub_'):
                cv2.circle(gc, (int(u), int(vv)), 6, cv2.GC_FGD, -1)
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(img, gc, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
        m = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    for poly in v.get('maskFix', {}).get('add', []):
        cv2.fillPoly(m, [np.array(poly, np.int32)], 1)
    for poly in v.get('maskFix', {}).get('sub', []):
        cv2.fillPoly(m, [np.array(poly, np.int32)], 0)
    gl = v.get('groundLine')
    grounds = sorted(p for k, p in v.get('anchors', {}).items() if k.startswith('ground_'))
    if not gl and len(grounds) >= 2 and v.get('kind', 'photo') == 'photo':
        # default: the ground plane through the tyre contact points; drops shadows and pavement
        gl = [[grounds[0][0], grounds[0][1] + 3], [grounds[-1][0], grounds[-1][1] + 3]]
    if gl:
        (u0, v0), (u1, v1) = gl
        if u1 < u0:
            (u0, v0), (u1, v1) = (u1, v1), (u0, v0)
        yy, xx = np.mgrid[0:h, 0:w]
        below = (yy - v0) * (u1 - u0) - (xx - u0) * (v1 - v0) > 0
        m[below] = 0
    # nothing in a photo is below a tyre: under each wheel whose outline anchors are known, drop what
    # lies below the tyre's lower arc (contact-patch shadow and pavement wedges GrabCut keeps)
    an = v.get('anchors', {})
    for c in ('FL', 'FR', 'RL', 'RR'):
        need = [f'{k}_{c}' for k in ('hub', 'ground', 'tyreFront', 'tyreRear')]
        if not all(k in an for k in need):
            continue
        hu, hv = an['hub_' + c]
        gu, gv = an['ground_' + c]
        u0, u1 = sorted((an['tyreFront_' + c][0], an['tyreRear_' + c][0]))
        a_, b_ = max(1.0, (u1 - u0) / 2), max(1.0, gv - hv)
        cu = (u0 + u1) / 2
        for u in range(max(0, int(u0)), min(w, int(u1) + 1)):
            t = min(1.0, abs(u - cu) / a_)
            arc = hv + b_ * math.sqrt(max(0.0, 1 - t * t))
            m[int(arc) + 3:, u] = 0
    # largest component, holes filled
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (lab == big).astype(np.uint8)
    inv = 1 - m
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, 4)
    for i in range(1, n):
        x, y, ww, hh, _ = stats[i]
        if x > 0 and y > 0 and x + ww < w and y + hh < h:
            m[lab == i] = 1
    cv2.imwrite(cache, m * 255)
    return img, m > 0


def ignore_mask(v, size, scale=1.0):
    """Pixels excluded from scoring for BOTH model and photo: 'ignore' polygons in views.json mark
    parts of a reference that show a different body from the one being built (e.g. a convertible's
    rear deck in an otherwise usable front view). Each needs a stated 'ignoreReason'."""
    w, h = size
    W, H = int(round(w * scale)), int(round(h * scale))
    ig = np.zeros((H, W), np.uint8)
    for poly in v.get('ignore', []):
        cv2.fillPoly(ig, [np.round(np.array(poly, np.float64) * scale).astype(np.int32)], 1)
    return ig > 0


# ---------------------------------------------------------------- projection

def rot(rv):
    R, _ = cv2.Rodrigues(np.asarray(rv, np.float64))
    return R


def project(P, rv, tv, f, cx, cy):
    R = rot(rv)
    Q = P @ R.T + tv
    z = Q[:, 2:3]
    return Q[:, :2] / np.maximum(z, 1e-6) * f + np.array([cx, cy]), z[:, 0]


ORTHO_AXES = {'side_left': ('-y', 'z'), 'side_right': ('y', 'z'), 'front': ('x', 'z'),
              'rear': ('-x', 'z'), 'top': ('-x', '-y')}


def _comp(P, ax):
    return (-1 if ax[0] == '-' else 1) * P[:, 'xyz'.index(ax[-1])]


def cam_project(P, cam):
    """uv pixels and metres-per-pixel-at-each-point for a perspective or orthographic camera."""
    if cam.get('ortho'):
        r, u = ORTHO_AXES[cam['axis']]
        s = cam['pxPerM']
        uv = np.stack([cam['origin'][0] + s * _comp(P, r), cam['origin'][1] - s * _comp(P, u)], axis=1)
        return uv, np.full(len(P), 1.0 / s), np.ones(len(P))
    f, _, cx, cy = cam['K']
    uv, z = project(P, np.array(cam['rvec']), np.array(cam['t']), f, cx, cy)
    return uv, z / f, z


def raster_cam(verts, faces, cam, scale=1.0):
    if cam.get('ortho'):
        uv, _, _ = cam_project(verts, cam)
        return raster_uv(uv, np.ones(len(verts)), faces, tuple(cam['size']), scale)
    f, _, cx, cy = cam['K']
    return raster(verts, faces, np.array(cam['rvec']), np.array(cam['t']), f, cx, cy, tuple(cam['size']), scale)


def raster(verts, faces, rv, tv, f, cx, cy, size, scale=1.0):
    uv, z = project(verts, rv, tv, f, cx, cy)
    return raster_uv(uv, z, faces, size, scale)


def raster_uv(uv, z, faces, size, scale=1.0):
    w, h = size
    W, H = int(round(w * scale)), int(round(h * scale))
    uv = uv * scale
    tri = uv[faces]
    ok = (z[faces] > 0.05).all(axis=1)
    tri = tri[ok]
    img = np.zeros((H, W), np.uint8)
    # one convex fill per triangle: fillPoly on a list uses an even-odd rule, which would punch
    # holes wherever triangles overlap (every silhouette has thousands of overlaps)
    pts = np.round(tri * 4).astype(np.int32)
    lo = pts.min(axis=1) >> 2
    hi = pts.max(axis=1) >> 2
    vis = (hi[:, 0] >= 0) & (hi[:, 1] >= 0) & (lo[:, 0] < W) & (lo[:, 1] < H)
    fill = cv2.fillConvexPoly
    for t in pts[vis]:
        fill(img, t, 1, cv2.LINE_8, 2)
    return img > 0


def iou(a, b):
    inter = np.logical_and(a, b).sum()
    uni = np.logical_or(a, b).sum()
    return inter / max(1, uni)


# ---------------------------------------------------------------- camera fit

def fit_view(name, v, spec, verts, faces, mask):
    h, w = mask.shape
    cx, cy = w / 2, h / 2
    names = [k for k in v['anchors'] if k.split('_')[0] in ('hub', 'ground', 'tyreFront', 'tyreRear')]
    img = np.array([v['anchors'][k] for k in names], np.float64)
    # PnP seed: fixed-point approximations of the outline anchors (refined properly below)
    W3 = wheels3d(spec)

    def approx(n):
        kind, c = n.split('_')
        wh = W3[c]
        xo = wh['planes'][1]
        return {'hub': wh['hub'], 'ground': (xo, wh['y'], 0.0), 'tyreFront': (xo, wh['y'] - wh['R'], wh['R']),
                'tyreRear': (xo, wh['y'] + wh['R'], wh['R'])}[kind]
    obj = np.array([approx(n) for n in names], np.float64)
    cands = []
    if v.get('kind') == 'ortho':
        raise NotImplementedError('ortho views: use kind "photo" with anchors, or calibrate.py')
    sc = WORK / max(w, h)
    m_small = cv2.resize(mask.astype(np.uint8), (int(round(w * sc)), int(round(h * sc))), interpolation=cv2.INTER_NEAREST) > 0
    ig_small = ignore_mask(v, (w, h), sc) if v.get('ignore') else None
    if ig_small is not None:
        m_small = m_small & ~ig_small

    ground = np.array([n.startswith('ground_') for n in names])

    def anchor_err(rv, tv, f, pcx=cx, pcy=cy):
        pred = predict_anchors(names, spec, np.asarray(rv, float).ravel(), np.asarray(tv, float).ravel(), f, pcx, pcy)
        return residual(pred, img, ground)

    # 1. scan focal length: PnP on the anchors at each focal, score by silhouette overlap
    for f in np.geomspace(0.38 * w, 3.5 * w, 44):          # hfov ~105 deg .. ~16 deg
        K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
        try:
            ok, rv, tv = cv2.solvePnP(obj, img, K, None, flags=cv2.SOLVEPNP_SQPNP)
            if ok:
                ok, rv, tv = cv2.solvePnP(obj, img, K, None, rv, tv, True, cv2.SOLVEPNP_ITERATIVE)
        except cv2.error:
            continue
        if not ok:
            continue
        rv, tv = rv.ravel(), tv.ravel()
        _, z = project(verts[::97], rv, tv, f, cx, cy)
        if (z < 0.1).any():
            continue
        s = raster(verts, faces, rv, tv, f, cx, cy, (w, h), sc)
        if ig_small is not None:
            s = s & ~ig_small
        e = anchor_err(rv, tv, f)
        cands.append((iou(s, m_small) - 4 * e / max(w, h), f, rv.copy(), tv.copy(), e))
    if not cands:
        raise RuntimeError(f'{name}: no camera fits the anchors')
    cands.sort(key=lambda c: -c[0])
    # 2. refine all 7 parameters from the best few starts. The wheel anchors stay enforced (they are
    # spec-true points); the silhouette then fixes what four points on one side cannot: the roll about
    # the wheel line and the focal length, which the car's spec height and length determine.
    best = None
    # The principal point is free too: web photos are often crops, so the optical centre need not be
    # the image centre (a centred guess on a crop shows up as an ever-wider lens). Offsets are in
    # units of 2% of the image size and mildly penalised beyond 25%.
    def pp(x):
        return cx + x[7] * 0.02 * w, cy + x[8] * 0.02 * h

    def pp_prior(x):
        return max(0.0, abs(x[7] * 0.02) - 0.25) + max(0.0, abs(x[8] * 0.02) - 0.25)

    seeds = []
    for _, f0, rv0, tv0, _ in cands[:4]:
        # anchors alone first: the camera the measured wheel points imply
        d0 = np.linalg.norm(tv0)

        def acost(x, rv0=rv0, tv0=tv0, f0=f0, d0=d0):
            return anchor_err(rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * d0, f0 * math.exp(x[6] * 0.05), *pp(x)) \
                + 50 * pp_prior(x)
        r = minimize(acost, np.zeros(9), method='Powell', options={'maxiter': 6000, 'xtol': 1e-4, 'ftol': 1e-7})
        x = r.x
        seeds.append((r.fun, f0 * math.exp(x[6] * 0.05), rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * d0, pp(x)))
    seeds.sort(key=lambda t: t[0])
    err0 = seeds[0][0]
    best = None
    for _, f0, rv0, tv0, (cx0, cy0) in seeds[:2]:
        dist = np.linalg.norm(tv0)

        def unpack(x, rv0=rv0, tv0=tv0, f0=f0, dist=dist, cx0=cx0, cy0=cy0):
            return (rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * dist, f0 * math.exp(x[6] * 0.05),
                    cx0 + x[7] * 0.02 * w, cy0 + x[8] * 0.02 * h)

        def cost(x, unpack=unpack):
            rv, tv, f, pcx, pcy = unpack(x)
            s = raster(verts, faces, rv, tv, f, pcx, pcy, (w, h), sc)
            if ig_small is not None:
                s = s & ~ig_small
            # anchors are measured facts: within a few pixels they are free, beyond that they
            # dominate, so the silhouette can settle roll/focal but never drag the wheels off
            tol = max(6.0, 0.004 * max(w, h))
            e = anchor_err(rv, tv, f, pcx, pcy)
            hfov = 2 * math.degrees(math.atan(w / 2 / f))
            prior = max(0.0, 16 - hfov) + max(0.0, hfov - 105)       # plausible lenses, phone wide included
            off = max(0.0, abs(pcx - w / 2) / w - 0.25) + max(0.0, abs(pcy - h / 2) / h - 0.25)
            return (1 - iou(s, m_small)) + 4 * e / max(w, h) + 0.2 * max(0.0, e - tol) / tol + 0.05 * prior + 2 * off
        res = minimize(cost, np.zeros(9), method='Powell', options={'maxiter': 4000, 'xtol': 1e-3, 'ftol': 1e-6})
        if best is None or res.fun < best[0]:
            best = (res.fun, unpack(res.x))
    rv, tv, f, cx, cy = best[1]
    err = anchor_err(rv, tv, f, cx, cy)
    return {'K': [float(f), float(f), float(cx), float(cy)], 'R': rot(rv).tolist(), 't': [float(x) for x in tv],
            'silhouetteIoU': round(float(1 - best[0]), 4),
            'rvec': [float(x) for x in rv], 'size': [w, h], 'anchorErrPx': round(err, 2),
            'anchorErrPxBeforeRefine': round(float(err0), 2), 'focalPx': round(float(f), 1),
            'hfovDeg': round(math.degrees(2 * math.atan(w / 2 / f)), 2),
            'principalOffset': [round(float(cx / w - 0.5), 3), round(float(cy / h - 0.5), 3)]}


# ---------------------------------------------------------------- scoring

def regions(model, ref, verts, cam, top=3):
    """Largest error blobs, with where they sit on the car (nearest projected vertex)."""
    fp = np.logical_and(model, ~ref).astype(np.uint8)
    fn = np.logical_and(ref, ~model).astype(np.uint8)
    out = []
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    uv, _, _ = cam_project(verts[::7], cam)
    for kind, m in (('model too big', fp), ('model too small', fn)):
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            c = cent[i]
            j = int(np.argmin(((uv - c) ** 2).sum(1)))
            p = verts[::7][j]
            out.append({'kind': kind, 'areaPx': area, 'centrePx': [int(c[0]), int(c[1])],
                        'near': {'x': round(float(p[0]), 2), 'y': round(float(p[1]), 2), 'z': round(float(p[2]), 2)},
                        'where': describe(p)})
    out.sort(key=lambda r: -r['areaPx'])
    return out[:top]


def describe(p):
    x, y, z = p
    along = 'nose' if y < -1.6 else 'front wheel' if y < -0.9 else 'door/cabin' if y < 0.6 else 'rear wheel/intake' if y < 1.8 else 'tail'
    height = 'roof/top line' if z > 1.0 else 'shoulder' if z > 0.65 else 'flank' if z > 0.3 else 'sill/underside'
    side = 'left' if x > 0.3 else 'right' if x < -0.3 else 'centre'
    return f'{along}, {height}, {side}'


def sheet(img, model, ref, path, title):
    h, w = ref.shape
    over = img.copy()
    red = np.logical_and(model, ~ref)
    blue = np.logical_and(ref, ~model)
    over[red] = (0.35 * over[red] + 0.65 * np.array([40, 40, 255])).astype(np.uint8)
    over[blue] = (0.35 * over[blue] + 0.65 * np.array([255, 120, 30])).astype(np.uint8)
    cnt, _ = cv2.findContours(model.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(over, cnt, -1, (0, 255, 255), max(1, w // 700))
    s = 1100 / max(w, h)
    a = cv2.resize(img, None, fx=s, fy=s)
    b = cv2.resize(over, None, fx=s, fy=s)
    out = np.concatenate([a, b], axis=1)
    cv2.putText(out, title, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(out, title, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.imwrite(path, out)


# ---------------------------------------------------------------- curve suggestions

def suggest(v_name, cam, verts, faces, ref, spec, step=0.1):
    """Compare the model's top/bottom silhouette edges with the photo's, column by column.

    For each image column crossing the car, the model edge pixel is traced back to the model
    vertex that forms it (its y along the car and its camera depth z); the pixel gap becomes
    metres as gap * z / f. Results are binned into stations every `step` m along the car.
    Positive dz = the photo is taller there (raise the curve)."""
    model = raster_cam(verts, faces, cam)
    uv, mpp, z = cam_project(verts, cam)
    cols = np.where(model.any(0) & ref.any(0))[0]
    L = spec['length']
    bins = {}
    for c in cols[::2]:
        mcol, rcol = np.where(model[:, c])[0], np.where(ref[:, c])[0]
        mt, rt, mb, rb = mcol[0], rcol[0], mcol[-1], rcol[-1]
        for edge, mrow, rrow in (('top', mt, rt), ('bottom', mb, rb)):
            near = np.where((np.abs(uv[:, 0] - c) < 2.5) & (np.abs(uv[:, 1] - mrow) < 4))[0]
            if not len(near):
                continue
            k = near[np.argmin(z[near])]
            y = float(verts[k, 1])
            dz = (mrow - rrow) * mpp[k]            # rows grow downward: model lower -> positive
            key = round(round(y / step) * step, 2)
            if -L / 2 - 0.05 <= y <= L / 2 + 0.05:
                bins.setdefault(key, {}).setdefault(edge, []).append(float(dz))
    out = []
    for y in sorted(bins):
        rec = {'y': y}
        for edge in ('top', 'bottom'):
            vals = bins[y].get(edge)
            if vals and len(vals) >= 2:
                rec[edge + 'Dz'] = round(float(np.median(vals)), 3)
        out.append(rec)
    return out


# ---------------------------------------------------------------- commands

def clamp_spec(cfg, spec):
    """Keep the loft inside the spec's published envelope. The curves are PCHIP (no overshoot), so
    clamping control points bounds the whole surface: top, fender/rail peaks (top - rail) and roof
    shoulders (top - crown) <= height; plan half-width <= width / 2."""
    H, HW = spec['height'], spec['width'] / 2
    if cfg.get('surface') == 'cage':
        # a B-spline surface sits inside its control points: the fitter checks the evaluated surface
        # against the envelope, so control points get headroom here instead of a hard cap
        H, HW = H + 0.06, HW + 0.04
    c = cfg['curves']
    top = {y: v for y, v in c['top']}
    for p in c['top']:
        p[1] = min(p[1], H)
    from bisect import bisect_left
    ys = sorted(top)

    def top_at(y):
        i = min(max(bisect_left(ys, y), 1), len(ys) - 1)
        y0, y1 = ys[i - 1], ys[i]
        t = 0 if y1 == y0 else (y - y0) / (y1 - y0)
        return min(H, top[y0] + (top[y1] - top[y0]) * min(1, max(0, t)))
    for key in ('rail', 'crown'):
        for p in c.get(key, []):
            if abs(p[0]) < 5:
                p[1] = max(p[1], top_at(p[0]) - H)     # peak = top - drop <= H
    for p in c['halfW']:
        p[1] = min(p[1], HW - (0.0 if cfg.get('surface') == 'cage' else 0.003))   # section spline bulge
    for key, lo, hi in (('tuck', 0.0, 0.15), ('floorIn', 0.03, 0.30)):
        for p in c.get(key, []):
            p[1] = min(hi, max(lo, p[1]))
    return cfg


FIT_CURVES = ('top', 'bottom', 'rail', 'belt', 'crease', 'halfW', 'beltW', 'railW', 'crown', 'bulgeDrop', 'tuck', 'floorIn')
SMOOTH = 3.0        # weight of the curvature penalty: extra freedom may not buy IoU with lumps
# Only silhouette-observable curves are fitted. belt / crease / beltW are character lines drawn
# inside the outline: a silhouette barely sees them, so fitting them lets them drift into lumps.
FIT_OBSERVED = ('top', 'bottom', 'rail', 'halfW', 'railW', 'tuck', 'floorIn')
TRUST = 0.0005      # score cost per (5 cm)^2 moved from the design: weakly observed points stay put


def densify(cfg, n, loft):
    """Resample every fitted curve to n evenly spaced control points (same shape, more freedom)."""
    L = cfg['length']
    ys = [round(-L / 2 + L * i / (n - 1), 4) for i in range(n)]
    for k in FIT_CURVES:
        pts = cfg['curves'].get(k) or loft.DEFAULTS.get(k)
        if pts is None:
            continue
        cv = loft.Curve(pts)
        cfg['curves'][k] = [[y, round(cv(y), 4)] for y in ys]
    return cfg


def roughness(cfg):
    """Sum of squared second differences of every fitted curve, per metre of length: 0 for straight
    or evenly curving lines, large for bumps."""
    r = 0.0
    for k in FIT_OBSERVED:
        pts = [p for p in cfg['curves'].get(k, []) if abs(p[0]) < 5]
        for (y0, a0), (y1, a1), (y2, a2) in zip(pts, pts[1:], pts[2:]):
            h0, h1 = max(y1 - y0, 1e-3), max(y2 - y1, 1e-3)
            d2 = ((a2 - a1) / h1 - (a1 - a0) / h0) / ((h0 + h1) / 2)
            r += d2 * d2 * (h0 + h1) / 2
    return r
FIT_SCALARS = {'noseRound': 0.04, 'tailRound': 0.03, 'noseMin': 0.04, 'tailMin': 0.03,
               'nosePower': 0.25, 'tailPower': 0.25,
               'noseRoundLow': 0.04, 'noseMinLow': 0.04, 'nosePowerLow': 0.25,
               'tailRoundLow': 0.03, 'tailMinLow': 0.03, 'tailPowerLow': 0.25}
# realistic plan shapes: a pointed-to-rounded nose, a squarish tail (a wall-fronted car is not a car)
SCALAR_LIMITS = {'noseRound': (0.35, 0.70), 'tailRound': (0.15, 0.40), 'noseMin': (0.30, 0.75),
                 'tailMin': (0.65, 0.95), 'nosePower': (1.8, 4.0), 'tailPower': (2.5, 5.0),
                 'noseRoundLow': (0.20, 0.70), 'noseMinLow': (0.30, 0.90), 'nosePowerLow': (1.8, 6.0),
                 'tailRoundLow': (0.10, 0.40), 'tailMinLow': (0.60, 0.97), 'tailPowerLow': (2.0, 6.0)}


def fitshape(a, spec, views, cams):
    """Coordinate descent on the body curves against every view's silhouette.

    The body is re-lofted in plain Python (blender/body.py loft_mesh) for each candidate; parts the
    curves do not shape are rasterised per camera. Objective: mean IoU + worst IoU, so no view is
    traded away for another. Hard limits: the loft may not exceed the spec's height or half-width
    (the car stays 1:1 in its published dimensions); length is fixed by construction.

    --joint also refines every camera in the same descent (a silhouette bundle adjustment): each
    camera pays the same anchor penalty as `fit`, so the wheels stay on their measured pixels while
    shape and cameras settle together instead of oscillating between separate fits."""
    import copy
    sys.path.insert(0, os.path.join(os.path.dirname(HERE_DIR), 'blender'))
    import body as loft
    joint = a.get('joint', False)
    cpath = os.path.join(a['run'], 'curves.json')
    cur = clamp_spec(json.load(open(a.get('start') or cpath)), spec)
    for k, (lo, hi) in SCALAR_LIMITS.items():         # start inside the realistic plan-shape box
        if k in cur:
            cur[k] = min(hi, max(lo, cur[k]))
    if a.get('densify'):
        cur = clamp_spec(densify(cur, int(a['densify']), loft), spec)
    if a.get('smoothFirst'):
        # iron out the wiggles a previous fit bought IoU with, then let the fit win IoU back under a
        # curvature penalty measured from this smooth start (the belt line is measured: leave it)
        for k in FIT_OBSERVED + ('crease', 'crown', 'beltW'):
            pts = cur['curves'].get(k)
            if not pts or len(pts) < 5:
                continue
            for _ in range(int(a['smoothFirst'])):
                vals = [p[1] for p in pts]
                for i in range(1, len(pts) - 1):
                    pts[i][1] = round(vals[i] + 0.5 * ((vals[i - 1] + vals[i + 1]) / 2 - vals[i]), 4)
        cur = clamp_spec(cur, spec)
    rough0 = max(roughness(cur), 1e-6)
    design = json.loads(json.dumps(cur))            # the trust region is measured from here
    parts = json.load(open(os.path.join(a['run'], 'parts.json')))
    fixed = np.load(os.path.join(a['run'], 'checks', 'model_tris_fixed.npz'))
    fv, ff = fixed['verts'].astype(np.float64), fixed['faces']
    H, HW = spec['height'], spec['width'] / 2
    arches = [(d['x'][0], d['y'], d['z'], d['r']) for d in parts.get('arches', [])]
    V = []
    for name, v in views.items():
        if name not in cams:
            continue
        c = cams[name]
        _, ref = photo_mask(a, name, v)
        w, h = c['size']
        sc = WORK / max(w, h)
        small = cv2.resize(ref.astype(np.uint8), (int(round(w * sc)), int(round(h * sc))),
                           interpolation=cv2.INTER_NEAREST) > 0
        names = [k for k in v.get('anchors', {}) if k.split('_')[0] in ('hub', 'ground', 'tyreFront', 'tyreRear')]
        img = np.array([v['anchors'][k] for k in names], np.float64)
        st = {'rvec': np.array(c['rvec'], float), 't': np.array(c['t'], float), 'f': c['K'][0],
              'cx': c['K'][2], 'cy': c['K'][3]}
        ig = ignore_mask(v, (w, h), sc) if v.get('ignore') else None
        if ig is not None:
            small = small & ~ig
        V.append({'name': name, 'size': (w, h), 'sc': sc, 'small': small, 'names': names, 'img': img, 'st': st,
                  'ig': ig})

    def camdict(vw, st):
        return {'K': [st['f'], st['f'], st['cx'], st['cy']], 'rvec': list(st['rvec']), 't': list(st['t']),
                'R': rot(st['rvec']).tolist(), 'size': list(vw['size'])}

    def penalty(vw, st):
        if not joint:
            return 0.0
        w, h = vw['size']
        pred = predict_anchors(vw['names'], spec, st['rvec'], st['t'], st['f'], st['cx'], st['cy'])
        e = residual(pred, vw['img'], np.array([n.startswith('ground_') for n in vw['names']]))
        tol = max(6.0, 0.004 * max(w, h))
        hfov = 2 * math.degrees(math.atan(w / 2 / st['f']))
        lens = max(0.0, 16 - hfov) + max(0.0, hfov - 105)
        off = max(0.0, abs(st['cx'] - w / 2) / w - 0.25) + max(0.0, abs(st['cy'] - h / 2) / h - 0.25)
        return 4 * e / max(w, h) + 0.2 * max(0.0, e - tol) / tol + 0.05 * lens + 2 * off

    def body_tris(cfg):
        verts, quads = loft.loft_mesh(cfg, step=0.05)
        P = np.array(verts)
        if P[:, 2].max() > H + 0.003 or np.abs(P[:, 0]).max() > HW + 0.003:      # spec envelope, +-3 mm
            return None
        Q = np.array(quads)
        T = np.concatenate([Q[:, [0, 1, 2]], Q[:, [0, 2, 3]]])
        cen = P[T].mean(axis=1)
        keep = np.ones(len(T), bool)
        for x0, yc, zc, r in arches:           # wheel wells: the body is cut there
            keep &= ~((np.abs(cen[:, 0]) > x0) & ((cen[:, 1] - yc) ** 2 + (cen[:, 2] - zc) ** 2 < r * r))
        return P, T[keep]

    def view_iou(vw, st, PT, base=None):
        c = camdict(vw, st)
        if base is None:
            base = raster_cam(fv, ff, c, vw['sc'])
        uv, _, z = cam_project(PT[0], c)
        m = raster_uv(uv, z, PT[1], tuple(vw['size']), vw['sc']) | base
        if vw['ig'] is not None:
            m = m & ~vw['ig']
        return iou(m, vw['small']), base

    def drift(cfg):
        d = 0.0
        for k in FIT_OBSERVED:
            for (_, v), (_, v0) in zip(cfg['curves'][k], design['curves'][k]):
                d += ((v - v0) / 0.05) ** 2
        for k in FIT_SCALARS:
            if k in cfg:
                d += ((cfg[k] - design[k]) / (FIT_SCALARS[k] * 2)) ** 2
        return d

    def total(ious, pens, cfg=None):
        reg = 0.0
        if cfg is not None:
            reg = a.get('smoothWeight', SMOOTH) * 0.01 * max(0.0, roughness(cfg) / rough0 - 1.0) \
                + a.get('trust', TRUST) * drift(cfg)
        return float(np.mean(ious) + np.min(ious) - 2 * np.mean(pens)) - reg

    PT = body_tris(cur)
    if PT is None:
        raise SystemExit('curves break the spec envelope even after clamping')
    ious, bases, pens = [], [], []
    for vw in V:
        i_, b_ = view_iou(vw, vw['st'], PT)
        ious.append(i_)
        bases.append(b_)
        pens.append(penalty(vw, vw['st']))
    J = total(ious, pens, cur)
    show = lambda: '  '.join(f"{vw['name']} {i:.4f}" for vw, i in zip(V, ious))   # noqa: E731
    print('start  ' + show(), f'  J {J:.4f}', '(joint shape + cameras)' if joint else '')

    params = []
    for k in FIT_OBSERVED:
        for i, (y, _) in enumerate(cur['curves'].get(k, [])):
            if abs(y) < 5:
                params.append(('c', k, i, 0.01 if k in ('tuck', 'floorIn') else 0.02))
    params += [('s', k, None, st) for k, st in FIT_SCALARS.items() if k in cur]
    if joint:
        for vi in range(len(V)):
            for j in range(3):
                params.append(('r', vi, j, 0.003))
            for j in range(3):
                params.append(('t', vi, j, 0.003))
            params += [('f', vi, None, 0.01), ('cx', vi, None, 0.003), ('cy', vi, None, 0.003)]
    t0 = time.time()
    scale = 1.0
    while scale > 0.12 and time.time() - t0 < a.get('minutes', 20) * 60:
        improved = 0
        for kind, k, i, stp in params:
            for sgn in (1, -1):
                d = sgn * stp * scale
                if kind in ('c', 's'):
                    cand = copy.deepcopy(cur)
                    if kind == 'c':
                        cand['curves'][k][i][1] = round(cand['curves'][k][i][1] + d, 4)
                    else:
                        lo, hi = SCALAR_LIMITS[k]
                        cand[k] = round(min(hi, max(lo, cand[k] + d * 1.0)), 4)
                    pt = body_tris(cand)
                    if pt is None:
                        continue
                    new = [view_iou(vw, vw['st'], pt, bases[j])[0] for j, vw in enumerate(V)]
                    s_ = total(new, pens, cand)
                    if s_ > J + 1e-5:
                        cur, PT, ious, J = cand, pt, new, s_
                        improved += 1
                        break
                else:
                    vw = V[k]
                    st = dict(vw['st'])
                    st['rvec'], st['t'] = st['rvec'].copy(), st['t'].copy()
                    w_, h_ = vw['size']
                    if kind == 'r':
                        st['rvec'][i] += d
                    elif kind == 't':
                        st['t'][i] += d * np.linalg.norm(st['t'])
                    elif kind == 'f':
                        st['f'] *= math.exp(d)
                    elif kind == 'cx':
                        st['cx'] += d * w_
                    else:
                        st['cy'] += d * h_
                    i_new, b_new = view_iou(vw, st, PT)
                    p_new = penalty(vw, st)
                    new_i = list(ious)
                    new_i[k] = i_new
                    new_p = list(pens)
                    new_p[k] = p_new
                    s_ = total(new_i, new_p, cur)
                    if s_ > J + 1e-5:
                        vw['st'], ious, pens, J = st, new_i, new_p, s_
                        bases[k] = b_new
                        improved += 1
                        break
        print(f'pass scale {scale:.2f}: {improved} moves  ' + show(), f'  J {J:.4f}  ({time.time() - t0:.0f}s)')
        if improved < 3:
            scale *= 0.5
    rounds = len(json.load(open(os.path.join(a['run'], 'checks', 'rounds.json')))) if os.path.exists(
        os.path.join(a['run'], 'checks', 'rounds.json')) else 0
    json.dump(json.load(open(cpath)), open(backup_path(a['run'], rounds), 'w'), indent=1)
    json.dump(cur, open(cpath, 'w'), indent=2)
    if joint:
        cam_path = os.path.join(a['run'], 'checks', 'cameras.json')
        if os.path.exists(cam_path):          # keep the cameras this shape was not fitted with
            json.dump(json.load(open(cam_path)), open(backup_path(a['run'], rounds).replace('curves.', 'cameras.'), 'w'), indent=1)
        for vw in V:
            c = cams[vw['name']]
            st = vw['st']
            c.update(camdict(vw, st))
            pred = predict_anchors(vw['names'], spec, st['rvec'], st['t'], st['f'], st['cx'], st['cy'])
            c['anchorErrPx'] = round(residual(pred, vw['img'], np.array([n.startswith('ground_') for n in vw['names']])), 2)
            c['focalPx'] = round(float(st['f']), 1)
            c['hfovDeg'] = round(math.degrees(2 * math.atan(vw['size'][0] / 2 / st['f'])), 2)
            c['refinedJointly'] = True
        json.dump(cams, open(os.path.join(a['run'], 'checks', 'cameras.json'), 'w'), indent=1)
        print('anchor error px: ' + '  '.join(f"{vw['name']} {cams[vw['name']]['anchorErrPx']}" for vw in V))
    return ious


def backup_path(run, rounds):
    """curves.round<n>.json, or .round<n>b/.c... if that round already has a backup (never overwrite)."""
    base = os.path.join(run, 'checks', f'curves.round{rounds:02d}')
    p, k = base + '.json', 0
    while os.path.exists(p):
        k += 1
        p = f'{base}{chr(96 + k)}.json'
    return p


def backproject(cam, uv, plane_x):
    """Pixels -> points on the vertical plane x = plane_x (car frame), through a solved camera."""
    f, _, cx, cy = cam['K']
    R = np.array(cam['R'])
    t = np.array(cam['t'])
    C = -R.T @ t
    out = []
    for u, v in uv:
        d = R.T @ np.array([(u - cx) / f, (v - cy) / f, 1.0])
        if abs(d[0]) < 1e-9:
            continue
        s_ = (plane_x - C[0]) / d[0]
        if s_ > 0:
            p = C + s_ * d
            out.append([round(float(p[1]), 4), round(float(p[2]), 4)])
    return out


def trace(a, views, cams):
    """views.json "traces": {"<feature>": {"x": plane_x, "px": [[u, v], ...]}} per view, traced on the
    photo along a feature line (window outline, belt, crease, intake). Each becomes a (y, z) polyline
    in metres on that plane: the measured version of the lines curves.json and parts.json draw."""
    out = {}
    for name, v in views.items():
        for feat, tr in v.get('traces', {}).items():
            if name not in cams:
                continue
            pts = backproject(cams[name], tr['px'], tr['x'])
            out.setdefault(feat, {})[name] = {'x': tr['x'], 'yz': pts}
            print(f"{name:14s} {feat:14s} " + ' '.join(f'({y:+.3f},{z:.3f})' for y, z in pts))
    json.dump(out, open(os.path.join(a['run'], 'checks', 'traces.json'), 'w'), indent=1)
    return out


def apply_suggestions(a):
    """Shift the 'top' (and optionally 'bottom') curve control points by the damped, smoothed
    per-station corrections from checks/suggest.json. Only edges named in --edges move: the
    underside is off by default because photo masks carry the car's shadow there."""
    sug = json.load(open(os.path.join(a['run'], 'checks', 'suggest.json')))
    cpath = os.path.join(a['run'], 'curves.json')
    cur = json.load(open(cpath))
    rounds = len(json.load(open(os.path.join(a['run'], 'checks', 'rounds.json')))) if os.path.exists(
        os.path.join(a['run'], 'checks', 'rounds.json')) else 0
    json.dump(cur, open(backup_path(a['run'], rounds), 'w'), indent=1)
    changes = {}
    for edge in a['edges']:
        key = edge + 'Dz'
        st = [(r['y'], r[key]) for r in sug['stations'] if key in r and np.isfinite(r[key])]
        if len(st) < 3:
            continue
        ys, dz = np.array([p[0] for p in st]), np.array([p[1] for p in st])
        dz = np.convolve(np.pad(dz, 1, mode='edge'), np.ones(3) / 3, mode='valid')     # smooth
        dz = np.clip(dz, -0.08, 0.08)                                                   # one step at a time
        pts = cur['curves'][edge]
        moved = []
        for p in pts:
            if ys.min() - 0.15 <= p[0] <= ys.max() + 0.15:
                d = float(np.interp(p[0], ys, dz)) * a['gain']
                p[1] = round(p[1] + d, 4)
                moved.append((p[0], round(d, 3)))
        changes[edge] = moved
    clamp_spec(cur, json.load(open(os.path.join(a['run'], 'spec.json'))))
    json.dump(cur, open(cpath, 'w'), indent=2)
    return changes


def _hand_cage(run):
    c = os.path.join(run, 'curves.json')
    return os.path.exists(c) and json.load(open(c)).get('surface') == 'mesh'


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    a = args(argv)
    spec = json.load(open(os.path.join(a['run'], 'spec.json')))
    if a['cmd'] in ('apply', 'fitshape') and _hand_cage(a['run']):
        print(f"{a['cmd']}: the body is a hand-shaped cage (curves.json \"surface\": \"mesh\"); curve fits would not "
              'reach it. Score it, then fix the cage by hand (reference/hand-shaping.md).')
        return 2
    if a['cmd'] == 'apply':
        for edge, moved in apply_suggestions(a).items():
            print(edge, ' '.join(f'{y:+.2f}:{d:+.3f}' for y, d in moved))
        return 0
    views = load_views(a, include_skipped=a.get('includeSkipped', False))
    if a['cmd'] == 'mask':
        for name, v in views.items():
            img, m = photo_mask(a, name, v)
            ov = img.copy()
            ov[~m] = (ov[~m] * 0.25).astype(np.uint8)
            p = os.path.join(a['private'], f'maskview_{name}.jpg')
            cv2.imwrite(p, cv2.resize(ov, None, fx=1000 / max(img.shape[:2]), fy=1000 / max(img.shape[:2])))
            print(name, 'mask', int(m.sum()), 'px ->', p)
        return 0
    tri = np.load(os.path.join(a['run'], 'checks', 'model_tris.npz'))
    verts, faces = tri['verts'].astype(np.float64), tri['faces']
    lo_path = os.path.join(a['run'], 'checks', 'model_tris_lo.npz')
    if os.path.exists(lo_path):          # decimated copy: fast enough for the camera search
        lo = np.load(lo_path)
        verts_lo, faces_lo = lo['verts'].astype(np.float64), lo['faces']
    else:
        verts_lo, faces_lo = verts, faces
    cam_path = os.path.join(a['run'], 'checks', 'cameras.json')
    cams = json.load(open(cam_path)) if os.path.exists(cam_path) else {}
    if a['cmd'] == 'fit':
        for name, v in views.items():
            _, m = photo_mask(a, name, v)
            t = time.time()
            cams[name] = fit_view(name, v, spec, verts_lo, faces_lo, m)
            print(name, 'focal', cams[name]['focalPx'], 'hfov', cams[name]['hfovDeg'], 'anchor err px',
                  cams[name]['anchorErrPxBeforeRefine'], '->', cams[name]['anchorErrPx'], f'({time.time() - t:.0f}s)')
        json.dump(cams, open(cam_path, 'w'), indent=1)
        return 0
    if a['cmd'] == 'trace':
        trace(a, views, cams)
        return 0
    if a['cmd'] == 'fitshape':
        fitshape(a, spec, views, cams)
        return 0
    if a['cmd'] == 'suggest':
        # pool every calibrated view: a single view over-fits what it cannot see (a side view
        # knows nothing of width or the far side); the per-station median balances them
        pooled = {}
        for name, v in views.items():
            if name not in cams:
                continue
            _, ref = photo_mask(a, name, v)
            for r in suggest(name, cams[name], verts, faces, ref, spec):
                for edge in ('topDz', 'bottomDz'):
                    if edge in r:
                        pooled.setdefault(r['y'], {}).setdefault(edge, []).append(r[edge])
        rows = []
        for y in sorted(pooled):
            rec = {'y': y}
            for edge, vals in pooled[y].items():
                rec[edge] = round(float(np.median(vals)), 3)
                rec[edge + 'Views'] = len(vals)
            rows.append(rec)
        json.dump({'views': sorted(k for k in views if k in cams), 'stations': rows},
                  open(os.path.join(a['run'], 'checks', 'suggest.json'), 'w'), indent=1)
        print('pooled over', len([k for k in views if k in cams]), 'views  (top: + = raise the top line;  bottom: + = raise the underside)')
        for r in rows:
            print(f"  y {r['y']:+.2f}   top {r.get('topDz', float('nan')):+.3f} m   bottom {r.get('bottomDz', float('nan')):+.3f} m")
        return 0
    if a['cmd'] == 'score':
        rounds_path = os.path.join(a['run'], 'checks', 'rounds.json')
        rounds = json.load(open(rounds_path)) if os.path.exists(rounds_path) else []
        n = len(rounds) + 1
        rec = {'round': n, 'time': time.strftime('%Y-%m-%d %H:%M'), 'note': a['note'], 'views': {}}
        ok = True
        for name, v in views.items():
            if name not in cams:
                print(name, 'has no camera: run fit first')
                ok = False
                continue
            img, ref = photo_mask(a, name, v)
            c = cams[name]
            model = raster_cam(verts, faces, c)
            if v.get('ignore'):
                ig = ignore_mask(v, c['size'])
                model, ref = model & ~ig, ref & ~ig
            s = iou(model, ref)
            gate = GATE[v.get('kind', 'photo')]
            worst = regions(model, ref, verts, c)
            rec['views'][name] = {'iou': round(float(s), 4), 'gate': gate, 'pass': bool(s >= gate), 'worst': worst,
                                  'anchorErrPx': c['anchorErrPx']}
            ok = ok and s >= gate
            sheet(img, model, ref, os.path.join(a['private'], f'critique-{n:02d}-{name}.jpg'),
                  f'round {n}  {name}  IoU {s:.3f}  (gate {gate})  red=model too big  blue=too small')
            print(f'{name:10s} IoU {s:.4f}  {"PASS" if s >= gate else "FAIL"}   worst:',
                  '; '.join(f"{r['kind']} @ {r['where']} ({r['areaPx']}px)" for r in worst))
        rec['pass'] = bool(ok)
        rec['meanIoU'] = round(float(np.mean([v['iou'] for v in rec['views'].values()])) if rec['views'] else 0.0, 4)
        rounds.append(rec)
        json.dump(rounds, open(rounds_path, 'w'), indent=1)
        print(f'round {n}: mean IoU {rec["meanIoU"]}  ->', 'PASS' if ok else 'FAIL', f'(sheets in {a["private"]})')
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
