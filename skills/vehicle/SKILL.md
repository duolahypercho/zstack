---
name: vehicle
description: "Build a 1:1, fully rigged, crash-deformable, game-ready vehicle in Blender from reference images. Generates consistent all-angle reference views with an image model (or finds reference photos on the web when no image model is available), models a panelled sheet-metal body with flush glass, lamps, intakes, wheels, a furnished cabin and an engine, rigs doors / bonnet / engine cover / wheels / steering / seats, proves every opening part swings clear, adds crumple-zone morph targets, and critiques the model against every reference view (solved cameras, silhouette IoU, per-station corrections) until the silhouettes match. Use when asked to make, model, rig, or improve a car, truck, van, or any wheeled vehicle in Blender."
---

# vehicle

A reference-to-rig pipeline for one vehicle. Every step leaves a file behind, so a run can
stop and resume, and every claim ("1:1", "doors open", "crash-ready") is backed by a check
that can fail.

Resolve `<skill>` to the directory holding this file. Work in a run directory `runs/<id>/`
in the user's project (never inside this skill). Reference photos of real products are private:
keep them in a separate folder outside any repository (`--refs DIR`); the critique writes its
photo overlays next to that folder, never into the run.

```
runs/<id>/spec.json        real dimensions                          step 1
<refs>/views.json + images reference views (private)                step 2
runs/<id>/curves.json      body feature curves                      step 4
runs/<id>/parts.json       openings, lamps, intakes, panels, cabin  step 4
runs/<id>/<id>.blend       the rigged model                         step 5
runs/<id>/checks/*.json    doors, crush, build, cameras, rounds     steps 5-7
runs/<id>/<id>.glb         the export                               step 8
runs/<id>/renders/*.png    beauty and feature renders               step 8
```

Requirements: Blender 4.2+ (tested on 5.1), Python 3.9+ with numpy, opencv-python and scipy
for the critique. Blender MCP is optional: every Blender step is a script that runs the same
headless (`blender -b -P`) or inside a live session via `execute_blender_code`.

## 1. Spec: the numbers that make it 1:1

Copy `<skill>/templates/spec.json` to `runs/<id>/spec.json`. Required: overall length, width
(without mirrors), height, wheelbase, front overhang, front/rear track, wheel radius and tyre
width per axle, door count and type (`conventional`, `scissor`, `butterfly`, `gullwing`), boot type.

- A real model: take the numbers from the manufacturer's published spec sheet.
- An original design: borrow the numbers of a real vehicle of the same class, and say which.
- Never reproduce badges, logos or other brand marks on a public asset.

## 2. Reference views

**Image model available** (`python3 <skill>/scripts/gen_refs.py probe` prints `available`):
`python3 <skill>/scripts/gen_refs.py all runs/<id>` generates `side_left` first, then every other
view with the side image as input, so they stay one car. Prompts: `<skill>/templates/prompts.json`.
Reject and regenerate a view whose wheel, door or pillar count differs, that is visibly
perspective where it must be orthographic, or that is cropped, mirrored, or carries text.
Record each as `"kind": "ortho", "axis": "side_left" | "front" | "rear" | "top"` in `views.json`.

**No image model**: search the web for photos of the vehicle (side, front 3/4, rear 3/4).
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

`runs/<id>/curves.json` (template `<skill>/templates/curves.json`): the body is a loft of
monotone curves along the car (y from -length/2 at the nose): `top` (centre line), `bottom`,
`rail` (drop from top to roof rail / bonnet edge; negative = fenders above the bonnet), `belt`
(glass line), `crease` (shoulder line), `halfW`, `beltW`, `railW`, plus nose/tail plan rounding.

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

Outlines are 2D polygons in a plane (`YZ` side, `XZ` front/rear, `XY` top) or `auto` outlines
traced from the body's own curves: `greenhouse` (side glass), `windscreen`, `belt` (doors).
Two rules the checks will enforce:
- a shut line must cross the wall, not graze it: ahead of the side glass a door's top edge runs
  just below the belt (`frontDrop`), and rises into the glass opening only where the glass starts;
- door glass must start behind the door's rising edge; put a fixed sail glass ahead of it.

## 5. Build and rig

```
blender -b --factory-startup -P <skill>/blender/build.py -- --run runs/<id> --stages model,checks
```

(Inside Blender MCP: `import sys; sys.path.insert(0, '<skill>/blender'); import build; build.main(['--run', 'runs/<id>'])`.)

It lofts the body (`body.py`), solidifies it into a 4 mm wall, cuts wells, glass, lamps, intakes
and panels (`panels.py`), builds wheels (`wheels.py`), cabin (`interior.py`), engine and fans
(`engine.py`), bolt-ons (`details.py`), materials (`common.py`: clear-coated metallic flake paint,
transmissive glass, chrome reflectors, emissive lamp elements, brushed and cast metals), and rigs it
(`rig.py`, contract in `<skill>/reference/rig-contract.md`): each hinge sits on the panel's real
edge at its outermost point, and the opening sense is measured, not assumed. `crush.py` adds four
crumple-zone morph targets (`Crush_Front/Rear/Left/Right`).

Checks written by the build (`checks/build.json` says `BUILD PASS` only when all hold):
- `door_check.py`: every opening part swept shut -> open in 24 steps: hinge on its panel, moves
  outward/upward from the first step, never intersects anything it did not touch when shut, and
  returns home. Failures name the part it hits (`clashWith`).
- `crush.py`: no key moves the cabin safety cell more than 1 cm, folds under 1% of faces, and no
  wheel is keyed.

A failure is fixed in the model or the rig, never by lowering a threshold.

## 6. Critique until 1:1

```
python3 <skill>/scripts/critique.py score   runs/<id> --refs DIR --note "what changed"
python3 <skill>/scripts/critique.py suggest runs/<id> --refs DIR
python3 <skill>/scripts/critique.py apply   runs/<id> --gain 0.7 --edges top
python3 <skill>/scripts/iterate.py          runs/<id> --refs DIR --rounds 6
```

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

## 7. Export and render

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
