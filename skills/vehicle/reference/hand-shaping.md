# Hand-shaping a car body

How professional vehicle artists shape a car body by hand in Blender, and how this skill runs
the same process through Blender MCP (or headless Blender) with `blender/cage_kit.py`.

**Hard rule: never use an AI 3D generator (Tripo, Hunyuan3D, Hyper3D Rodin, or any other) for any
part of a vehicle.** Their meshes are triangle soup with no loops. A body without loops cannot be
cut into panels, hinged, rigged, or crumpled cleanly. Every car body is a hand-shaped cage.

## Method

Professional modellers shape one uncut, mirrored, sparse, all-quad subdivision shell, then cut it:

- They lay the shell over a spec box, contour by contour.
- They judge it by how stripe reflections flow across it, not by the shaded view.
- They cut panels and openings only after the reflections pass.

Any hole, trim or support loop added to a sparse curved cage changes its curvature, and the paint
shows the change as a pinch. That is why cutting comes last.

This skill never cuts the cage. `build.py` bakes the cage (Mirror, then Subdivision) into a dense
shell, then cuts shut lines, glass and lamp openings into that dense shell with booleans. This
gives the same result the professionals get from their "guide mesh" step: the cuts cannot disturb
the approved surface.

## Rules and numbers

| Topic | Rule |
|-------|------|
| Frame | Metric units, Unit Scale 1.0, metres. The nose points to -Y, Z is up, and X is the mirror axis. +X is the car's **left**, so the driver's right is -X. glTF export turns this into +Y up and nose +Z. |
| Proportion | Build the spec box (length × width × height) and the wheel centres (from wheelbase, track and tyre radius) **before** mounting any reference image. Where the box and a blueprint disagree, the box wins. Tyre and rim sizes are standardised, so they are a reliable scale check. |
| References | Mount each view on its own image empty: depth Front, opacity 0.1–0.3, "show in perspective" off, all in an unselectable REF collection. Use solved cameras on 2–4 perspective photos: blueprints are often wrong, and studio photos are often retouched. |
| Mirror | Model the half with x ≥ 0 only. Use a Mirror modifier with clipping and merge (1e-4) on from the first polygon. Snap any vertex with \|x\| < 1 mm to exactly 0 after every edit. |
| Order | 1. Contours: wheel-arch rings, hood/fender shut contour, shoulder or belt, rocker, windscreen base, bumper outlines. 2. Fill with quads, front to back. 3. Greenhouse (roof and pillars) last. |
| Sparsity | Use as few control points as possible, spaced as evenly as possible, for as long as possible. A hood or roof half starts at 6–8 faces. Edit whole rows (move, scale, shear, proportional falloff), not single vertices. |
| Loops follow design lines | A sparse cage whose loops ignore the feature lines just becomes a smoother cigar. Every design line (shoulder, belt, rocker, arch) must be an edge loop. |
| Density changes | Change density by one step per loop. Aim for an adjacent edge-length ratio near 1.5:1. That figure is uncalibrated: no source gives a hard number, so check the stripes. |
| Poles | Catmull–Clark is only G1 at a vertex whose valence isn't 4 (a pole), and C2 elsewhere. Put poles and any non-quads only where neighbouring face normals differ by under 5°, or on hidden surfaces. Never put one under a highlight. |
| Round openings | Use at least 8 segments for a full circle; 12 is the usual start. Match the segment count to the loops that end on the opening. Space the ring evenly and fit it to a circle. |
| Creases vs loops | Preview sharpness with creases (`crease_edge`). glTF carries no crease data, so the final edge must be geometry. This pipeline bakes the subdivision, so a crease becomes geometry at export. |
| Details | Handles, emblems and scoops go in last, into the dense mesh, never into the sparse cage. |
| Shut lines | Place them on existing loops, in low-curvature areas or along feature lines. Make the visible gap 3–5 mm, identical along each line and left to right (real tolerance is 0.5–1 mm). This pipeline uses `parts.json` `gap` = 4 mm. |
| Parts | Anything that moves or detaches is its own object, with its origin on its hinge or axle: `car_root > susp > steer > spin` (see `rig-contract.md`). |
| Budgets | Hero or player car: 40–80k triangles (racing sim 80–200k). Traffic car: 5–25k. The shipped GTA V LOD chain was 60–120k, 20–30k, 10–15k, 5–10k, 0.5–1k. Build LODs by hand; automatic reducers damage the flow. |
| Finish | Body panels are real geometry with weighted normals and no normal maps (normal maps cause reflection artifacts). Bake only the interior, tyre sidewalls and grilles. |

