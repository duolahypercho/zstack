---
name: vehicle
description: "Build a 1:1, fully rigged, crash-deformable, game-ready vehicle in Blender from reference images. Generates consistent all-angle reference views with the built-in image generation tool (or finds reference photos on the web), hand-shapes the body as a mirrored all-quad subdivision cage (through Blender MCP or headless; never with an AI 3D generator), cuts it into a panelled sheet-metal body with flush glass, lamps, intakes, wheels, a furnished cabin and an engine, rigs doors / bonnet / engine cover / wheels / steering / seats, proves every opening part swings clear, adds crumple-zone morph targets, and critiques the model against every reference view (solved cameras, silhouette IoU, per-station corrections) until the silhouettes match. Use when asked to make, model, rig, or improve a car, truck, van, or any wheeled vehicle in Blender."
---

# vehicle

A reference-to-rig pipeline for one vehicle. Every step leaves a file behind, so a run can
stop and resume, and every claim ("1:1", "doors open", "crash-ready") is backed by a check
that can fail.

**Never use an AI 3D generator (Tripo, Hunyuan3D, Hyper3D Rodin, or any other) for a vehicle or
any part of one.** Their meshes are triangle soup without edge loops: they cannot be cut into
panels, hinged, rigged or crumpled. The body is a cage shaped by hand (section 5); wheels, lamps,
cabin and engine are built by the scripts here.

Resolve `<skill>` to the directory holding this file. Work in a run directory `runs/<id>/`
in the user's project (never inside this skill). Reference photos of real products are private:
keep them in a separate folder outside any repository (`--refs DIR`); the critique writes its
photo overlays next to that folder, never into the run.

```
runs/<id>/spec.json        real dimensions                          step 1
runs/<id>/refs/plan.json   the image_gen prompts, in order          step 2
<refs>/views.json + images reference views (private), gated         step 2
runs/<id>/curves.json      body feature curves                      step 4
runs/<id>/parts.json       openings, lamps, intakes, panels, cabin  step 4
runs/<id>/cage.json        the hand-shaped body cage                step 5
runs/<id>/cage/            cage checkpoints and reflection checks   step 5
runs/<id>/<id>.blend       the rigged model                         step 6
runs/<id>/checks/*.json    doors, crush, build, cameras, rounds     steps 6-7
runs/<id>/<id>.glb         the export                               step 8
runs/<id>/renders/*.png    beauty and feature renders               step 8
```

Requirements: Blender 4.2+ (tested on 5.1), Python 3.9+ with numpy, opencv-python and scipy
for the critique. Blender MCP is optional: every Blender step is a script that runs the same
headless (`blender -b -P`) or inside a live session via `execute_blender_code`. Hand-shaping
(section 5) is best done live through Blender MCP; every check it needs also runs headless.

## 1. Spec: the numbers that make it 1:1

Copy `<skill>/templates/spec.json` to `runs/<id>/spec.json`. Required: overall length, width
(without mirrors), height, wheelbase, front overhang, front/rear track, wheel radius and tyre
width per axle, door count and type (`conventional`, `scissor`, `butterfly`, `gullwing`), boot type.

- A real model: take the numbers from the manufacturer's published spec sheet.
- An original design: borrow the numbers of a real vehicle of the same class, and say which.
- Never reproduce badges, logos or other brand marks on a public asset.

Optional `"rim": {"spokes": 5, "twin": true, "splitDeg": [hub, mid, rim], "width": [hub, rim]}`
styles the wheels. A split spoke starts as one arm at the hub and fans out: the example uses
1.5° / 7° / 12° half-angles and 13 / 9 mm arm half-widths. Match the design in the reference photo.

## 2. Reference views

**You generate the views** with your built-in image generation tool. This skill has no provider
integration and needs no credentials: the image generation tool is called by you, the agent, and
is the only way references are produced here. `<skill>/scripts/refs.py` is the bookkeeping around
it — it writes the prompts, records each result, and runs the gate.

```
python3 <skill>/scripts/refs.py plan runs/<id>
```

prints the six prompts in order and writes `runs/<id>/refs/plan.json`. Then, for each view in
that order: call `image_gen` with that prompt, save the result, and hand it back with
`python3 <skill>/scripts/refs.py adopt runs/<id> --view side_left --image <path>`.

