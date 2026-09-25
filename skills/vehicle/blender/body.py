"""zstack/vehicle - feature-line body loft.

Frame (fixed for the whole skill): +X is the car's LEFT, -Y is FORWARD (nose),
+Z is UP, ground at z = 0, metres. glTF export then gives +Z forward, +X left.

A body is a set of longitudinal curves (control points, PCHIP-interpolated so
nothing overshoots), traced off the side and top blueprints:

  top(y)       centre-line silhouette: bumper, bonnet, windscreen, roof, deck/tail
  bottom(y)    sill / bumper underside (wheel arches are cut analytically)
  crease(y)    the shoulder character line (a real crease under smooth shading)
  belt(y)      glass line (side-window sill)
  rail(y)      drop from top(y) to the roof rail / bonnet edge
  crown(y)     drop from top(y) to the roof shoulder
  bulgeDrop(y) how far below the crease the flank is widest
  halfW(y)     max body half-width      beltW(y)  half-width at the glass line
  railW(y)     half-width at the rail   tuck(y)   rocker tuck-under

Every station's cross-section is a Hermite spline through the same 9 key points,
so all stations have the same ring count and the rings form clean quads.

Usage inside Blender (MCP execute or `blender -b -P`):
    import body; ob = body.build(json.load(open('runs/<id>/curves.json')))
"""
import math

try:                                   # inside Blender
    from mathutils import Vector
except ImportError:                    # plain Python (scripts/critique.py rebuilds the loft to fit it)
    class Vector(tuple):
        def __new__(cls, xy):
            return tuple.__new__(cls, (float(xy[0]), float(xy[1])))

        x = property(lambda s: s[0])
        y = property(lambda s: s[1])

        def __add__(s, o):
            return Vector((s[0] + o[0], s[1] + o[1]))

        def __sub__(s, o):
            return Vector((s[0] - o[0], s[1] - o[1]))

        def __mul__(s, k):
            return Vector((s[0] * k, s[1] * k))

        __rmul__ = __mul__

        @property
        def length(s):
            return math.hypot(s[0], s[1])

        def normalized(s):
            n = s.length or 1.0
            return Vector((s[0] / n, s[1] / n))


class Curve:
    """Monotone piecewise-cubic (PCHIP) through (x, y) control points."""

    def __init__(self, pts):
        pts = sorted(pts)
        self.x = [p[0] for p in pts]
        self.y = [p[1] for p in pts]
        n = len(pts)
        h = [self.x[i + 1] - self.x[i] for i in range(n - 1)]
        d = [(self.y[i + 1] - self.y[i]) / h[i] for i in range(n - 1)]
        m = [0.0] * n
        if n == 2:
            m = [d[0], d[0]]
        else:
            m[0], m[-1] = d[0], d[-1]
            for i in range(1, n - 1):
                if d[i - 1] * d[i] <= 0:
                    m[i] = 0.0
                else:
                    w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
                    m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
        self.h, self.m = h, m

    def __call__(self, x):
        xs = self.x
        if x <= xs[0]:
            return self.y[0] + self.m[0] * (x - xs[0])
        if x >= xs[-1]:
            return self.y[-1] + self.m[-1] * (x - xs[-1])
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        h = self.h[lo]
        t = (x - xs[lo]) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * self.y[lo] + (t3 - 2 * t2 + t) * h * self.m[lo]
                + (-2 * t3 + 3 * t2) * self.y[lo + 1] + (t3 - t2) * h * self.m[lo + 1])


def hermite(p0, p1, m0, m1, t):
    t2, t3 = t * t, t * t * t
    return p0 * (2 * t3 - 3 * t2 + 1) + m0 * (t3 - 2 * t2 + t) + p1 * (-2 * t3 + 3 * t2) + m1 * (t3 - t2)


DEFAULTS = {'crown': [[-9, 0.02], [9, 0.02]], 'bulgeDrop': [[-9, 0.10], [9, 0.10]], 'tuck': [[-9, 0.05], [9, 0.05]],
            'floorIn': [[-9, 0.17], [9, 0.17]]}


