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

import bmesh
import bpy
from mathutils import Vector


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


DEFAULTS = {'crown': [[-9, 0.02], [9, 0.02]], 'bulgeDrop': [[-9, 0.10], [9, 0.10]], 'tuck': [[-9, 0.05], [9, 0.05]]}


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

    def bottom(self, y):
        z = self.c['bottom'](y)
        for ay, zc, r in self.arches:
            dy = y - ay
            if abs(dy) < r:
                z = max(z, zc + math.sqrt(r * r - dy * dy))
        return z

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
        hw = c['halfW'](y) * f
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
            (hw - 0.17 * f, zb, False),                # 1 floor edge
            (hw - c['tuck'](y), zr, False),            # 2 rocker turn
            (hw - 0.004, zmax, False),                 # 3 lower flank
            (hw, zc, belt - zc >= 0.10 and zc - zb >= 0.15),  # 4 shoulder crease (sharp only with a shoulder above)
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


def build(cfg, name='BodyShell', step=0.021, material=None):
    """Closed, mirrored shell with smooth shading. Returns the object (linked)."""
    spec = BodySpec(cfg)
    ys = stations(spec, step)
    bm = bmesh.new()
    rows = []
    key_ring = {}
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