## Reflections are the acceptance test

Rhino's zebra definitions carry over to subdivision surfaces:

| Continuity | What the stripes do at a join |
|------------|-------------------------------|
| G0 (position) | Stripes kink or jump sideways. |
| G1 (tangency) | Stripes line up but turn sharply. |
| G2 (curvature) | Stripes match and continue smoothly. This is the baseline for visible automotive surfaces. |

Check reflections two ways:

- **Blender 5.x MatCaps**, which follow the camera: `check_reflection_horizontal.exr`,
  `check_reflection_vertical.exr`, `metal_carpaint.exr`, `check_rim_light.exr`,
  `check_gradient.exr`.
- **A stripe environment locked to the world.** The highlight stays fixed while the camera moves,
  so it sweeps across the panels.

Analyse the surface at subdivision level 3 or higher.

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Bullseye or wobble at one point | A pole under the highlight | Move the pole to a flat or hidden area, or reroute the loops. |
| Stripes pinch toward an opening | A cut or support loop in the sparse cage | Never cut the cage. Cut the dense shell (this pipeline does). |
| Ripples along a panel | Uneven spacing or a sudden density change | `space` along the loops, `relax` across them, or use fewer vertices. |
| Dents where feature edges meet | Feature edges placed before the outline, or packed too tight | Rebuild the outline first, then re-space. |

Fairing is mostly done by hand. Smooth Vertices and Laplacian Smooth shrink a sparse cage and
pull it off the silhouette. The kit's `relax`, `space` and `circle` stand in for the LoopTools
tools, which are no longer bundled with Blender from 4.2 onwards.

When the whole cage needs fairing (a seed refitted through slightly rippled design points
carries those ripples into the paint), use `fair`: Taubin smoothing, a positive step toward the
neighbour mean followed by a slightly larger negative step, which removes lumps without the
shrinkage of plain Laplacian smoothing. Leave the end caps out so the length holds, and re-score
the silhouettes afterwards. On the example, eight iterations over the body rows cut the built
body's median normal deviation from 0.40° to 0.29° (cage p95 2.1° to 1.9°) at no IoU cost.

## Driving Blender through MCP

The common server (ahujasid/blender-mcp) is a remote Python prompt, not a transactional API:

- It `exec`s code on the main thread from a timer.
- Each response has a 180 s limit, and the first call sometimes fails.
- `get_scene_info` lists only 10 objects.
- Undo invalidates every Python reference.
- A viewport screenshot shows wherever the viewport happens to point.

The rules that follow from this, which `cage_kit.py` implements:

1. **Find objects by name in every call.** Use `CAR_cage`, `REF_*` and `CAM_*`; never keep a
   Python reference across calls.
2. **Edit in object-mode bmesh.** `edit_cage(fn)` does `from_mesh`, then the edit, then a seam
   weld, then `to_mesh`.
3. **Save state outside the session.** Call `checkpoint(RUN, tag)` before every risky step. It
   writes `runs/<id>/cage/history/NNN-tag.{json,blend}`. `export_cage` writes `cage.json`, which
   is the source of truth for the build.
4. **Keep code in files.** An MCP call runs `import cage_kit as K; K.<step>(...)` or
   `exec(open(path).read())`, never a long code string.
5. **End every call with numbers.** `metrics(RUN)` prints JSON: counts, non-quads, poles on
   curved areas, seam drift, non-manifold edges, the worst edge ratio and where it is, subdivided
   dimensions against the spec, and fairness.
6. **Look through fixed cameras.** `render_checks` renders MatCap and stripe images from the
   `CAM_*` set. `photo_overlays` draws the cage through each solved photo camera over the private
   photo. For a live screenshot, call `aim_viewport(view)` in the same call as the screenshot.

