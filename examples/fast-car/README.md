# fast-car

A mid-engine two-seat sports coupe built end to end with the [`vehicle`](../../skills/vehicle/SKILL.md)
skill. Unbranded: proportions follow the published dimensions of a current production mid-engine
coupe (4634 x 1934 x 1235 mm, wheelbase 2722 mm, tracks 1647 / 1590 mm, 245/35 R19 and 305/30 R20).

| file | what |
|------|------|
| `spec.json`, `curves.json`, `parts.json` | proportions, design curves (the cage seed), openings, lamps, intakes, panels, paint |
| `cage.json`, `cage_ops.json`, `ops/` | the hand-shaped body cage and the ten edits that made it, in order (`cage_round.py --replay` regenerates it to within 1 µm) |
| `fast-car.glb` | rigged export: 4 hinged panels, steer/spin wheels, seat and steering anchors, 4 crash morph targets |
| `fast-car.blend` | the Blender file |
| `checks/*.json` | door sweep, crash check, build log, solved cameras, every critique round |
| `renders/` | beauty, feature and reflection (zebra) renders |

Rebuild: `blender -b --factory-startup -P skills/vehicle/blender/build.py -- --run examples/fast-car`.

## How the body was made

The body is a hand-shaped subdivision cage, not a lofted or generated mesh. It went through three
stages.

**1. Seed.** The cage was seeded from the design curves. Measurements from the reference photos
then corrected their proportions:
- a vertical nose face at y ≈ -2.24 m, from the splitter (z 0.07 m) to the emblem (z 0.57 m);
- a hood 10–20 cm lower than the curve fit had it, sitting in a valley between higher fender
  crests;
- a windscreen base at about z 0.88 m and y -0.9 m, so the windscreen is far larger.

**2. Hand edits.** Ten small, replayable edits followed (`ops/`):
- fender crests;
- the swept-up lower nose corners;
- fender pods that reach inboard as the hood narrows to its V;
- the nose face set back;
- Taubin fairing of the body rows;
- the spec length held exactly.

**3. Traced parts.** The headlamps, front intakes, grille and side intakes are traced on the
reference photos. They are ray-cast through the solved cameras onto the body. The paint is solid
white with a gloss black roof and A-pillars.

## Results (checks, not impressions)

| check | result |
|-------|--------|
| door sweep (24 steps each: hinge on panel, opens outward/upward, no new intersections, returns home) | pass: Door_FL, Door_FR, Hood, EngineCover |
| same sweep on the re-imported GLB | pass |
| crash morphs (cabin cell moves <= 1 cm, < 1% faces folded, wheels unkeyed) | pass: front, rear, left, right |
| spec envelope (length 4.634, width 1.934, height 1.235 m) | length exact, width -0.6 mm, roof +3.2 mm |
| body cage | 730 vertices, all quads, 4 poles (end-cap corners), 0 open edges |
| triangles | about 213k (hero asset; no LODs yet) |

Silhouette IoU against four reference photos (gate 0.90 per photo). Both columns are scored with the
same reference masks:

| view | body in photo | previous (lofted curves) | now (hand-shaped) |
|------|---------------|--------------------------|-------------------|
| side | coupe | 0.921 | 0.911 |
| front three-quarter | convertible, rear deck ignored | 0.902 | 0.913 |
| rear three-quarter | coupe | 0.912 | 0.912 |
| left three-quarter | coupe | 0.906 | 0.901 |
| mean | | 0.910 | 0.909 |

Silhouettes are a tie. The previous body had already been fitted to them. The change is in what IoU
cannot see. In the front three-quarter photo, feature-outline IoU (model feature vs the outline
traced on the photo) moved as follows:

| feature | previous | now |
|---------|----------|-----|
| near headlamp | 0.00 | 0.88 |
| near front intake | 0.00 | 0.94 |
| grille | 0.16 | 0.86 |
| windscreen | 0.15 | 0.60 |

What that does not say:
- **Cameras.** The front three-quarter and side cameras were re-solved once against the
  hand-shaped body. Both methods tried agreed on the new front three-quarter focal length
  (hfov ≈ 45.6° instead of 52.6°). The rear three-quarter and left three-quarter re-solves
  scored worse and were discarded.
- **Mask.** The rear three-quarter mask counted ground shadow under the rear overhang as car.
  That was fixed with a `maskFix` `sub` polygon, whose reason is recorded in the private
  `views.json`. The previous body was re-scored with the same mask for the table above.
- **Thin margin.** The left three-quarter view clears the gate by 0.0005.
- **Far headlamp.** It is the mirror of the traced near one, and it still lands about 60 px off
  the photo's far lamp. The camera or the body is not yet consistent across the car's width.
- **Surfaces.** The nose is softer and rounder than the real car's crisp beak. The zebra renders
  still show bullseyes on the fender pods and the hood. The doors lack the real car's deep
  scallop, and the wheels are a generic 5-spoke. The grille lattice reads grey in bright light.
- **References.** One convertible reference (rear three-quarter) was replaced by a coupe
  photo. The convertible-only rear deck in the front three-quarter photo is excluded from scoring
  for both model and photo.

The reference photos themselves are not in this repository.
