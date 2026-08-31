// Map mode, road editing, table views, and Scenario Quality Checker controls.

// View toggles are grouped by the part of the application they affect.
const viewOptionGroups = [
  ["Table view", [["show_waypoint_table", "Trajectory points table"], ["show_road_waypoint_table", "Road waypoints table"], ["show_road_relations_table", "Road relations table"], ["show_detection_gaps", "Perception gaps"], ["show_speed_profile", "Velocity profile"]]],
  ["Road user view", [["show_vehicles", "Vehicles"], ["show_bounding_boxes", "Bounding boxes"], ["show_trajectory_waypoints", "Trajectory waypoints"], ["show_point_indices", "Point indices"], ["show_waypoint_times", "Waypoint times"], ["show_speed_labels", "Speed labels"], ["show_segment_average_speeds", "Segment average speeds"], ["show_actor_names", "Actor names"], ["show_min_ttc", "TTC min"], ["show_min_thw", "THW min"]]],
  ["Map view", [["show_map", "XODR map"], ["show_road_connections", "Road connections"], ["show_road_points", "Road points"], ["show_lane_numbers", "Lane numbers"], ["show_road_centerlines", "Road centerlines"], ["show_map_helpers", "Map helper lines"]]],
];

/** Keep the View popover beside its trigger without crossing viewport edges. */
function positionViewOptions() {
  const options = $("#view-options");
  const details = options.closest("details");
  if (!details.open) return;
  const triggerBounds = details.querySelector("summary").getBoundingClientRect();
  const optionsBounds = options.getBoundingClientRect();
  const viewportMargin = 8;
  const preferredLeft = triggerBounds.right - optionsBounds.width;
  const maximumLeft = window.innerWidth - optionsBounds.width - viewportMargin;
  const preferredTop = triggerBounds.bottom + 4;
  const constrainedLeft = Math.max(
    viewportMargin,
    Math.min(preferredLeft, maximumLeft),
  );
  const availableHeight = window.innerHeight - preferredTop - viewportMargin;
  options.style.left = `${constrainedLeft}px`;
  options.style.top = `${preferredTop}px`;
  options.style.maxHeight = `${Math.max(0, availableHeight)}px`;
}

const viewOptionsDetails = $("#view-options").closest("details");
viewOptionsDetails.addEventListener("toggle", positionViewOptions);
window.addEventListener("resize", positionViewOptions);
/** Report whether a details element is one of the dismissible toolbar popovers. */
function isDismissibleMenu(details) {
  return Boolean(details)
    && (details.classList.contains("help-options") || Boolean(details.closest(".canvas-toolbar")));
}