Headless Blender can't grab the viewport: `render.opengl`, `draw_view3d` and `screenshot_area` all
fail. A Workbench MatCap render or an EEVEE stripe-world render from a named camera works with or
without a UI, and gives the same frame every round.

## Blender 4.x/5.x API notes

| Area | Current API |
|------|-------------|
| Edge crease | FLOAT attribute `crease_edge`: `me.edge_creases_ensure()`; in bmesh, `bm.edges.layers.float['crease_edge']`. The old `edge.crease` is gone. |
| Mirror | `use_axis`, `use_clip`, `use_mirror_merge`, `merge_threshold` |
| Subdivision | `levels`, `render_levels`, `quality`, `use_limit_surface`, `use_creases`, `boundary_smooth` |
| Shrinkwrap | `wrap_method='TARGET_PROJECT'` gives the smoothest wrap. `NEAREST_SURFACEPOINT` can jump. Options: `wrap_mode`, `vertex_group`, `invert_vertex_group`. |
| Modifier order | `obj.modifiers.move(i, j)` works headless. |
| Loop cut | There is no scripted `loopcut_slide`. Walk the edge ring (`BMLoop.link_loop_next.link_loop_next`), then `bmesh.ops.subdivide_edges(..., cuts=1, quad_corner_type='STRAIGHT_CUT')`. New vertices come back in the op's `geom_split`; they inherit interpolated attributes. |
| Loop walking | There is no walker API. Walk `BMLoop` links by hand (`cage_kit.loop_verts`, `ring_edges`). |
| Auto smooth | Removed in 4.1. Use `shade_smooth_by_angle` or the `sharp_edge` attribute. |
| EEVEE | The engine id is `BLENDER_EEVEE` (`_NEXT` was dropped in 5.0). |
| Limit surface | A subdivision surface sits inside its cage. The Catmull–Clark limit of a vertex is (n²P + 4ΣE + ΣF) / (n(n+5)). To make the surface pass through design points, move the cage: `fit_surface`. |

## Sources

- Chris Plush / CG Masters, guide-mesh method and car course syllabus: https://www.youtube.com/watch?v=3rlMzsBWtPY, https://3dcarsacademy.com/
- Ali Ismail, modeling cars with polygons: https://www.ebalstudios.com/blog/modeling-cars-polygons
- Karol Miklas, vehicle production for games: https://80.lv/articles/vehicle-production-for-games/
- Polycount car-modelling workflow thread: https://polycount.com/discussion/222893/car-modelling-workflow
- FrankPolygon, SubD topology: https://polycount.com/discussion/230580/topology-for-subdivide
- Topology Guides, reflection artifacts: https://topologyguides.com/artifacts
- Catmull–Clark continuity: https://en.wikipedia.org/wiki/Catmull%E2%80%93Clark_subdivision_surface
- Rhino Zebra (G0/G1/G2): https://docs.mcneel.com/rhino/mac/help/en-us/commands/zebra.htm
- Blender 5.0 technical MatCaps: https://projects.blender.org/blender/blender/pulls/146591
- Blender manual: Mirror, Subdivision Surface, Shrinkwrap, Solidify, Empties, glTF exporter: https://docs.blender.org/manual/en/latest/
- Blender API gotchas (undo invalidates IDs): https://docs.blender.org/api/current/info_gotchas_crashes.html
- Blender 4.0 Python API changes (attributes): https://developer.blender.org/docs/release_notes/4.0/python_api/
- ahujasid/blender-mcp: https://github.com/ahujasid/blender-mcp
- SunStrike Studios, car modeling for games (budgets, cut late, UVs): https://sunstrikestudios.com/en/blog/car_modeling_for_games/
- BeamNG vehicle art (separate parts, no normal maps on panels): https://documentation.beamng.com/modding/vehicle/vehicle-art/modeling/
- GTA V LOD chain: https://www.modding-forum.com/guide/1-lod-models/
- Panel gap sizes: https://www.trueblox.com/blogs/prior-to-body-work/panel-gaps-in-depth