class BodySpec:
    def __init__(self, cfg):
        self.cfg = cfg
        self.L = cfg['length']
        self.y0, self.y1 = -self.L / 2, self.L / 2
        curves = {**DEFAULTS, **cfg['curves']}
        self.c = {k: Curve(v) for k, v in curves.items()}
        self.arches = cfg['arches']            # [[y_axle, z_centre, radius], ...]
        self.nose_r = cfg.get('noseRound', 0.34)
        self.tail_r = cfg.get('tailRound', 0.28)
        self.nose_min = cfg.get('noseMin', 0.0)
        self.tail_min = cfg.get('tailMin', 0.0)
        self.nose_p = cfg.get('nosePower', 2.0)   # plan corner superellipse (2 round, higher squarer)
        self.tail_p = cfg.get('tailPower', 2.0)
        # the lower body (bumper / sill level) may round differently from the upper body: a wedge
        # nose is pointed at bonnet level and square at bumper level. Defaults: same as upper.
        self.low = {k: cfg.get(k + 'Low', cfg.get(k, d)) for k, d in
                    (('noseRound', 0.34), ('noseMin', 0.0), ('nosePower', 2.0),
                     ('tailRound', 0.28), ('tailMin', 0.0), ('tailPower', 2.0))}

    def bottom(self, y):
        z = self.c['bottom'](y)
        for ay, zc, r in self.arches:
            dy = y - ay
            if abs(dy) < r:
                z = max(z, zc + math.sqrt(r * r - dy * dy))
        return z

    def plan_low(self, y):
        lo = self.low
        if y < self.y0 + lo['noseRound']:
            t = min(1.0, (self.y0 + lo['noseRound'] - y) / lo['noseRound'])
            p = lo['nosePower']
            return lo['noseMin'] + (1 - lo['noseMin']) * max(0.0, 1 - t ** p) ** (1 / p)
        if y > self.y1 - lo['tailRound']:
            t = min(1.0, (y - (self.y1 - lo['tailRound'])) / lo['tailRound'])
            p = lo['tailPower']
            return lo['tailMin'] + (1 - lo['tailMin']) * max(0.0, 1 - t ** p) ** (1 / p)
        return 1.0

    def plan(self, y):
        if y < self.y0 + self.nose_r:
            t = min(1.0, (self.y0 + self.nose_r - y) / self.nose_r)
            return self.nose_min + (1 - self.nose_min) * max(0.0, 1 - t ** self.nose_p) ** (1 / self.nose_p)
        if y > self.y1 - self.tail_r:
            t = min(1.0, (y - (self.y1 - self.tail_r)) / self.tail_r)
            return self.tail_min + (1 - self.tail_min) * max(0.0, 1 - t ** self.tail_p) ** (1 / self.tail_p)
        return 1.0

    def keys(self, y):
        c = self.c
        top = c['top'](y)
        zb = self.bottom(y)
        f = self.plan(y)
        fl = self.plan_low(y)
        hw = c['halfW'](y) * f
        hwl = c['halfW'](y) * fl                      # lower body plan width
        hwm = (hw + hwl) / 2                          # the shoulder blends the two
        bw = min(c['beltW'](y) * f, hw - 0.005)
        rw = min(c['railW'](y) * f, bw - 0.004)
        rail = top - c['rail'](y)
        belt = min(c['belt'](y), rail - 0.01)
        crease = min(c['crease'](y), belt - 0.02)
        zc = max(crease, zb + 0.10)
        zmax = max(zc - c['bulgeDrop'](y), zb + 0.06)
        zr = zb + min(0.07, (zmax - zb) * 0.4)
        roof_edge = top - c['crown'](y)
        return [
            (0.0, zb, False),                          # 0 bottom centre
            (hwl - c['floorIn'](y) * fl, zb, False),   # 1 floor edge (how far the floor pan is set in)
            (hwl - c['tuck'](y), zr, False),           # 2 rocker turn
            (hwl - 0.004, zmax, False),                # 3 lower flank
            (max(hwm, bw + 0.005), zc, belt - zc >= 0.10 and zc - zb >= 0.15),  # 4 shoulder crease
            (bw, belt, False),                         # 5 glass line
            (rw, rail, False),                         # 6 roof rail / bonnet edge
            (rw * 0.80, roof_edge, False),             # 7 roof shoulder
            (0.0, top, False),                         # 8 top centre
        ]


SPAN = [5, 3, 5, 3, 5, 7, 4, 7]                    # samples between key i and i+1
SPAN_T = {5: [0.0, 0.03, 0.2, 0.4, 0.6, 0.8, 0.955]}  # thin rows at the glass edges


