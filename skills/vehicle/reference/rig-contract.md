# Vehicle rig contract

One frame, one set of names, so any engine can drive the model.

## Frame

| Axis | Blender | glTF / three.js after export |
|------|---------|------------------------------|
| forward (nose) | -Y | +Z |
| left | +X | +X |
| up | +Z | +Y |

Ground is z = 0. Metres. The car is centred on the origin in X and Y.
A left-hand-drive car has its steering wheel and `Seat_FL` at **+X**.

## Node names

| Node | Kind | Children | Notes |
|------|------|----------|-------|
| `Body` | mesh | - | painted shell with the openings cut out |
| `Door_FL` `Door_FR` `Door_RL` `Door_RR` | empty (pivot) | `Door_XX_Skin`, `Window_XX`, `Handle_XX`, `DoorCard_XX` | pivot on the hinge line |
| `Hood` | empty (pivot) | `Hood_Skin` | hinge at the rear edge (front-engine) or front edge (frunk) |
| `Trunk` / `Tailgate` / `EngineCover` | empty (pivot) | its skin (+ glass) | hinge on the correct edge |
| `Wheel_FL` ... `Wheel_RR` | empty (steer pivot, at wheel centre) | `Wheel_XX_Spin` | front pivots steer about local Z |
| `Wheel_XX_Spin` | empty (spin pivot) | tyre, rim, brake disc | spins about local X |
| `SteeringWheel` | empty on the column axis | rim, spokes, hub | turns about the column |
| `Seat_FL` `Seat_FR` (`Seat_RL` `Seat_RR`) | empty at the hip point | seat meshes | occupant anchors |
| `Glass_Windscreen`, `Glass_Hatch`, `Glass_Sail_FL/FR` | mesh | - | fixed glass (hatch glass rides `EngineCover`) |
| `Window_FL` ... | mesh | - | door glass, child of its door |
| `Lamp_Head_L/R`, `Lamp_Tail_L/R` | mesh (lens) | - | + `_Housing`, `_Strip*` (emissive light guide), `_Reflector*` / `_Core*` (projector units) |
| `Intake_*`, `Grille_*` | mesh | - | recessed housing + `_Grille` fins or lattice |
| `Mirror_L/R_*` | mesh | - | ride the front doors |
| `Engine_*`, `Fan_*` | mesh | - | tagged `zstack: engine`; visible through intakes / hatch glass |
| `SteeringWheel` | empty | wheel meshes | `spinAxis` (+ `spinAxisGltf`): the column axis |

## Hinge metadata (custom properties -> glTF extras)

Every opening pivot carries:

| Property | Type | Meaning |
|----------|------|---------|
| `hingeAxis` | `[x, y, z]` | unit axis in the pivot's local frame (= world frame at rest) |
| `openSign` | `1` or `-1` | the rotation sense that opens the part (right-hand rule about `hingeAxis`) |
| `openLimit` | radians | full-open angle |
| `doorType` | string | `conventional`, `scissor`, `butterfly`, `gullwing`, `lid` |
| `hingeAxisGltf` | `[x, y, z]` | the same axis in the glTF frame (Blender (x, y, z) -> glTF (x, z, -y)), written at export |

The hinge point is a vertex of the panel: for a door, the outermost vertex of its hinge edge, so
every other point of that edge is inboard of the axis and swings back into the shut-line gap
rather than forward into the fender. For a lid, the highest point of its hinge edge.

## Crash morph targets

Every exterior mesh (body, panels, glass, lamps, grilles, mirrors, exhaust) carries four shape keys,
exported as glTF morph targets with their names in `mesh.extras.targetNames`:
`Crush_Front`, `Crush_Rear`, `Crush_Left`, `Crush_Right`. Weight 0 = intact, 1 = the full crumple.
They are one world-space field sampled per part, so neighbouring parts deform together. Zones stop
ahead of the tyres and outside the cabin cell; wheels and the cabin interior are never keyed (engine-bay parts inside a zone, such as front radiator fans, fold with it).

Typical axes (left side; mirror X for the right side):

| Type | Axis | Opens by |
|------|------|----------|
| conventional | ~(0, 0, 1), tilted a few degrees in at the top | 60-70 deg |
| scissor | (1, 0, 0) at the A-pillar base | 75-90 deg upward |
| butterfly | normalize(0.35, -0.55, 0.76): up the A-pillar, tilted out | 70-90 deg |
| gullwing | (0, 1, 0) on the roof centre line | 70-90 deg |
| hood (rear hinge) | (1, 0, 0) | 60-80 deg |

The door check (`blender/door_check.py`) is the authority on whether `openSign`
is right: an opening part must travel outward or upward from its first step.

## Materials

Paint is one material named `Paint` so an engine can recolour it. Other names:
`Glass`, `GlassTint`, `Trim_Black`, `Trim_Chrome`, `Tyre`, `Rim`, `Brake`,
`Lamp_Head` (emissive), `Lamp_Tail` (emissive red), `Lamp_Indicator`, `Interior`.