document.addEventListener("click", (event) => {
  document.querySelectorAll("details[open]").forEach((menu) => {
    if (isDismissibleMenu(menu) && !menu.contains(event.target)) menu.open = false;
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const eligibleMenus = [...document.querySelectorAll("details[open]")].filter(isDismissibleMenu);
  if (!eligibleMenus.length) return;
  const focusedMenu = document.activeElement?.closest?.("details[open]");
  const closingFocusedMenu = eligibleMenus.includes(focusedMenu);
  eligibleMenus.forEach((menu) => { menu.open = false; });
  // Focus only returns to a summary the user was actually inside, and only that
  // case consumes the key press so an unrelated Escape still exits fullscreen.
  if (!closingFocusedMenu) return;
  event.preventDefault();
  // The fullscreen handler listens on the same node, so a capture-phase
  // immediate stop is what keeps one Escape from also leaving fullscreen.
  event.stopImmediatePropagation();
  focusedMenu.querySelector("summary")?.focus();
}, true);

/** Apply the active editing mode to toolbars, panels, tabs, and View options. */
function renderMode() {
  // Keep unavailable View options visible but disabled so mode restrictions are explicit.
  const mapMode = state.scenario.settings.map_mode;
  if (mapMode && state.measurementTool !== null) clearMeasurement();
  if (mapMode) document.querySelectorAll(".canvas-toolbar details[open]").forEach((menu) => menu.removeAttribute("open"));
  $("#mode-toggle").textContent = mapMode ? "Switch to trajectory mode" : "Switch to map mode";
  $("#primary-title").textContent = mapMode ? "Roads" : "Actors";
  if (mapMode) $("#inspector-title").textContent = "Road inspector";
  $("#actor-list").hidden = mapMode;
  $("#road-list").hidden = !mapMode;
  $("#actor-actions").hidden = false;
  $("#add-actor").textContent = mapMode ? "Add road" : "Add actor";
  $("#delete-actor").hidden = mapMode;
  $("#map-actions").hidden = !mapMode;
  [$("#time-step").closest("label"), $("#trajectory-direction"), $("#waypoint-timing"), $("#lane-snap-control"), $("#delete-last-point"), $("#clear-actor")].forEach((element) => { element.hidden = mapMode; });
  $("#time-step").value = state.scenario.settings.time_step_s;
  const direction = state.scenario.settings.trajectory_calculation_mode;
  const timingMode = state.scenario.settings.waypoint_timing_mode;
  $("#trajectory-direction").textContent = `Timing edits: ${direction}`;
  $("#waypoint-timing").textContent = timingMode === "constant_speed"
    ? "New points: preserve speed"
    : "New points: fixed time step";
  $("#export-2d").checked = state.scenario.settings.export_2d;
  $("#show-sqc-warnings").checked = state.scenario.settings.show_sqc_warnings;
  $("#show-sqc-errors").checked = state.scenario.settings.show_sqc_errors;
  $("#show-tooltips").checked = state.scenario.settings.tooltips_enabled;
  $("#show-adjustment-warnings").checked = state.scenario.settings.adjustment_warnings_enabled;
  $("#connect-roads").classList.toggle("active", Boolean(state.connectionMode));
  $("#connect-roads").setAttribute("aria-pressed", String(Boolean(state.connectionMode)));
  $("#lane-snap").checked = state.scenario.settings.lane_snap_enabled;
  $("#lane-snap").disabled = mapMode;
  $("button[data-tab=\"waypoints\"]").hidden = mapMode || !state.scenario.settings.show_waypoint_table;
  $("button[data-tab=\"gaps\"]").hidden = mapMode || !state.scenario.settings.show_detection_gaps;
  $("button[data-tab=\"roads\"]").hidden = !mapMode || !state.scenario.settings.show_road_waypoint_table;
  $("button[data-tab=\"relations\"]").hidden = !mapMode || !state.scenario.settings.show_road_relations_table;
  const availableTabs = mapMode
    ? [["roads", state.scenario.settings.show_road_waypoint_table], ["relations", state.scenario.settings.show_road_relations_table]]
    : [["waypoints", state.scenario.settings.show_waypoint_table], ["gaps", state.scenario.settings.show_detection_gaps]];
  const visibleTabs = availableTabs.filter(([, enabled]) => enabled).map(([tab]) => tab);
  $("#tables").hidden = visibleTabs.length === 0;
  const activeTab = ["waypoints", "roads", "relations", "gaps"].find((tab) => !$("#" + tab + "-panel").hidden);
  if (visibleTabs.length && !visibleTabs.includes(activeTab)) showTableTab(visibleTabs[0]);
  state.previousMapMode = mapMode;
  const options = $("#view-options");
  options.replaceChildren();
  viewOptionGroups.forEach(([title, entries]) => {
    const group = document.createElement("section");
    group.className = "view-option-group";
    const heading = document.createElement("h3"); heading.textContent = title; group.append(heading);
    entries.forEach(([key, label]) => {
      const entry = document.createElement("label");
      entry.className = "check-label";
      const input = document.createElement("input");
      input.id = `view-${key}`;
      input.type = "checkbox"; input.checked = state.scenario.settings[key];
      input.disabled = mapMode
        ? ["show_detection_gaps", "show_speed_profile", "show_min_ttc", "show_min_thw", "show_waypoint_table"].includes(key)
        : ["show_road_waypoint_table", "show_road_relations_table"].includes(key);
      entry.classList.toggle("unavailable", input.disabled);
      if (input.disabled) entry.title = "Unavailable in current mode";
      input.onchange = () => updateSettings({ [key]: input.checked }, input);
      const text = document.createElement("span");
      text.textContent = label;
      entry.append(input, text); group.append(entry);
    });
    options.append(group);
  });
}

/** Build the selectable reference-road list; derived helper roads stay hidden. */
function renderMapEditor() {
  const list = $("#road-list"); list.replaceChildren();
  const selectedRoad = selectedRoadIndex();
  state.scenario.map.roads.forEach((road, index) => {
    if (road.kind !== "reference") return;
    const button = document.createElement("button"); button.className = "actor";
    button.id = `road-choice-${index}`;
    button.textContent = road.name || `road_${index + 1}`;
    button.classList.toggle("active", selectedRoad === index);
    button.setAttribute("aria-pressed", String(selectedRoad === index));
    button.setAttribute("aria-label", `${button.textContent}${selectedRoad === index ? ", selected" : ""}`);
    button.onclick = () => { state.selectedRoad = index; renderRoadForm(); renderRoadPoints(); renderMapEditor(); };
    list.append(button);
  });
  renderRoadForm(); renderRoadPoints();
}

/** Populate the road inspector and bind its fields to the selected reference road. */
function renderRoadForm() {
  const mapMode = state.scenario.settings.map_mode;
  $("#road-form").hidden = !mapMode;
  $("#actor-form").hidden = mapMode;
  if (!mapMode) return;
  const road = state.scenario.map.roads[selectedRoadIndex()];
  if (!road) { $("#road-form").replaceChildren(); return; }
  $("#inspector-title").textContent = "Road inspector";
  $("#road-form").innerHTML = `<label>Name<input id="road-name"></label><label>Width [m]<input id="road-width" type="number" min="0.1" step="0.1"></label><label>Lane count<input id="road-lanes" type="number" min="1" step="1"></label><label>Lane widths<input id="road-lane-widths" placeholder="-1:3.5; 1:3.5"></label><label>Lane types<input id="road-lane-types" placeholder="-1:driving; 1:driving"></label><label>Predecessor road<input id="road-predecessor"></label><label>Successor road<input id="road-successor"></label><label>Predecessor lane links<input id="road-predecessor-links"></label><label>Successor lane links<input id="road-successor-links"></label>`;
  setValue("#road-name", road.name); setValue("#road-width", road.width_m); setValue("#road-lanes", road.lane_count);
  setValue("#road-lane-widths", Object.entries(road.lane_widths_m).map(([id, width]) => `${id}:${width}`).join("; "));
  setValue("#road-lane-types", Object.entries(road.lane_types).map(([id, type]) => `${id}:${type}`).join("; "));
  setValue("#road-predecessor", road.predecessor_road); setValue("#road-successor", road.successor_road);
  setValue("#road-predecessor-links", road.predecessor_lane_links); setValue("#road-successor-links", road.successor_lane_links);
  $("#road-form").querySelectorAll("input").forEach((input) => input.onchange = (event) => saveRoad(event.currentTarget));
}

/** Persist compact lane specifications for backend expansion and validation. */
async function saveRoad(sourceField = null) {
  const index = selectedRoadIndex();
  if (index === undefined) return;
  try {
    await api(`/api/map/roads/${index}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#road-name").value, width_m: Number($("#road-width").value), lane_count: Number($("#road-lanes").value), lane_width_spec: $("#road-lane-widths").value, lane_type_spec: $("#road-lane-types").value, predecessor_road: $("#road-predecessor").value, successor_road: $("#road-successor").value, predecessor_lane_links: $("#road-predecessor-links").value, successor_lane_links: $("#road-successor-links").value }) });
    await refresh();
  } catch (error) { markFieldInvalid(sourceField, error.message); }
}

/** Rebuild the point table; coordinate edits submit the complete ordered polyline. */
function renderRoadPoints() {
  const body = $("#road-body"); body.replaceChildren();
  const road = state.scenario.map.roads[selectedRoadIndex()]; if (!road) return;
  road.points.forEach((point, pointIndex) => {
    const roadName = road.name || `road ${selectedRoadIndex() + 1}`;
    const row = document.createElement("tr");
    row.innerHTML = `<td><button id="road-point-${pointIndex}-insert" type="button" class="insert" aria-label="Insert (+) road point before point ${pointIndex + 1}">+</button><button id="road-point-${pointIndex}-remove" type="button" class="remove" aria-label="Remove (−) road point ${pointIndex + 1}">−</button></td><td><input id="road-point-${pointIndex}-x" type="number" step="any" value="${point[0]}"></td><td><input id="road-point-${pointIndex}-y" type="number" step="any" value="${point[1]}"></td>`;
    const coordinateInputs = row.querySelectorAll("input");
    coordinateInputs[0].setAttribute("aria-label", `${roadName}, road point ${pointIndex + 1}, X coordinate in metres`);
    coordinateInputs[1].setAttribute("aria-label", `${roadName}, road point ${pointIndex + 1}, Y coordinate in metres`);
    row.querySelector(".insert").onclick = () => insertRoadPoint(pointIndex);
    row.querySelector(".remove").onclick = () => deleteRoadPoint(pointIndex);
    row.querySelectorAll("input").forEach((input) => input.onchange = (event) => {
      // A number input reports invalid or cleared content as "", which Number()
      // would turn into 0 and quietly warp the reference line.
      const points = [...body.rows].map((candidate) => [...candidate.querySelectorAll("input")].map((entry) => (
        entry.value.trim() === "" ? NaN : Number(entry.value)
      )));
      if (points.flat().some((coordinate) => !Number.isFinite(coordinate))) {
        markFieldInvalid(event.currentTarget, "Road point coordinates must be numeric");
        return;
      }
      updateRoadPoints(points, event.currentTarget);
    });
    body.append(row);
  });
}

/** Replace the selected road's reference line and reload all derived map geometry. */
async function updateRoadPoints(points, sourceField = null) {
  const index = selectedRoadIndex();
  if (index === undefined) return;
  try {
    await api(`/api/map/roads/${index}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points }),
    });
    await refresh();
  } catch (error) {
    markFieldInvalid(sourceField, error.message);
  }
}