def section(spec, y):
    ks = spec.keys(y)
    P = [Vector((k[0], k[1])) for k in ks]
    n = len(P)
    dirs = []
    for i in range(n):
        if i == 0:
            d = Vector((1.0, 0.0))
        elif i == n - 1:
            d = Vector((-1.0, 0.0))
        else:
            d = P[i + 1] - P[i - 1]
            d = d.normalized() if d.length > 1e-9 else (P[i + 1] - P[i]).normalized()
        dirs.append(d)
    out, key_ring, ring = [], {}, 0
    for i in range(n - 1):
        a, b = P[i], P[i + 1]
        c = (b - a).length
        ma, mb = dirs[i] * c, dirs[i + 1] * c
        if ks[i][2]:
            ma = b - a
        if ks[i + 1][2]:
            mb = b - a
        key_ring[i] = ring
        for t in SPAN_T.get(i, [s / SPAN[i] for s in range(SPAN[i])]):
            out.append(hermite(a, b, ma, mb, t))
            ring += 1
    key_ring[n - 1] = ring
    out.append(P[-1])
    return out, key_ring


def stations(spec, step):
    n = max(2, int(round(spec.L / step)))
    ys = [spec.y0 + spec.L * i / n for i in range(n + 1)]
    for k in range(1, 20):
        u = (k / 20) ** 2
        ys += [spec.y0 + spec.nose_r * u, spec.y1 - spec.tail_r * u]
    # extra stations by plan-width fraction so a squared nose/tail keeps its corner
    for k in range(1, 12):
        u = (k / 12) ** 2
        ys += [spec.y0 + spec.low['noseRound'] * u, spec.y1 - spec.low['tailRound'] * u]
    for lo_, hi_, tip in ((spec.y0, spec.y0 + spec.nose_r, spec.y0), (spec.y1 - spec.tail_r, spec.y1, spec.y1)):
        for ft in (0.995, 0.985, 0.97, 0.95, 0.92, 0.88, 0.82, 0.74, 0.64, 0.52, 0.40, 0.28, 0.16):
            a_, b_ = lo_, hi_
            for _ in range(40):
                m_ = (a_ + b_) / 2
                if (tip == spec.y0) == (spec.plan(m_) > ft):
                    b_ = m_
                else:
                    a_ = m_
            ys.append((a_ + b_) / 2)
    return sorted(set(round(y, 5) for y in ys if spec.y0 <= y <= spec.y1))


# ---------------------------------------------------------------- subdivision-cage surface
#
# How automotive modellers get clean reflections: a SPARSE quad cage whose edge loops follow the
# design lines, smoothed by Catmull-Clark subdivision. On a regular quad grid Catmull-Clark
# converges to a uniform bicubic B-spline surface, so that is what is evaluated here, directly:
# sparse control rings (stations along the car x points around the section) -> smooth surface.
# Few control points = no room for wiggles; a character line stays crisp because its control
# point is duplicated (the B-spline equivalent of a support loop). Plain Python + numpy, so the
# Blender build and the critique's fast silhouette use exactly the same surface.

def _bspline_basis(n_ctrl, per_span, closed):
    """Matrix (samples x n_ctrl) of uniform cubic B-spline weights. Open curves repeat the end
    control points so the surface reaches them; closed curves wrap."""
    import numpy as np
    idx = list(range(n_ctrl))
    if not closed:
        idx = [0, 0] + idx + [n_ctrl - 1, n_ctrl - 1]
        n_seg = len(idx) - 3
    else:
        n_seg = n_ctrl
    rows = []
    for sgi in range(n_seg):
        ts = [k / per_span for k in range(per_span)]
        if not closed and sgi == n_seg - 1:
            ts.append(1.0)
        for t in ts:
            w = [(1 - t) ** 3 / 6, (3 * t ** 3 - 6 * t ** 2 + 4) / 6,
                 (-3 * t ** 3 + 3 * t ** 2 + 3 * t + 1) / 6, t ** 3 / 6]
            row = [0.0] * n_ctrl
            for j in range(4):
                c = idx[sgi + j] if not closed else (sgi + j - 1) % n_ctrl
                row[c] += w[j]
            rows.append(row)
    return np.array(rows)


def cage_stations(spec, n):
    """Control-station positions: even along the car, denser into the nose and tail plan rounding."""
    import math as _m
    ys = []
    for i in range(n):
        u = i / (n - 1)
        # blend of uniform and cosine spacing: 45% of the stations crowd towards the two ends
        v = 0.55 * u + 0.45 * (0.5 - 0.5 * _m.cos(_m.pi * u))
        ys.append(spec.y0 + spec.L * v)
    return ys


