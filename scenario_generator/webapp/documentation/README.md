# Try scenario.generator

The best way to learn scenario.generator is to build something small and watch
it come alive. These examples start gently and then combine more of the editor.

Open **Load scenario** in the application header when you want to start from a
bundled
**Pass straight intersecting vehicle from right passing straight (.json)**,
**Cut-in from left on curved road (.json)**, **Cut-in from left (.xosc)**, or
**VRU crossing from left (.json)** example instead. The VRU crossing loads its
aligned trajectories and the **RITA junction** map together. The same dialog
still provides **Upload scenario** for your own files. **Load map** uses the
equivalent workflow and offers the bundled **Highway (.xodr)**, **RITA junction
(.xodr)**, and **Roundabout (.xodr)** maps.

A complete scenario configuration replaces the current actors, scenario
information, and map. A configuration without a map reference intentionally
removes a previously loaded map.

| Example | What you build | Approx. time |
| --- | --- | --- |
| [1. Create an intersecting conflict (no map)](01-intersection-conflict.md) | A car and a cyclist reaching one conflict point with a controlled time gap | 10 min |
| [2. Build a simple map and drive it](02-create-simple-map.md) | Two connected roads and a lane-snapped trajectory | 15 min |
| [3. Create a rainy pedestrian crossing](03-import-adapt-map-scenario.md) | “Rainy intersecting conflict with crossing pedestrian” on an imported [OpenDRIVE](https://www.asam.net/standards/detail/opendrive/) map | 20 min |
| [4. Test OpenADStack in OpenADSim](04-openads-scenario.md) | A closed-loop cut-in test with an OpenADStack-controlled ego vehicle on an OpenADSim-supported map | 20 min |

Each example produces a downloadable ZIP. The first three together cover the
main editing, inspection, playback, quality-checking, export, and
postprocessing workflows.

> **Note — Pointer and keyboard operation:** The tutorials describe canvas
> selection and dragging where that is the most direct pointer workflow. The
> same scenarios can be built without dragging: edit exact values in the
> labelled **Trajectory points** and **Road waypoints** tables. When the canvas
> has keyboard focus, use the arrow keys to move its cursor, Enter to select a
> position, `+` and `-` to zoom, Alt plus an arrow key to pan, and `F` to fit
> the view. These keyboard commands also work for measurements and lane
> selection.