/** Ask the backend to interpolate a new control point at the requested table index. */
async function insertRoadPoint(pointIndex) {
  const index = selectedRoadIndex();
  if (index === undefined) return;
  try {
    state.scenario = await api(
      `/api/map/roads/${index}/points/${pointIndex}/insert`,
      { method: "POST" },
    );
    render();
    const remainingCount = state.scenario.map.roads[index]?.points.length || 0;
    const nextFocus = remainingCount
      ? document.getElementById(`road-point-${Math.min(pointIndex, remainingCount - 1)}-remove`)
      : $("#add-road-point");
    nextFocus?.focus();
  } catch (error) {
    setStatus(error.message);
  }
}

/** Remove one control point and render the backend's validated road state. */
async function deleteRoadPoint(pointIndex) {
  const index = selectedRoadIndex();
  if (index === undefined || !confirm(`Remove road point ${pointIndex + 1}?`)) return;
  try {
    state.scenario = await api(
      `/api/map/roads/${index}/points/${pointIndex}`,
      { method: "DELETE" },
    );
    render();
  } catch (error) {
    setStatus(error.message);
  }
}

/** Expose predecessor, successor, and lane links only for editable reference roads. */
function renderRoadRelations() {
  const body = $("#relations-body"); body.replaceChildren();
  state.scenario.map.roads.forEach((road, index) => {
    if (road.kind !== "reference") return;
    const row = document.createElement("tr");
    const roadName = road.name || `road_${index + 1}`;
    row.innerHTML = `<td><button type="button" class="connect">Link</button><button type="button" class="clear">Clear</button></td><td><button type="button" class="road"></button></td><td><input></td><td><input></td><td><input></td><td><input></td>`;
    row.querySelector(".connect").id = `road-relation-${index}-connect`;
    row.querySelector(".clear").id = `road-relation-${index}-clear`;
    const inputs = row.querySelectorAll("input");
    ["predecessor road", "predecessor lane links", "successor road", "successor lane links"].forEach((label, inputIndex) => {
      inputs[inputIndex].id = `road-relation-${index}-${inputIndex}`;
      inputs[inputIndex].setAttribute("aria-label", `${roadName}, ${label}`);
    });
    [road.predecessor_road, road.predecessor_lane_links, road.successor_road, road.successor_lane_links].forEach((value, inputIndex) => { inputs[inputIndex].value = value || ""; });
    const roadButton = row.querySelector(".road");
    roadButton.id = `road-relation-${index}-select`;
    roadButton.textContent = roadName;
    roadButton.setAttribute("aria-pressed", String(state.selectedRoad === index));
    row.querySelector(".connect").setAttribute("aria-label", `Link: connect lanes for ${roadName}`);
    row.querySelector(".clear").setAttribute("aria-label", `Clear road connections for ${roadName}`);
    roadButton.onclick = () => { state.selectedRoad = index; render(); };
    row.querySelector(".connect").onclick = () => { state.selectedRoad = index; state.connectionMode = true; state.connectionSource = null; render(); setStatus("Connect mode: select a source lane, then a target lane"); };
    row.querySelector(".clear").onclick = async () => {
      if (!confirm(`Clear all road connections for ${roadName}?`)) return;
      try {
        await api(`/api/map/roads/${index}/connections/clear`, { method: "POST" });
        await refresh();
      } catch (error) {
        setStatus(error.message);
      }
    };
    inputs.forEach((input) => input.onchange = (event) => updateRoadRelation(index, inputs, event.currentTarget));
    body.append(row);
  });
}