def cage_ring(spec, y, per_key=2, crease_copies=1):
    """Half-ring control points (x, z) from the section's key points, with `per_key` points between
    keys (taken off the section spline) and the shoulder crease duplicated `crease_copies` times."""
    sec, key_ring = section(spec, y)
    out = []
    keys = sorted(key_ring.items())
    sharp = spec.keys(y)[4][2]
    for (ki, a), (_, b) in zip(keys, keys[1:]):
        out.append(sec[a])
        if ki == 4 and crease_copies:
            # sharp crease: duplicate the control point (like a support loop); soft: place the extra
            # points just beside it so every ring keeps the same point count
            for c in range(crease_copies):
                out.append(sec[a] if sharp else sec[min(a + c + 1, b - 1)])
        for j in range(1, per_key + 1):
            out.append(sec[a + round((b - a) * j / (per_key + 1))])
    out.append(sec[-1])
    return out


def cage_surface(cfg, per_span_y=6, per_span_r=4):
    """Evaluate the body surface. Returns rows (list of closed rings of (x, y, z)), right side first
    then the mirrored left, bottom centre -> top centre -> back down."""
    import numpy as np
    spec = BodySpec(cfg)
    ys = cage_stations(spec, cfg.get('cageStations', 30))
    rings = []
    for y in ys:
        half = cage_ring(spec, y, cfg.get('cagePerKey', 2), cfg.get('creaseCopies', 1))
        right = [(p[0], p[1]) for p in half]
        left = [(-p[0], p[1]) for p in half[1:-1]]
        rings.append(right + list(reversed(left)))
    R = len(rings[0])
    P = np.array([[(x, y, z) for x, z in ring] for ring, y in zip(rings, ys)])     # (S, R, 3)
    By = _bspline_basis(len(ys), per_span_y, closed=False)
    Br = _bspline_basis(R, per_span_r, closed=True)
    surf = np.einsum('as,srk,br->abk', By, P, Br)
    return [[tuple(float(c) for c in p) for p in row] for row in surf]


def loft_mesh(cfg, step=0.04):
    if cfg.get('surface') == 'cage':
        rows = cage_surface(cfg, per_span_y=3, per_span_r=2)
        verts, quads, idx = [], [], []
        for row in rows:
            idx.append(list(range(len(verts), len(verts) + len(row))))
            verts += row
        for a, b in zip(idx, idx[1:]):
            m = len(a)
            for j in range(m):
                quads.append((a[j], b[j], b[(j + 1) % m], a[(j + 1) % m]))
        for ring in (idx[0], idx[-1]):
            c = len(verts)
            verts.append(tuple(sum(verts[i][k] for i in ring) / len(ring) for k in range(3)))
            quads += [(c, ring[j], ring[(j + 1) % len(ring)], c) for j in range(len(ring))]
        return verts, quads
    return _loft_mesh_sections(cfg, step)


def _loft_mesh_sections(cfg, step=0.04):
    """Plain-Python loft: (verts, quads) of the outer surface, no end caps. Used by the critique
    to re-rasterise the body thousands of times while it fits the curves to the photos."""
    spec = BodySpec(cfg)
    verts, quads, rows = [], [], []
    for y in stations(spec, step):
        sec, _ = section(spec, y)
        right = [(p[0], y, p[1]) for p in sec]
        left = [(-p[0], y, p[1]) for p in sec[1:-1]]
        ring = right + list(reversed(left))
        rows.append(list(range(len(verts), len(verts) + len(ring))))
        verts += ring
    for a, b in zip(rows, rows[1:]):
        m = len(a)
        for j in range(m):
            quads.append((a[j], b[j], b[(j + 1) % m], a[(j + 1) % m]))
    for ring in (rows[0], rows[-1]):              # flat end caps (fan) so the silhouette is closed
        c = len(verts)
        verts.append(tuple(sum(verts[i][k] for i in ring) / len(ring) for k in range(3)))
        quads += [(c, ring[j], ring[(j + 1) % len(ring)], c) for j in range(len(ring))]
    return verts, quads


