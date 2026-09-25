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


def load_views(a):
    views = json.load(open(os.path.join(a['refs'], 'views.json')))
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
            ring = []
            # the outer sidewall only: in a photo the tread and inner sidewall are mostly hidden
            # inside the wheel arch, so the visible outline is the outer circle's ellipse
            for xp in w['planes'][1:]:
                ring.append(np.stack([np.full_like(_T, xp), w['y'] + w['R'] * np.cos(_T), w['R'] + w['R'] * np.sin(_T)], 1))
            uv, _ = project(np.concatenate(ring), rv, tv, f, cx, cy)
            nose, _ = project(np.array([[0.0, w['y'] - 5.0, w['R']]]), rv, tv, f, cx, cy)
            cache[c] = (uv, nose[0])
        uv, nose = cache[c]
        if kind == 'ground':
            out.append(uv[np.argmax(uv[:, 1])])
        else:
            # where the tyre outline (outer sidewall ellipse) crosses the hub's pixel row:
            # the same construction used when reading the photo
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

    def anchor_err(rv, tv, f):
        pred = predict_anchors(names, spec, np.asarray(rv, float).ravel(), np.asarray(tv, float).ravel(), f, cx, cy)
        return float(np.sqrt(((pred - img) ** 2).sum(1)).mean())

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
        e = anchor_err(rv, tv, f)
        cands.append((iou(s, m_small) - 4 * e / max(w, h), f, rv.copy(), tv.copy(), e))
    if not cands:
        raise RuntimeError(f'{name}: no camera fits the anchors')
    cands.sort(key=lambda c: -c[0])
    # 2. refine all 7 parameters from the best few starts. The wheel anchors stay enforced (they are
    # spec-true points); the silhouette then fixes what four points on one side cannot: the roll about
    # the wheel line and the focal length, which the car's spec height and length determine.
    best = None
    seeds = []
    for _, f0, rv0, tv0, _ in cands[:4]:
        # anchors alone first: the camera the measured wheel points imply
        d0 = np.linalg.norm(tv0)

        def acost(x, rv0=rv0, tv0=tv0, f0=f0, d0=d0):
            return anchor_err(rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * d0, f0 * math.exp(x[6] * 0.05))
        r = minimize(acost, np.zeros(7), method='Powell', options={'maxiter': 4000, 'xtol': 1e-4, 'ftol': 1e-7})
        x = r.x
        seeds.append((r.fun, f0 * math.exp(x[6] * 0.05), rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * d0))
    seeds.sort(key=lambda t: t[0])
    err0 = seeds[0][0]
    for _, f0, rv0, tv0 in seeds[:2]:
        dist = np.linalg.norm(tv0)

        def unpack(x, rv0=rv0, tv0=tv0, f0=f0, dist=dist):
            return rv0 + x[:3] * 0.02, tv0 + x[3:6] * 0.02 * dist, f0 * math.exp(x[6] * 0.05)

        def cost(x, unpack=unpack):
            rv, tv, f = unpack(x)
            s = raster(verts, faces, rv, tv, f, cx, cy, (w, h), sc)
            # anchors are measured facts: within a few pixels they are free, beyond that they
            # dominate, so the silhouette can settle roll/focal but never drag the wheels off
            tol = max(6.0, 0.004 * max(w, h))
            e = anchor_err(rv, tv, f)
            hfov = 2 * math.degrees(math.atan(w / 2 / f))
            prior = max(0.0, 16 - hfov) + max(0.0, hfov - 105)       # plausible lenses, phone wide included
            return (1 - iou(s, m_small)) + 4 * e / max(w, h) + 0.2 * max(0.0, e - tol) / tol + 0.05 * prior
        res = minimize(cost, np.zeros(7), method='Powell', options={'maxiter': 3000, 'xtol': 1e-3, 'ftol': 1e-6})
        if best is None or res.fun < best[0]:
            best = (res.fun, unpack(res.x))
    rv, tv, f = best[1]
    err = anchor_err(rv, tv, f)
    return {'K': [float(f), float(f), float(cx), float(cy)], 'R': rot(rv).tolist(), 't': [float(x) for x in tv],
            'silhouetteIoU': round(float(1 - best[0]), 4),
            'rvec': [float(x) for x in rv], 'size': [w, h], 'anchorErrPx': round(err, 2),
            'anchorErrPxBeforeRefine': round(float(err0), 2), 'focalPx': round(float(f), 1),
            'hfovDeg': round(math.degrees(2 * math.atan(w / 2 / f)), 2)}


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

def apply_suggestions(a):
    """Shift the 'top' (and optionally 'bottom') curve control points by the damped, smoothed
    per-station corrections from checks/suggest.json. Only edges named in --edges move: the
    underside is off by default because photo masks carry the car's shadow there."""
    sug = json.load(open(os.path.join(a['run'], 'checks', 'suggest.json')))
    cpath = os.path.join(a['run'], 'curves.json')
    cur = json.load(open(cpath))
    rounds = len(json.load(open(os.path.join(a['run'], 'checks', 'rounds.json')))) if os.path.exists(
        os.path.join(a['run'], 'checks', 'rounds.json')) else 0
    json.dump(cur, open(os.path.join(a['run'], 'checks', f'curves.round{rounds:02d}.json'), 'w'), indent=1)
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
    json.dump(cur, open(cpath, 'w'), indent=2)
    return changes


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    a = args(argv)
    spec = json.load(open(os.path.join(a['run'], 'spec.json')))
    if a['cmd'] == 'apply':
        for edge, moved in apply_suggestions(a).items():
            print(edge, ' '.join(f'{y:+.2f}:{d:+.3f}' for y, d in moved))
        return 0
    views = load_views(a)
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