**The side view first, and feed it into every later view as the input image.** That is what keeps
six calls drawing one design instead of six different cars. The prompts carry the consistency
clause; the attached side image is what actually enforces it.

`adopt` runs the gate (`<skill>/scripts/check_refs.py`) on the view as it lands, and a failing view
is regenerated, never accepted with a note. The gate exists because `calibrate.py` measures the
car by thresholding the image against its own border colour: a view with a gradient studio
background, or a car that runs off the frame edge, yields a mask of the *studio*, and the
pixel-to-metre scale is then quietly wrong. A concept render with a beautiful moody backdrop is
not a usable reference. Each orthographic view is recorded as
`"kind": "ortho", "axis": "side_left" | "front" | "rear" | "top"` in `views.json`.

Also reject and regenerate any view whose wheel, door or pillar count differs from the side view,
that is visibly perspective where it must be orthographic, or that is mirrored or carries text.

`front34` and `rear34` are `"kind": "photo"`. After `adopt`ing them, add their `bbox` and wheel
anchors by hand (or point `adopt` at web photos below, which are already recorded that way).

**Web photos instead**: search the web for photos of the vehicle (side, front 3/4, rear 3/4).
Prefer clearly licensed images (e.g. Wikimedia Commons); record URL and licence in
`<refs>/sources.tsv`. For each photo add a `views.json` entry with `"kind": "photo"`, the car's
`bbox`, and anchors for each clearly visible wheel XX (FL/FR/RL/RR, +X is the car's LEFT):
`hub_XX` (outer centre), `ground_XX` (tyre contact), `tyreFront_XX` and `tyreRear_XX` (the tyre's
fore and aft extremes at hub height). Read pixels from zoomed, gridded crops: a 15 px anchor error
visibly tilts the solved camera.

## 3. Calibrate

- Orthographic views: `python3 <skill>/scripts/calibrate.py runs/<id> --refs DIR` maps pixels to
  metres from the spec and flags any view whose aspect ratio is > 3% off (a different car:
  regenerate, never stretch). In Blender, `<skill>/blender/blueprints.py` `load(run, refs)` places
  them as underlays.
- Photos: `python3 <skill>/scripts/critique.py mask runs/<id> --refs DIR`, inspect every
  `critique/maskview_*.jpg`, and correct with `maskFix` polygons (`add` for dark roofs and glass
  GrabCut dropped, `sub` for shadows). Then `critique.py fit` solves each photo's camera from the
  anchors and refines roll and focal length on the silhouette (`checks/cameras.json`).

## 4. Describe the car

`runs/<id>/curves.json` (template `<skill>/templates/curves.json`) describes the body by its
design lines, monotone curves along the car (y from -length/2 at the nose): `top` (centre line),
`bottom`, `rail` (drop from top to roof rail / bonnet edge; negative = fenders above the bonnet),
`belt` (glass line), `crease` (shoulder line), `halfW`, `beltW`, `railW`, `tuck`, `floorIn`, plus
nose/tail plan rounding (and `*Low` variants for a wedge nose that is square at bumper level).

The body surface is built the way automotive modellers build it: a sparse quad cage whose loops
follow those design lines, smoothed by subdivision (`"surface": "cage"`). On a regular quad grid
Catmull-Clark converges to a bicubic B-spline, which `body.py` evaluates directly from
`cageStations` control rings (24-30) of `cagePerKey` points between section keys; the shoulder
crease is kept crisp by a duplicated control point (`creaseCopies`), the B-spline equivalent of a
support loop. Rules from the trade that the pipeline follows:
- as few control points as possible: 10-12 per curve, 24-30 stations. Dense control points buy
  silhouette IoU with bumps;
- measure design lines from the photos (`critique.py trace`: pixels back-projected onto a plane
  through the solved camera) instead of guessing them. The belt, window outline and quarter glass
  of the example came from traces;
- judge surfaces by reflections, not silhouettes: the zebra check below.

The old per-section loft (no `surface` key) still builds, for comparison. These curves are the
starting point: they carry the proportions the critique can fit, and they seed the hand-shaped
cage of section 5, which is where the car gets its real shape.

`runs/<id>/parts.json` (template `<skill>/templates/parts.json`) lists, in the car's frame
(+X left, -Y forward, +Z up; left-side parts with `"mirror": true` build the right side too):

| key | what | how it is cut |
|-----|------|---------------|
| `arches` | wheel wells | cylinder across the tyre band only, lined |
| `openings` | windscreen, side glass, hatch glass | removed wall becomes flush glass; `parent` rides a panel |
| `recesses` | lamps (`lens`, `emissive`, `units`, `strips`), intakes and grilles (`grille`: fins / lattice, `openBack`) | aperture + housing receding into the body |
| `panels` | doors, bonnet, engine cover / boot | 4 mm shut-line ribbon; hinge edge and `openLimit` |
| `mirrors`, `exhaust`, `blades` | bolt-ons | |
| `cabin`, `engine` | interior and engine bay dimensions, cooling fans | |

Fixed glass gets a black rubber surround (`seal`, `sealRadius`); glass that rides a door is
frameless unless it sets `"seal": true`. A surround is cut back wherever a moving part (skin,
glass, mirror, door card) would sweep into it, as a real one stops at the shut line.

Outlines are 2D polygons in a plane (`YZ` side, `XZ` front/rear, `XY` top) or `auto` outlines
traced from the body's own curves: `greenhouse` (side glass), `windscreen`, `belt` (doors).

**Photo-traced outlines.** A lamp, intake or grille can instead be traced on a reference photo
whose camera the critique has solved:

```json
"photo": {"view": "front_right", "px": [[905, 622], [1000, 630], ...], "flipX": true}
```

This replaces `poly` and `range`. At build time each pixel is ray-cast through the camera onto
the body, so the part lands where the photo shows it. The cut is a thin shell along the view rays
(`depth`: 0.15 m in front of the surface, 0.25 m behind, stopped where each ray leaves the body),
so it cannot slice whatever lies behind a sloped lamp. `flipX` mirrors a part traced on the car's
right side into the left-side definition, so it can carry `"mirror": true`. Check it by symmetry:
the mirrored twin must land on the other side's feature in the same photo; if it misses, the camera
or the body there is wrong. Trace from zoomed, gridded crops, and never commit the photo itself.

A front- or rear-facing (`XZ`) part recedes toward the car's middle by default. A part that
faces the other way for where it sits, such as a forward-facing side intake ahead of a rear
wheel, sets `"facing": "front"` (or `"rear"`). The back of a deep opening (grille, intake duct)
uses `"housing": "Void"`, which reads as a hole rather than a dark surface. That is the trade's
rule: paint deep cavities dark instead of modelling them.

Two-tone paint: `materials` overrides any palette entry (e.g. `"Paint": {"color": [0.78, 0.79,
0.78], "metallic": 0.05}` for a solid white). `paintZones` gives painted-skin faces inside a prism
(same `plane` / `poly` / `range` / `mirror` as any part) another material, such as `Paint_Accent`
for a gloss black roof or pillars.

Two rules the checks will enforce:
- a shut line must cross the wall, not graze it: ahead of the side glass a door's top edge runs
  just below the belt (`frontDrop`), and rises into the glass opening only where the glass starts;
- door glass must start behind the door's rising edge; put a fixed sail glass ahead of it.

## 5. Hand-shape the body

The professional method, and the one this skill follows, is summarised in
`<skill>/reference/hand-shaping.md` with its rules, numbers and sources. It works like this:

- Shape one sparse, mirrored, all-quad cage under Subdivision Surface, row by row, against the
  spec box and the references.
- Judge it only by reflections.
- Never cut the cage. The build cuts shut lines and openings into the dense subdivided shell, so
  cuts cannot pinch the paint.

`<skill>/blender/cage_kit.py` provides every step. In a Blender MCP session, run each step with
`execute_blender_code` as `import sys; sys.path.insert(0, '<skill>/blender'); import cage_kit as K`
followed by one call. Headless, run the same calls in a script with `blender -b --factory-startup -P`.

Keep every edit as a small op file (`runs/<id>/ops/NN-what.py`), with a comment saying which
reference it answers. Then apply it with one round:

```
blender -b --factory-startup -P <skill>/blender/cage_round.py -- --run runs/<id> --tag r07 \
        --ops ops/07-crest-over-front-wheel.py --refs DIR
```

Each round loads `cage.json`, applies the ops, and then:
- prints the metrics;
- exports and checkpoints the cage;
- renders the photo overlays and stripe checks;
- appends the ops to `cage_ops.json`.

`--replay` regenerates the accepted cage from `curves.json` plus that list, so the hand-shaping
stays reviewable and reproducible. The example's ten ops replay to within 1 µm. After changing
the curves (proportions), `--replay` re-applies the hand edits on the new seed.

1. **Set up (idempotent).** `K.setup(RUN)` does all of this:
   - sets metric units;
   - adds `REF_box`, the spec envelope, and `REF_wheel_*` circles at the axle centres, all in an
     unselectable `REF` collection;
   - mounts the blueprint underlays if `blueprint.json` exists;
   - adds the fixed check cameras `CAM_side|front|rear|top` (ortho) and `CAM_front34|rear34|eye`;
   - adds a `CAM_photo_<view>` for every photo camera the critique has solved. These carry no
     background image, so no `.blend` ever holds a private photo path.
2. **Seed the cage.** `K.cage_from_curves(RUN)` builds the x ≥ 0 half cage from `curves.json`:
   - one row per station (`cageStations`, default 24, evenly spaced);
   - `cageCols` columns (default 24) round the half section, spread by arc length and curvature;
   - every design-line key (floor edge, rocker, flank, shoulder, belt, rail, roof) as a column;
   - the end caps as quad grids, the shoulder creased at 0.8;
   - Mirror (clip, merge) and Subdivision (limit surface, creases) modifiers.

   A subdivision surface shrinks inside its cage, so the seed is refitted until the subdivided
   surface passes through the design points. Expect a miss of about 0.01 mm at the median and
   a few mm at worst. To continue an earlier session, use `K.load_cage(RUN)` (from `cage.json`)
   or `K.restore(RUN, -1)` instead.
3. **Shape: one small step per call.**
   - Save first with `K.checkpoint(RUN, 'tag')`. `K.restore(RUN, 'tag')` is the undo; MCP undo
     is not reliable.
   - Edit with `K.edit_cage(lambda bm: ...)`, working on rows (stations, nose = 0) and columns
     (round the section, bottom centre = 0), never on vertex indices:
     - `K.move(bm, rows=, cols=, dx=, dy=, dz=, falloff_rows=, scale_x=)` moves a block, fading
       over neighbouring rows;
     - `K.space`, `K.relax` and `K.circle` on `K.row_loop(bm, r)`, `K.col_loop(bm, c)` or
       `K.loop_verts(edge)` stand in for LoopTools.
   - `K.insert_loop('row' | 'col', i)` adds a loop the way a loop cut does, then refits so the
     surface does not move.
   - `K.fit_surface(targets={index: point})` makes the surface pass through measured points,
     for example a traced belt line.
   - `K.project_pixels(RUN, view, px, plane=('x', 0.0))` turns photo pixels into 3D points
     through a solved camera: on a plane (the centre line, a flank), or, with `plane=None`, on
     the current body.
   - `K.pull(targets, radius=0.18)` then bends the surface through those points with a smooth
     falloff. It is proportional editing driven by measurements.
   - `K.fair(bm, rows=range(1, 23))` is Taubin smoothing. It removes the ripples the seed
     inherits from the curve fit without shrinking the body. Leave the end caps out, or the
     length grows, and re-score afterwards.
   - Work in the professional order: the proportions and silhouette against the box and photos
     first, then the arches, shoulder and belt, then the nose, tail and bumpers, and the
     greenhouse last. Edit whole rows. A hood or roof half needs only 6–8 faces.
4. **Measure.** Every call ends with `K.metrics(RUN)`, which prints JSON:
   - non-quads (keep at 0);
   - poles on curved areas (`curvedPoles`: move them to flat or hidden areas);
   - `seamDrift` and `nonManifold` (keep at 0);
   - `maxEdgeRatio` and where it is (even spacing);
   - `specError_mm` (width, length, roof height against the spec);
   - fairness of the subdivided surface.
5. **Look.**
   - `K.render_checks(RUN, tag)` renders MatCap views (`check_reflection_horizontal/vertical`,
     which follow the camera) and a chrome cage in a world-locked stripe environment, from the
     fixed cameras. It writes `runs/<id>/cage/checks/<tag>_<view>_<kind>.png`.
   - `K.photo_overlays(RUN, REFS, tag)` draws the subdivided cage in cyan, with a magenta
     silhouette, through each solved photo camera onto the photo. It writes to
     `<refs>/../critique/cage/`, which stays private.
   - In a live session, call `K.aim_viewport('side')` in the same call as
     `get_viewport_screenshot`, so each screenshot is comparable with the last.
   - Read the stripes: a kink or jump means a G0 break, a sharp turn means G1, a bullseye means a
     pole or bump, and ripples mean uneven spacing.
6. **Accept.** When the stripes flow and the overlays sit on the car, write the cage with
   `K.export_cage(RUN)` to `runs/<id>/cage.json`. Then set `curves.json`: `"surface": "mesh"`,
   `"cage": "cage.json"`, `"subdivLevels": 2`. Level 2 gives a body of about 60k triangles; use 3
   only for stills. Build (section 6): `checks/build.json` must show `cageOpenEdges: 0`, and the
   panel, door and crush checks still apply.

The Blender MCP link is fragile: calls time out after 180 s, and `get_scene_info` lists only 10
objects. So re-fetch objects by name in every call, keep code in files rather than long strings,
and save state with `checkpoint`, never in the session. Never drive a Blender instance that
another session is using: use a separate Blender MCP port, or run headless.

With `"surface": "mesh"`, `critique.py fitshape` and `apply`, and `iterate.py`, refuse to run:
they edit curves, and the curves no longer shape the body. `score` still gates every round. The
fixes it points at are made in the cage, by hand.

What the example's hand-shaping taught:
- **Measure before moving.** Back-project points you are sure of (the emblem and the grille on
  the centre plane, the A-pillar base on a flank plane). On the example they showed a vertical
  nose face, a hood 10–20 cm lower than the curve fit had it, and a far larger windscreen.
  Silhouettes cannot see any of those.
- **Treat solved cameras as uncertain.** Two photos can disagree about the same fender, and a
  symmetric body cannot satisfy both. When they do, split the difference instead of chasing one
  view.
- **Re-solving cameras.** After a large reshape, re-solve a camera once with `critique.py fit
  --views <view>`. Keep it only if the full-resolution `score` improves, and prefer evidence that
  does not use the silhouette, such as a mirrored feature landing on its twin. Record which
  cameras were re-solved.
- **Fix reference errors, not the gate.** A mask that counts ground shadow as car is a reference
  error: fix it with a `maskFix` `sub` polygon and write down the reason. Then re-score the
  previous version with the same mask, so the comparison stays fair.
- **A dent is not a scoop.** Pushing cage columns straight inward to make a door scoop gave a dent,
  with bullseye rings in the stripe renders, not a clean concave sweep, and it hid part of the
  traced intake. It was rejected. A concave panel needs its own loops laid along the sweep.

## 6. Build and rig

```
blender -b --factory-startup -P <skill>/blender/build.py -- --run runs/<id> --stages model,checks
```

(Inside Blender MCP: `import sys; sys.path.insert(0, '<skill>/blender'); import build; build.main(['--run', 'runs/<id>'])`.)

It lofts the body from the curves (`body.py`), or bakes the hand-shaped cage when `curves.json`
has `"surface": "mesh"` (`body.build_from_cage`: Mirror, then Subdivision, applied), solidifies it into a 4 mm wall, cuts wells, glass, lamps, intakes
and panels (`panels.py`), builds wheels (`wheels.py`), cabin (`interior.py`), engine and fans
(`engine.py`), bolt-ons (`details.py`), materials (`common.py`: clear-coated metallic flake paint,
transmissive glass, chrome reflectors, emissive lamp elements, brushed and cast metals), and rigs it
(`rig.py`, contract in `<skill>/reference/rig-contract.md`): each hinge sits on the panel's real
edge at its outermost point, and the opening sense is measured, not assumed. `crush.py` adds four
crumple-zone morph targets (`Crush_Front/Rear/Left/Right`).

Checks written by the build (`checks/build.json` says `BUILD PASS` only when all hold):
- `door_check.py`: every opening part swept shut -> open in 24 steps: hinge on its panel, moves
  outward/upward from the first step, never intersects anything it did not touch when shut, and
  returns home. Failures name the part it hits (`clashWith`) and the moving part that hits it (`clashBy`).
- `crush.py`: no key moves the cabin safety cell more than 1 cm, folds under 1% of faces, and no
  wheel is keyed.
- `fairness` (in `build.json`): angle between each painted-skin vertex normal and its neighbours'
  mean (cut edges and real creases excluded), as p50 / p95 / share over 3 degrees. It is the
  number behind the zebra renders: ripples raise it.

Reflection check: render `zebra_front34`, `zebra_side`, `zebra_top` (paint swapped for a mirror
under a banded sky). Stripes must flow in smooth, evenly spaced bands across each panel;
bullseye rings mark a bump, kinks mark a crease the design does not have, jitter marks noise.
Fix those in the cage (fewer or smoother control points), never in the shader.

A failure is fixed in the model or the rig, never by lowering a threshold.

## 7. Critique until 1:1

```
python3 <skill>/scripts/critique.py score    runs/<id> --refs DIR --note "what changed"
python3 <skill>/scripts/critique.py fitshape runs/<id> --refs DIR --joint --densify 20 --trust 0.00025
python3 <skill>/scripts/critique.py suggest  runs/<id> --refs DIR
python3 <skill>/scripts/critique.py apply    runs/<id> --gain 0.7 --edges top
python3 <skill>/scripts/iterate.py           runs/<id> --refs DIR --rounds 6
```

`fitshape --joint` is the main tool: it re-lofts the body in plain Python (hundreds of candidates
a minute) and runs one coordinate descent over the silhouette-observable curves (`top`, `bottom`,
`rail`, `halfW`, `railW`, `tuck`, `floorIn`), the nose/tail plan shape (upper body and, for wedge
noses, the lower body separately via `*Low` keys) and every camera. The objective is mean IoU +
worst IoU, so no view is traded away; each camera pays its anchor penalty, so wheels stay on
their measured pixels. Guards that keep the result a car:
- the spec envelope is a hard limit (height, half-width, +-3 mm), so the car stays 1:1;
- character lines (`belt`, `crease`, `beltW`) are never fitted: a silhouette barely sees them;
- a trust region (`--trust`) charges for every metre moved from the design, and a curvature
  penalty charges for lumps; plan-shape scalars live in realistic bounds.
A silhouette can be matched by a shape that no longer looks like the car. After every fit,
render `front34`, `rear34`, `front`, `side_left` and the zebra views, and look before accepting it.
Use `--densify 11 --smooth-first 3 --smooth-weight 12` with the cage surface: few control points,
a smooth start, and a curvature penalty strong enough that IoU is never bought with bumps.

Reference hygiene (in `views.json`), each recorded with its reason:
- `maskFix` add/sub polygons for dark roofs, reflections, parked cars and shadows GrabCut gets
  wrong; draw them from zoomed crops on the real edge. Nothing below a tyre ever counts (applied
  automatically from each wheel's anchors).
- `ignore` + `ignoreReason`: pixels scored for neither model nor photo, for the part of a reference
  that shows a different body (a convertible's deck in an otherwise usable front view).
- `skip`: a reference of a different body altogether. Never skip or ignore a view because it scores
  badly.

Intakes are closed, deep ducts (fans sit inside them, visible through the grille): an
open-backed recess is see-through and punches a hole in the silhouette.

`score` rasterises the model through each solved camera, computes silhouette IoU against the
reference mask, writes a sheet per view (red = model too big, blue = too small) and names the
worst regions; each round is appended to `checks/rounds.json`. `suggest` converts the top and
underside edge gaps of the most side-on view into metres per 10 cm station; `apply` moves the
curve control points by a damped, clamped share of it. `iterate.py` repeats
build -> score -> suggest -> apply, keeps the best round, reverts any round that scores worse or
breaks a door or crush check, and never refits cameras mid-loop (a re-solving camera absorbs the
shape error it is meant to expose).

Gates: IoU >= 0.95 per orthographic view, >= 0.90 per photo view. Then critique by eye on the
beauty renders (surface quality, panel gaps, lamp detail): numbers cannot see a lumpy bumper.
Plan-width errors need a front, rear or top view; the side view alone only constrains heights.

## 8. Export and render

```
blender -b --factory-startup -P <skill>/blender/build.py -- --run runs/<id> --stages model,checks,export,render
```

`export.py` writes `runs/<id>/<id>.glb` (extras, morph targets, hierarchy; hinge axes also in the
glTF frame as `hingeAxisGltf`), re-imports it and re-runs the door check on the imported file.
`render_views.py` renders standard views, feature shots (`doors_open`, `interior`, `engine`,
`crush_front`, `night`) and, when `checks/cameras.json` exists, views matched to each reference photo.

## Reporting

Per round: IoU per view and the gate, the worst regions, door and crush results, triangle count,
and what changed. Say plainly which gates are not met. A render is not proof; the checks are.