def build(cfg, name='BodyShell', step=0.021, material=None):
    """Closed, mirrored shell with smooth shading. Returns the object (linked)."""
    import bmesh
    import bpy
    spec = BodySpec(cfg)
    bm = bmesh.new()
    rows = []
    key_ring = {}
    if cfg.get('surface') == 'cage':
        # dense samples of the smooth cage surface (~2 cm): fine enough for the boolean cuts
        for row in cage_surface(cfg, per_span_y=cfg.get('samplesY', 9), per_span_r=cfg.get('samplesR', 6)):
            rows.append([bm.verts.new(p) for p in row])
        ys = [r[0].co.y for r in rows]
    else:
        ys = stations(spec, step)
        for y in ys:
            sec, key_ring = section(spec, y)
            right = [bm.verts.new((p.x, y, p.y)) for p in sec]
            left = [bm.verts.new((-p.x, y, p.y)) for p in sec[1:-1]]
            rows.append(right + list(reversed(left)))
    m = len(rows[0])
    for a, b in zip(rows, rows[1:]):
        for j in range(m):
            j2 = (j + 1) % m
            bm.faces.new((a[j], b[j], b[j2], a[j2])).smooth = True
    # end caps: concentric rings (domed if noseDome/tailDome > 0, flat and flat-shaded if 0)
    for ring, sign, dome in ((rows[0], -1, cfg.get('noseDome', 0.0)), (rows[-1], 1, cfg.get('tailDome', 0.0))):
        cx = sum(v.co.x for v in ring) / len(ring)
        cz = sum(v.co.z for v in ring) / len(ring)
        prev = ring
        for k in range(1, 5):
            s_ = 1 - k / 4.6
            dy = sign * dome * (1 - s_ * s_)
            cur = [bm.verts.new((cx + (v.co.x - cx) * s_, v.co.y + dy, cz + (v.co.z - cz) * s_)) for v in ring]
            for j in range(len(ring)):
                j2 = (j + 1) % len(ring)
                quad = (prev[j], prev[j2], cur[j2], cur[j]) if sign > 0 else (prev[j], cur[j], cur[j2], prev[j2])
                try:
                    bm.faces.new(quad).smooth = abs(dome) > 1e-4
                except ValueError:
                    pass
            prev = cur
        try:
            bm.faces.new(prev if sign > 0 else list(reversed(prev))).smooth = abs(dome) > 1e-4
        except ValueError:
            pass
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0008)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0005)
    # Taubin-relax the last 0.3 m at each end, where short end sections crowd the rows
    relax = cfg.get('endRelax', 0.30)
    if relax > 0:
        y_lo, y_hi = min(ys), max(ys)
        vs = [v for v in bm.verts if (v.co.y > y_hi - relax or v.co.y < y_lo + relax)
              and abs(v.co.y - y_hi) > 1e-4 and abs(v.co.y - y_lo) > 1e-4]
        nbrs = {v: [e.other_vert(v) for e in v.link_edges] for v in vs}
        for _ in range(6):
            for fac in (0.5, -0.53):
                new = {v: v.co + (sum((n.co for n in nb), Vector()) / len(nb) - v.co) * fac for v, nb in nbrs.items() if nb}
                for v, co in new.items():
                    v.co = co
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)   # closed shell: safe
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    if material is not None:
        me.materials.append(material)
    bpy.context.scene.collection.objects.link(ob)
    ob['keyRing'] = {str(k): v for k, v in key_ring.items()}  # ID properties need string keys
    return ob


def build_from_cage(path, levels=3, name='BodyShell'):
    """Body shell from a hand-shaped half cage (cage.json, written by cage_kit.export_cage):
    mirrored on X, subdivided (Catmull-Clark, creases honoured), applied, closed. The subdivided
    shell is dense enough for the boolean cuts that follow, so cutting never alters its curvature."""
    import json
    import bmesh
    import bpy
    data = json.load(open(path))
    me = bpy.data.meshes.new(name + '_cage')
    me.from_pydata([tuple(v) for v in data['verts']], [], [tuple(f) for f in data['faces']])
    me.update()
    if data.get('creases'):
        cr = me.edge_creases_ensure()
        lookup = {tuple(sorted(e.vertices)): e.index for e in me.edges}
        vals = [0.0] * len(me.edges)
        for a, b, w in data['creases']:
            i = lookup.get(tuple(sorted((a, b))))
            if i is not None:
                vals[i] = w
        cr.data.foreach_set('value', vals)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    m = ob.modifiers.new('Mirror', 'MIRROR')
    m.use_axis = (True, False, False)
    m.use_clip = True
    m.use_mirror_merge = True
    m.merge_threshold = 1e-4
    s = ob.modifiers.new('Subdiv', 'SUBSURF')
    s.levels = s.render_levels = levels
    s.quality = 3
    s.use_limit_surface = True
    s.use_creases = True
    dg = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.clear()
    ob.data = baked
    bpy.data.meshes.remove(me)
    bm = bmesh.new()
    bm.from_mesh(baked)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    open_edges = sum(1 for e in bm.edges if e.is_boundary)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(baked)
    bm.free()
    ob['cageOpenEdges'] = open_edges          # must be 0: a closed shell is required for the cuts
    return ob
