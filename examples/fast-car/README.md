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
| spec envelope (length 4.634, width 1.934, height 1.235 m) | held as a hard limit by the fitter (+-3 mm) |
| triangles | about 216k (hero asset; no LODs yet) |

Silhouette IoU against four reference photos (cameras solved from wheel anchors, 4-10 px anchor
error; gate 0.90 per photo):

| view | body in photo | IoU | gate |
|------|---------------|-----|------|
| side | coupe | 0.922 | pass |
| front three-quarter | convertible, rear deck ignored | 0.902 | pass |
| rear three-quarter | coupe | 0.901 | pass |
| left three-quarter | coupe | 0.905 | pass |
| mean | | 0.907 | |

52 critique rounds are in `checks/rounds.json` (round 1 mean 0.760). What that table does not say:

- Margins are thin: two views clear the gate by 0.001-0.002.
- One convertible reference (rear three-quarter) was replaced by a coupe photo, and the
  convertible-only rear deck in the front three-quarter photo is excluded from scoring for both
  model and photo. Both are recorded with their reasons in the private `views.json`.
- An unconstrained fit reached higher IoU with a body that no longer looked like the car; the
  shipped shape comes from the regularised fit (observable curves only, trust region, curvature
  penalty, realistic plan bounds) and was checked on renders.
- IoU cannot see surface quality: the bonnet still undulates slightly and the frunk's front shut
  line dips through the valley between the fenders. Those are the next things to fix.

The reference photos themselves are not in this repository.
