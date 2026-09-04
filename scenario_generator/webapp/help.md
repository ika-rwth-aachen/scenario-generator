# Quick workflow

1. Open **Load scenario** or **Load map** and choose a bundled default or load a
   custom file. A map is optional when actors do not need a road network.
   Loading a complete scenario configuration replaces the current actors,
   scenario information, and map. If that configuration has no map reference,
   the previous map is removed intentionally. A trajectory-only JSON contains
   actor trajectories rather than the complete editable project state.
2. Select an actor in the **Actors** panel and edit its static information, action, and controller.
3. Create or change trajectory points on the canvas or in the Trajectory points table.
4. Use the **Export controls** and **Additional scenario information** panels for metadata, environmental information, quality checks, and exports.

# Canvas interaction

- Select an empty canvas position to add a trajectory point for the selected actor.
- With the canvas focused, move its cursor using the arrow keys and press Enter
  to select the current position. Hold Shift for five-metre cursor steps.
- Drag a trajectory or road point to move it, or enter its exact coordinates in
  the corresponding points table.
- Use the mouse wheel or the canvas `+` and `-` keyboard commands to zoom. Use
  Alt plus an arrow key to pan. **Fit view** or the `F` key resets the viewport.
- On a touchscreen, drag empty canvas space to pan. Use a two-finger pinch to
  pan and zoom while keeping the point between both fingers in place.
- Actor names, point times, and speeds can be edited in their labelled form and
  table fields. Pointer users can also double-click their canvas labels.
- Distance and radius measurements accept canvas positions selected with either
  a pointer or the keyboard cursor.

# Views and tables

- Use View to control tables, actor annotations, map overlays, perception gaps, and the velocity profile.
- Options unavailable in the current editing mode are disabled.
- The velocity profile and perception gaps are available only in trajectory mode.

# Saving and exports

- Save config downloads the current scenario configuration. The JSON stores a
  map reference rather than embedding OpenDRIVE XML. Keep the referenced XODR
  with the config; when restoring separate files, load the map first and then
  the config so they can be matched by filename.
- Generate files produces the selected scenario files and can run selected
  postprocessing scripts before creating the ZIP bundle. The scenario config
  and any OpenDRIVE map it references are included automatically so the saved
  project remains portable.
- Only scripts installed with the application are available. Deployment
  operators should review custom scripts before adding them.
- Export trajectories in 2D writes trajectory heights as `z = 0`; otherwise a loaded map provides road elevation.

# Additional information

- Parameter declarations are configured for each actor in the OpenSCENARIO section.
- Environmental information can be enabled, loaded from a template, and saved as a reusable template.
- The Scenario Quality Checker validates the current scenario before export when enabled.
