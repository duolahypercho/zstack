# fast-car

A mid-engine two-seat sports coupe built end to end with the [`vehicle`](../../skills/vehicle/SKILL.md)
skill. Unbranded: proportions follow the published dimensions of a current production mid-engine
coupe (4634 x 1934 x 1235 mm, wheelbase 2722 mm, tracks 1647 / 1590 mm, 245/35 R19 and 305/30 R20).

| file | what |
|------|------|
| `spec.json`, `curves.json`, `parts.json` | the complete input: rebuild with `blender -b --factory-startup -P skills/vehicle/blender/build.py -- --run examples/fast-car` |
| `fast-car.glb` | rigged export: 4 hinged panels, steer/spin wheels, seat and steering anchors, 4 crash morph targets |
| `fast-car.blend` | the Blender file |
| `checks/*.json` | door sweep, crash check, build log, solved cameras, every critique round |
| `renders/` | beauty and feature renders (doors open, interior, engine, front crash, night) |

## Results (checks, not impressions)

| check | result |
|-------|--------|
| door sweep (24 steps each: hinge on panel, opens outward/upward, no new intersections, returns home) | pass: Door_FL, Door_FR, Hood, EngineCover |
| same sweep on the re-imported GLB | pass |
| crash morphs (cabin cell moves <= 1 cm, < 1% faces folded, wheels unkeyed) | pass: front, rear, left, right |
| triangles | about 202k (hero asset; no LODs yet) |

Silhouette IoU against three reference photos (cameras solved from wheel anchors; gate 0.90 per photo):

| view | IoU | gate |
|------|-----|------|
| side (coupe) | 0.901 | pass |
| front three-quarter | 0.897 | fail, 0.003 short |
| rear three-quarter | 0.878 | fail |
| mean | 0.892 | |

Critique history: 33 rounds in `checks/rounds.json` (round 1 mean 0.760). The loop stops improving
here for reasons it reports rather than hides:

- The rear three-quarter reference is the convertible body, whose rear deck and buttresses differ
  from the coupe modelled here; it cannot reach the gate on this reference.
- Photo corrections only constrain heights (top line, underside). Plan width needs a front, rear or
  top view, which this reference set lacks.
- Silhouette IoU cannot see surface quality: the bonnet and rear deck are still lumpier than a
  production body. That is the next thing to fix, by eye, on the beauty renders.

The reference photos themselves are not in this repository.
