# zstack

A stack of production skills for [Claude Code](https://claude.com/claude-code). Each skill is a
self-contained pipeline: numbered steps, scripts that do the work, and checks that can fail.
A skill never claims a result a check has not proven.

<!-- catalog:begin -->
| Skill | What it does |
|-------|--------------|
| [`building`](skills/building/SKILL.md) | Build one real building for a game end to end: open data (footprint, LiDAR height, assessor roll) into a spec, metadata tags, a look-only all-angle r…. |
| [`interior`](skills/interior/SKILL.md) | Furnish one building's floors for a game from its metadata and interior research: public indoor references first (look-only), otherwise image-model c…. |
| [`street-block`](skills/street-block/SKILL.md) | Make a 3D game's city (Three.js or any engine) look indistinguishable from street-level photos, block by block. |
| [`vehicle`](skills/vehicle/SKILL.md) | Build a 1:1, fully rigged, crash-deformable, game-ready vehicle in Blender from reference images. |
<!-- catalog:end -->

## Install

**As a Claude Code plugin** (every skill, kept up to date):

```
/plugin marketplace add duolahypercho/zstack
/plugin install zstack@zstack
```

Skills are then available as `/zstack:<skill>` (for example `/zstack:vehicle`), and Claude also
picks them up on its own when a request matches a skill's description.

**Without the plugin system** (symlinks, so a `git pull` updates them):

```bash
git clone https://github.com/duolahypercho/zstack.git
cd zstack
scripts/install.sh              # every skill into ~/.claude/skills  -> /vehicle
scripts/install.sh vehicle      # just one
scripts/install.sh --project .  # into ./.claude/skills of the current project
```

## The vehicle skill

Reference images in, a rigged GLB out:

1. **Spec**: real dimensions (length, width, height, wheelbase, tracks, tyres) from a published
   spec sheet, so the model is 1:1 by construction.
2. **References**: six consistent views from the built-in image generation tool, gated so a view
   with a non-flat background or an edge-cropped car cannot be modelled from; or reference photos
   found on the web (kept private, never shipped).
3. **Body**: a feature-line loft (top line, belt, shoulder crease, sill, plan width), solidified into
   a sheet-metal wall.
4. **Panels**: windows cut as flush glass; lamps and intakes recessed with real housings; doors,
   bonnet and engine cover separated along 4 mm shut lines.
5. **Everything else**: wheels (tyre with tread, twin-spoke rim, drilled disc, caliper), cabin
   (bucket seats, dash with screens, steering wheel, console, door cards), engine (cast block,
   brushed valve covers, polished plenum and runners, headers, strut brace), metal cooling fans.
6. **Rig**: hinge pivots found on each panel's real edge with a measured opening sense; wheels
   steer and spin; seats and steering wheel are anchors. Metadata ships as glTF extras.
7. **Crash**: four crumple-zone morph targets (front, rear, left, right) that stop at the cabin
   and the wheels.
8. **Proof**: every door is swept open and checked for clashes; the crash morphs are checked
   for cabin intrusion and folded faces; the GLB is re-imported and checked again.
9. **Critique**: each reference photo gets a solved camera, the model's silhouette is scored
   against the photo's (IoU), and the worst regions are named; the loop repeats until the gates pass.

Materials are physically based: clear-coated metallic paint with flake, transmissive glass,
chrome reflectors behind clear lenses, emissive lamp elements, brushed and cast metals.

[`examples/fast-car`](examples/fast-car) is a mid-engine sports coupe built with the skill: its
spec, curves and parts files, the check reports, per-round critique scores, and renders.

## Adding a skill

```bash
python3 scripts/new-skill.py my-skill "What it does, and when Claude should use it."
```

This creates `skills/my-skill/` (SKILL.md skeleton, `scripts/`, `reference/`, `templates/`)
and adds it to the catalog above. Every directory under `skills/` with a `SKILL.md` is a skill:
the plugin discovers them automatically, so nothing else needs registering.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules every skill follows.

```bash
python3 scripts/validate.py     # checks every skill; CI runs the same thing
```

## Layout

```
.claude-plugin/        plugin + marketplace manifests
skills/<name>/         one skill: SKILL.md + scripts/ + reference/ + templates/
examples/<name>/       worked examples produced by a skill
scripts/               new-skill.py, validate.py, install.sh
```

## Licence

MIT. Reference photos used while building examples are not part of this repository.