/** Submit all relation cells together so road and lane links remain consistent. */
async function updateRoadRelation(index, inputs, sourceField = null) {
  try {
    await api(`/api/map/roads/${index}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ predecessor_road: inputs[0].value, predecessor_lane_links: inputs[1].value, successor_road: inputs[2].value, successor_lane_links: inputs[3].value }) });
    state.selectedRoad = index; await refresh();
  } catch (error) { await refresh(); markFieldInvalid(document.getElementById(sourceField?.id), error.message); }
}

/** Build JSON-only perception intervals and keep actor choices aligned with state. */
function renderDetectionGaps() {
  const body = $("#gap-body"); body.replaceChildren();
  state.scenario.detection_gaps.forEach((gap, index) => {
    const row = document.createElement("tr"); row.innerHTML = `<td><select id="gap-${index}-actor" aria-label="Perception gap ${index + 1}, actor"></select></td><td><input id="gap-${index}-start" aria-label="Perception gap ${index + 1}, start time in seconds" type="number" step="any" value="${gap.start_time_s}"></td><td><input id="gap-${index}-end" aria-label="Perception gap ${index + 1}, end time in seconds" type="number" step="any" value="${gap.end_time_s}"></td><td><button id="gap-${index}-remove" type="button" aria-label="Remove (−) perception gap ${index + 1}">−</button></td>`;
    const selector = row.querySelector("select"); state.scenario.actors.forEach((actor) => { const option = new Option(actor.name, actor.name, false, actor.name === gap.vehicle_name); selector.add(option); });
    row.querySelectorAll("input, select").forEach((input) => input.onchange = (event) => updateGap(index, row, event.currentTarget)); row.querySelector("button").onclick = () => deleteGap(index); body.append(row);
  });
}

/** Validate and persist the actor and time bounds represented by one gap row. */
async function updateGap(index, row, sourceField = null) {
  const inputs = row.querySelectorAll("input");
  try {
    await api(`/api/detection-gaps/${index}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vehicle_name: row.querySelector("select").value,
        start_time_s: Number(inputs[0].value),
        end_time_s: Number(inputs[1].value),
      }),
    });
    await refresh();
  } catch (error) {
    markFieldInvalid(sourceField, error.message);
  }
}

/** Delete a perception interval, then refresh indexes used by the remaining rows. */
async function deleteGap(index) {
  if (!confirm(`Remove perception gap ${index + 1}?`)) return;
  await api(`/api/detection-gaps/${index}`, { method: "DELETE" });
  await refresh();
  const remainingCount = state.scenario.detection_gaps.length;
  const nextFocus = remainingCount
    ? document.getElementById(`gap-${Math.min(index, remainingCount - 1)}-remove`)
    : $("#add-gap");
  nextFocus?.focus();
}
/** Persist shared settings immediately because multiple views depend on each value. */
async function updateSettings(payload, sourceField = null) {
  try {
    await api("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refresh();
  } catch (error) {
    if (sourceField) markFieldInvalid(sourceField, error.message);
    else setStatus(error.message);
  }
}

// Mode and CRUD controls below always round-trip through the authoritative backend.
$("#mode-toggle").onclick = async () => {
  try {
    if (state.scenario.settings.map_mode) {
      state.scenario = await api("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ map_mode: false }) });
      render();
      return;
    }
    state.scenario = await api("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ map_mode: true }) });
    state.selectedRoad = state.scenario.map.roads.length ? 0 : undefined;
    render();
  } catch (error) { setStatus(error.message); }
};
$("#add-actor").onclick = async () => {
  if (!state.scenario.settings.map_mode) {
    const result = await api("/api/actors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    state.scenario = result.scenario;
    state.selected = result.name;
    render();
    return;
  }
  state.scenario = await api("/api/map/roads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  state.selectedRoad = state.scenario.map.roads.length - 1;
  render();
};
$("#add-road-point").onclick = () => { const road = state.scenario.map.roads[selectedRoadIndex()]; if (road) insertRoadPoint(road.points.length); };
$("#add-gap").onclick = async () => {
  const actor = selectedActor();
  if (!actor) return;
  await api("/api/detection-gaps", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      vehicle_name: actor.name,
      start_time_s: 0,
      end_time_s: 1,
    }),
  });
  await refresh();
};
$("#time-step").onchange = () => updateSettings({ time_step_s: Number($("#time-step").value) }, $("#time-step"));
$("#lane-snap").onchange = () => updateSettings({ lane_snap_enabled: $("#lane-snap").checked }, $("#lane-snap"));
$("#trajectory-direction").onclick = () => updateSettings({ trajectory_calculation_mode: state.scenario.settings.trajectory_calculation_mode === "forward" ? "backward" : "forward" });
$("#waypoint-timing").onclick = () => updateSettings({ waypoint_timing_mode: state.scenario.settings.waypoint_timing_mode === "fixed_time" ? "constant_speed" : "fixed_time" });
$("#export-2d").onchange = () => updateSettings({ export_2d: $("#export-2d").checked }, $("#export-2d"));
$("#show-sqc-warnings").onchange = () => updateSettings({ show_sqc_warnings: $("#show-sqc-warnings").checked }, $("#show-sqc-warnings"));
$("#show-sqc-errors").onchange = () => updateSettings({ show_sqc_errors: $("#show-sqc-errors").checked }, $("#show-sqc-errors"));
$("#show-tooltips").onchange = () => updateSettings({ tooltips_enabled: $("#show-tooltips").checked }, $("#show-tooltips"));
$("#show-adjustment-warnings").onchange = () => updateSettings({ adjustment_warnings_enabled: $("#show-adjustment-warnings").checked }, $("#show-adjustment-warnings"));
$("#clear-actor").onclick = async () => {
  const actor = selectedActor();
  if (!actor || !confirm(`Clear all trajectory points for ${actor.name}?`)) return;
  try {
    state.scenario = await api(`/api/actors/${actor.name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ waypoints: [] }),
    });
    state.selected = actor.name;
    render();
    setStatus("Trajectory cleared");
  } catch (error) {
    setStatus(error.message);
  }
};
$("#delete-last-point").onclick = () => { const actor = selectedActor(); if (actor?.waypoints.length) deleteWaypoint(actor.waypoints.length - 1); };
$("#clear-road-links").onclick = async () => {
  const index = selectedRoadIndex();
  if (index === undefined || !confirm("Clear all links of the selected road?")) return;
  try {
    await api(`/api/map/roads/${index}/connections/clear`, { method: "POST" });
    await refresh();
  } catch (error) {
    setStatus(error.message);
  }
};
$("#connect-roads").onclick = () => { state.connectionMode = !state.connectionMode; state.connectionSource = null; $("#connect-roads").classList.toggle("active", state.connectionMode); $("#connect-roads").setAttribute("aria-pressed", String(state.connectionMode)); setStatus(state.connectionMode ? "Connect mode: select a source lane, then a target lane on the canvas" : "Connect mode cancelled"); };
$("#delete-last-road-point").onclick = () => { const road = state.scenario.map.roads[selectedRoadIndex()]; if (road?.points.length && confirm("Remove the last road waypoint?")) updateRoadPoints(road.points.slice(0, -1)); };
$("#delete-road").onclick = async () => {
  const index = selectedRoadIndex();
  if (index === undefined || !confirm("Delete the selected road?")) return;
  try {
    await api(`/api/map/roads/${index}`, { method: "DELETE" });
    state.selectedRoad = undefined;
    await refresh();
  } catch (error) {
    setStatus(error.message);
  }
};
$("#clear-map").onclick = async () => {
  if (!confirm("Clear the loaded map?")) return;
  await api("/api/map", { method: "DELETE" });
  await refresh();
};
/** Size quality dialogs from their longest finding, capped at half the viewport. */
function sizeQualityDialog(report, selector = "#quality-dialog") {
  const lines = [...report.problems, ...report.warnings].flatMap((entry) => String(entry).split("\n"));
  const longest = Math.max(0, ...lines.map((entry) => entry.length));
  const width = Math.min(window.innerWidth - 28, Math.max(280, longest * 7 + 72));
  $(selector).style.width = `${width}px`;
}
// Quality dialogs share report rendering, while the PDF action downloads a file.
$("#quality-check").onclick = async () => {
  setStatus("Running Scenario Quality Checker...");
  try {
    const report = await api("/api/quality-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_name: $("#output-name").value,
        additional_information: additionalInformation(),
      }),
    });
    const result = $("#quality-result");
    result.replaceChildren();
    const sections = [
      ["Problems", state.scenario.settings.show_sqc_errors ? report.problems : [], "quality-problems"],
      ["Warnings", state.scenario.settings.show_sqc_warnings ? report.warnings : [], "quality-warnings"],
    ];
    sections.forEach(([title, issues, className]) => {
      const heading = document.createElement("h3");
      heading.textContent = `${title} (${issues.length})`;
      const list = document.createElement("ul");
      list.className = className;
      issues.forEach((issue) => {
        const item = document.createElement("li");
        item.textContent = issue;
        list.append(item);
      });
      result.append(heading, list);
    });
    if (!report.warnings.length && !report.problems.length) result.append("No issues found.");
    sizeQualityDialog(report);
    $("#quality-dialog").showModal();
    setStatus("Scenario Quality Checker completed");
  } catch (error) {
    setStatus(error.message);
  }
};
$("#quality-pdf").onclick = async () => {
  setStatus("Creating Scenario Quality Checker PDF...");
  try {
    const response = await api("/api/quality-check/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_name: $("#output-name").value,
        additional_information: additionalInformation(),
      }),
    });
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "scenario_quality_report.pdf";
    link.click();
    URL.revokeObjectURL(url);
    setStatus("Scenario Quality Checker PDF ready");
  } catch (error) {
    setStatus(error.message);
  }
};
$("#close-quality").onclick = () => $("#quality-dialog").close();
$("#cancel-export-quality").onclick = () => {
  $("#export-quality-dialog").close();
  exportQualityConfirmation?.(false);
  exportQualityConfirmation = null;
};
$("#confirm-export-quality").onclick = () => {
  $("#export-quality-dialog").close();
  exportQualityConfirmation?.(true);
  exportQualityConfirmation = null;
};
$("#export-quality-dialog").oncancel = () => {
  exportQualityConfirmation?.(false);
  exportQualityConfirmation = null;
};
// Informational dialogs fetch maintained server content when opened.
$("#help").onclick = async () => {
  try {
    const help = await api("/api/help");
    $("#help-content").innerHTML = help.html;
    $("#help").closest("details").open = false;
    $("#help-dialog").showModal();
  } catch (error) {
    setStatus(error.message);
  }
};
$("#close-help").onclick = () => $("#help-dialog").close();
["#help-dialog", "#data-privacy-dialog"].forEach((selector) => {
  $(selector).addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
});
$("#data-privacy").onclick = () => $("#data-privacy-dialog").showModal();
$("#close-data-privacy").onclick = () => $("#data-privacy-dialog").close();
$("#delete-my-data").onclick = async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/session", { method: "DELETE" });
    window.location.reload();
  } catch (error) {
    button.disabled = false;
    setStatus(error.message);
  }
};
$("#about").onclick = async () => {
  try {
    const about = await api("/api/about");
    $("#about-content").innerHTML = about.html;
    $("#about-dialog").showModal();
  } catch (error) {
    setStatus(error.message);
  }
};
$("#close-about").onclick = () => $("#about-dialog").close();
