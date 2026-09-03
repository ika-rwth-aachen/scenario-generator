// Scenario state, actor inspector, trajectory table, and additional information UI.

// Shared client-side state. The server remains authoritative; this object only
// represents the most recently fetched scenario and transient UI interaction.
const state = { scenario: null, selected: null, view: null, camera: null, playbackTime: 0, measurementPoints: [], measurementMode: "off", measurementTool: null, playing: false, dragTarget: null, didDrag: false, keyboardCursorVisible: false, metrics: {}, suppressCanvasClick: false, additionalInformationDraft: null };
// Keep DOM lookups concise without introducing a client-side framework.
const $ = (selector) => document.querySelector(selector);
const basePathMetadata = document.querySelector('meta[name="scenario-generator-base-path"]');
const applicationBasePath = basePathMetadata ? basePathMetadata.content : "";

/** Resolve an application path without escaping an optional deployment prefix. */
function applicationUrl(path) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${applicationBasePath}${normalizedPath}`;
}
// Keep the palette aligned with the established desktop canvas.
const actorColors = ["#a64b4b", "#416b94", "#39704a", "#8c5a2b", "#73578c", "#2f6f73"];
const tooltipSpecs = [
  ["#add-actor", () => state.scenario.settings.map_mode
    ? "Add an empty road to the map, then select canvas positions or use the Road waypoints table to define its reference line."
    : "Add another road user to the scenario. Configure its type, dimensions, action, parameters, and controller in the Actor inspector."],
  ["#mode-toggle", "Switch editing context. Entering Map mode alone does not create a map; select a canvas position or use Add road to start one."],
  ["#time-step", "Used only by 'New points: fixed time step': each clicked point is initially placed this many seconds after the previous point."],
  ["#trajectory-direction", "Applies when you edit an existing point's time, speed, or position. Forward keeps earlier points fixed and recalculates later ones; backward keeps later points fixed and recalculates earlier ones. It does not change the direction in which newly clicked points are appended."],
  ["#waypoint-timing", "Controls only the timestamp assigned to the next clicked trajectory point. Fixed time step uses the value above. Preserve speed derives the time from the previous segment's average speed, so the difference is visible when successive segment lengths differ."],
  ["#lane-snap", "For a loaded map, snap new or moved trajectory points to the centreline of a compatible lane. It may also use that lane's speed limit."],
  ["#delete-last-point", "Remove the most recently added trajectory point for the selected actor."],
  ["#clear-actor", "Remove every trajectory point for the selected actor. The actor itself remains available."],
  ["#map-canvas", "Pointer: select empty space to append a point, drag a point to move it, use the wheel to zoom, and right/middle drag to pan. Keyboard: use arrows for the cursor, Enter to select, plus/minus to zoom, and Alt plus arrows to pan. Exact point values are also available in the tables."],
  ["#fit-view", "Reset the viewport so all currently visible roads and trajectories fit on the canvas."],
  ["#connect-roads", "Connect two lanes by selecting a source lane and then a target lane on the canvas with a pointer or the keyboard cursor."],
  ["#delete-last-road-point", "Remove the final reference-line point from the selected road."],
  ["#delete-road", "Remove the selected editable road and its links."],
  ["#clear-map", "Remove the current map from this scenario. Exported or uploaded source files are not deleted."],
  ["#actor-action", "Choose the OpenSCENARIO motion action: Trajectory follows the clicked path; Route exports waypoints as a route; Reach position exports only the final target; Clear trajectory omits motion."],
  ["#edit-parameters", "Open per-actor OpenSCENARIO parameter declarations. These are exported with the actor."],
  ["#environment-enabled", "Include environment data such as weather and time of day in the OpenSCENARIO export."],
  ["#quality-check", "Run the Scenario Quality Checker for the current scenario without first downloading an export."],
  ["#output-name", "Base name for the generated ZIP and selected exported files."],
  ["#batch-export-xosc", "Include an OpenSCENARIO .xosc file. When a map is selected, it references the accompanying OpenDRIVE export."],
  ["#batch-export-xodr", "Request the current editable or imported map as an OpenDRIVE .xodr file. A map referenced by the scenario config is included automatically as a project dependency."],
  ["#batch-export-json", "Include the defined trajectories and actor data as JSON."],
  ["#batch-export-mcap", "Include an Omega-Prime .mcap recording when the export has enough scenario data."],
  ["#edit-postprocessing", "Select an approved postprocessing tool and configure its parameters. Selected tools run on the generated files before the ZIP is downloaded."],
  ["#generate-files", "Create a ZIP containing the scenario configuration, its map dependency when present, and all selected exports. Configured post-processors run before the files are bundled."],
];
let exportQualityConfirmation = null;
let mutationQueue = Promise.resolve();

/** Present operation feedback and keep the header's loading indicator consistent. */
function setStatus(message, loading = false) {
  const status = $("#status");
  status.textContent = message;
  status.classList.toggle("loading", loading);
  status.setAttribute("aria-busy", String(loading));
}

/**
 * Add or remove one description id without disturbing the other descriptions a
 * control already carries, such as the canvas instructions and summary.
 */
function toggleDescribedBy(element, id, present) {
  if (!element) return;
  const tokens = (element.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  const remaining = tokens.filter((token) => token !== id);
  if (present) remaining.push(id);
  if (remaining.length) element.setAttribute("aria-describedby", remaining.join(" "));
  else element.removeAttribute("aria-describedby");
}

/** Associate a validation failure with the control that caused it. */
function markFieldInvalid(field, message) {
  setStatus(message);
  if (!field) return;
  field.setAttribute("aria-invalid", "true");
  toggleDescribedBy(field, "status", true);
  field.focus();
}

document.addEventListener("input", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) return;
  field.removeAttribute("aria-invalid");
  toggleDescribedBy(field, "status", false);
});

/**
 * Call the backend, protecting imported maps and serializing state mutations so
 * slower responses cannot overwrite a newer edit.
 */
async function api(path, options = {}, parseJson = true) {
  const method = (options.method || "GET").toUpperCase();
  const modifiesImportedMap = method !== "GET"
    && (path.startsWith("/api/map/roads") || path === "/api/map/connections")
    && !state.scenario?.map.edit_enabled
    && state.scenario.map.roads.length > 0;
  if (modifiesImportedMap) {
    const proceed = confirm(
      "Modify the imported map? An editable copy will be created; the original uploaded file remains unchanged.",
    );
    if (!proceed) throw new Error("Map modification cancelled.");
    state.scenario = await api("/api/map/editing", { method: "POST" });
  }
  // Serialize mutations so an older browser request cannot overwrite a newer one.
  const request = async () => {
    const response = await fetch(applicationUrl(path), options);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || response.statusText);
    }
    return parseJson && response.headers.get("content-type")?.includes("application/json")
      ? response.json()
      : response;
  };
  if ((options.method || "GET").toUpperCase() === "GET") return request();
  const pending = mutationQueue.then(request, request);
  mutationQueue = pending.catch(() => undefined);
  return pending;
}

/** Reload authoritative state while retaining a still-valid actor selection. */
async function refresh(selectName) {
  state.scenario = await api("/api/scenario");
  state.selected = selectName || state.selected || state.scenario.actors[0]?.name;
  if (!state.scenario.actors.some((actor) => actor.name === state.selected)) {
    state.selected = state.scenario.actors[0]?.name;
  }
  render();
}

/** Resolve the selected actor from the latest server snapshot. */
function selectedActor() {
  return state.scenario.actors.find((actor) => actor.name === state.selected);
}

/** Return editable road indexes, excluding lane polygons and other helper geometry. */
function referenceRoadIndexes() {
  return state.scenario.map.roads.flatMap((road, index) => road.kind === "reference" ? [index] : []);
}

/** Keep road selection valid and fall back to the first editable reference road. */
function selectedRoadIndex() {
  const selected = state.selectedRoad;
  if (Number.isInteger(selected) && state.scenario.map.roads[selected]?.kind === "reference") return selected;
  const [firstRoad] = referenceRoadIndexes();
  state.selectedRoad = firstRoad;
  return firstRoad;
}

/** Assign a form value while normalizing null and undefined to an empty field. */
function setValue(selector, value) {
  $(selector).value = value ?? "";
}

/** Render every state-derived panel from one internally consistent snapshot. */
function render() {
  const focusedId = document.activeElement?.id;
  syncPlaybackRange();
  renderActors();
  renderInspector();
  renderPoints();
  renderSpeedProfile();
  renderMapEditor();
  renderRoadRelations();
  renderDetectionGaps();
  renderMode();
  renderAdditionalInformation();
  draw();
  applyTooltips();
  updateCanvasSummary();
  if (focusedId) requestAnimationFrame(() => {
    if (document.activeElement === document.body || document.activeElement === document.documentElement) {
      document.getElementById(focusedId)?.focus();
    }
  });
}

/** Keep an equivalent text summary of the visual scenario available to assistive technology. */
function updateCanvasSummary() {
  const scenario = state.scenario;
  if (!scenario) return;
  const actorSummary = scenario.actors.map((actor) => (
    `${actor.name}, ${actor.dimensions.actor_type}, ${actor.waypoints.length} trajectory points, ${trajectoryActiveAt(actor, currentPlaybackTime()) ? "active at the current playback time" : "outside its active trajectory time"}`
  )).join("; ");
  const roadCount = scenario.map.roads.filter((road) => road.kind === "reference").length;
  $("#canvas-summary").textContent = `Scenario contains ${scenario.actors.length} actors and ${roadCount} roads. ${actorSummary}`;
}

/** Clamp playback to the current scenario duration after trajectory changes. */
function syncPlaybackRange() {
  const slider = $("#time-slider");
  const endTime = scenarioEndTime();
  slider.max = endTime;
  state.playbackTime = Math.min(currentPlaybackTime(), endTime);
  slider.value = state.playbackTime;
  $("#time-output").value = `${state.playbackTime.toFixed(2)} s`;
}

// One shared tooltip element and hide timer; re-creating the timer per render
// could otherwise hide a tooltip the pointer is still resting on.
const accessibleTooltip = (() => {
  const tooltip = document.createElement("div");
  tooltip.id = "accessible-tooltip";
  tooltip.className = "accessible-tooltip";
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  document.body.append(tooltip);
  let hideTimer;
  const show = (element, content) => {
    window.clearTimeout(hideTimer);
    tooltip.textContent = content;
    tooltip.hidden = false;
    tooltip.classList.remove("hoverable");
    const bounds = element.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.min(window.innerHeight - tooltip.offsetHeight - 8, bounds.bottom + 6)}px`;
  };
  const hide = (allowPointerEntry = false) => {
    tooltip.classList.toggle("hoverable", allowPointerEntry);
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => { tooltip.hidden = true; }, allowPointerEntry ? 300 : 75);
  };
  tooltip.onmouseenter = () => window.clearTimeout(hideTimer);
  tooltip.onmouseleave = () => hide(false);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") tooltip.hidden = true;
  });
  return { show, hide };
})();

/** Expose optional contextual help on pointer hover and keyboard focus. */
function applyTooltips() {
  const enabled = Boolean(state.scenario?.settings.tooltips_enabled);
  const { show, hide } = accessibleTooltip;
  tooltipSpecs.forEach(([selector, text]) => {
    document.querySelectorAll(selector).forEach((element) => {
      if (enabled) {
        const content = typeof text === "function" ? text() : text;
        toggleDescribedBy(element, "accessible-tooltip", true);
        element.removeAttribute("title");
        element.onmouseenter = () => show(element, content);
        element.onmouseleave = () => hide(true);
        element.onfocus = () => show(element, content);
        element.onblur = () => hide(false);
        element.dataset.scenarioGeneratorTooltip = "true";
      } else if (element.dataset.scenarioGeneratorTooltip === "true") {
        toggleDescribedBy(element, "accessible-tooltip", false);
        element.onmouseenter = null;
        element.onmouseleave = null;
        element.onfocus = null;
        element.onblur = null;
        delete element.dataset.scenarioGeneratorTooltip;
      }
    });
  });
}

/** Rebuild actor selection and attach the inline rename interaction. */
function renderActors() {
  const list = $("#actor-list");
  list.replaceChildren();
  state.scenario.actors.forEach((actor, index) => {
    const node = $("#actor-template").content.firstElementChild.cloneNode(true);
    node.id = `actor-choice-${index}`;
    node.querySelector(".actor-name").textContent = actor.name;
    node.querySelector("small").textContent = actor.dimensions.actor_type;
    node.querySelector(".actor-dot").style.backgroundColor = actorColor(actor);
    node.classList.toggle("active", actor.name === state.selected);
    node.setAttribute("aria-pressed", String(actor.name === state.selected));
    node.setAttribute("aria-label", `${actor.name}, ${actor.dimensions.actor_type}${actor.name === state.selected ? ", selected" : ""}`);
    node.onclick = () => {
      state.selected = actor.name;
      render();
    };
    node.ondblclick = async (event) => {
      event.preventDefault();
      const newName = window.prompt("Actor name", actor.name)?.trim();
      if (!newName || newName === actor.name) return;
      try {
        const result = await api(`/api/actors/${actor.name}/rename`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        state.scenario = result.scenario;
        state.selected = result.name;
        render();
      } catch (error) {
        setStatus(error.message);
      }
    };
    list.append(node);
  });
}

/** Build the selected actor's dynamic inspector, including controller file actions. */
function renderInspector() {
  if (state.scenario.settings.map_mode) return;
  const actor = selectedActor();
  if (!actor) return;
  $("#inspector-title").textContent = "Actor inspector";
  const dimensions = actor.dimensions;
  $("#actor-form").innerHTML = `
    <label>Name<input id="actor-name"></label>
    <details class="inspector-section" open><summary>Static information</summary>
      <label>Actor type<select id="actor-type"><option value="vehicle">Vehicle</option><option value="cyclist">Cyclist</option><option value="pedestrian">Pedestrian</option></select></label>
      <label>CARLA blueprint<select id="carla-blueprint"></select></label>
      <label>Length [m]<input id="length" type="number" step="0.001"></label>
      <label>Width [m]<input id="width" type="number" step="0.001"></label>
      <label>Height [m]<input id="height" type="number" step="0.001"></label>
    </details>
    <details class="inspector-section" open><summary>Actions</summary>
      <label>Action<select id="actor-action"><option value="trajectory">Trajectory</option><option value="route">Route</option><option value="reach_position">Reach position</option><option value="clear_trajectory">Clear trajectory</option></select></label>
    </details>
    <details class="inspector-section"><summary>Parameter declarations</summary>
      <button id="edit-parameters" class="secondary">Edit parameter declarations</button>
    </details>
    <details class="inspector-section"><summary>Controller</summary>
      <label>Controller name<input id="controller-name"></label>
      <label>Controller XML<textarea id="controller-xml" rows="4" spellcheck="false"></textarea></label>
      <div class="inline-actions"><button id="load-controller" class="secondary" type="button">Load<br>controller</button><input id="controller-upload" type="file" accept=".json,.xml,.xosc" hidden /><button id="clear-controller" class="secondary">Clear<br>controller</button><button id="save-controller-json" class="secondary">Save<br>controller</button></div>
    </details>`;
  setValue("#actor-name", actor.name);
  setValue("#actor-type", dimensions.actor_type);
  renderBlueprintOptions(dimensions.actor_type, dimensions.carla_blueprint);
  setValue("#actor-action", dimensions.xosc_export_mode);
  setValue("#length", dimensions.length_m);
  setValue("#width", dimensions.width_m);
  setValue("#height", dimensions.height_m);
  setValue("#controller-name", dimensions.controller_name);
  setValue("#controller-xml", dimensions.controller_xml);
  ["actor-type", "actor-action", "length", "width", "height", "controller-name", "controller-xml"].forEach((id) => {
    $("#" + id).onchange = (event) => saveActor(event.currentTarget);
  });
  $("#actor-type").onchange = (event) => {
    renderBlueprintOptions($("#actor-type").value, "");
    saveActor(event.currentTarget);
  };
  $("#carla-blueprint").onchange = (event) => {
    const entry = blueprintEntry(
      $("#actor-type").value,
      $("#carla-blueprint").value,
    );
    if (entry?.dimensions) {
      setValue("#length", entry.dimensions.length_m);
      setValue("#width", entry.dimensions.width_m);
      setValue("#height", entry.dimensions.height_m);
    }
    saveActor(event.currentTarget);
  };
  $("#load-controller").onclick = () => $("#controller-upload").click();
  $("#controller-upload").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const text = await file.text();
    try {
      const template = file.name.toLowerCase().endsWith(".json")
        ? JSON.parse(text)
        : null;
      $("#controller-xml").value = template
        ? String(template.xml || template.controller_xml || "")
        : text;
    } catch (error) {
      markFieldInvalid($("#controller-xml"), `Controller template load failed: ${error.message}`);
      return;
    }
    if (!$("#controller-xml").value.trim()) {
      markFieldInvalid($("#controller-xml"), "Controller template is empty. Choose a template that contains controller XML.");
      return;
    }
    if (!$("#controller-name").value) $("#controller-name").value = file.name.replace(/\.(json|xml|xosc)$/i, "");
    saveActor($("#controller-xml"));
  };
  $("#clear-controller").onclick = () => {
    if (!confirm(`Clear the controller configuration for ${actor.name}?`)) return;
    $("#controller-name").value = "";
    $("#controller-xml").value = "";
    saveActor($("#controller-name"));
    setStatus("Cleared controller template");
  };
  $("#save-controller-json").onclick = () => {
    const name = $("#controller-name").value || "controller";
    const content = JSON.stringify({ name, xml: $("#controller-xml").value.trim() }, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  $("#edit-parameters").onclick = () => {
    renderParameterRows(dimensions.parameter_declarations);
    $("#parameter-dialog").showModal();
  };
  $("#actor-name").onchange = async () => {
    const newName = $("#actor-name").value.trim();
    if (!newName || newName === actor.name) return;
    try {
      const result = await api(`/api/actors/${actor.name}/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      state.scenario = result.scenario;
      state.selected = result.name;
      render();
    } catch (error) {
      setValue("#actor-name", actor.name);
      markFieldInvalid($("#actor-name"), error.message);
    }
  };
  $("#delete-actor").disabled = state.scenario.actors.length <= 1;
}

/** Persist inspector values and report any server-side dimension clamping. */
async function saveActor(sourceField = null) {
  const actor = selectedActor();
  const dimensionInputs = [["length", "Length"], ["width", "Width"], ["height", "Height"]];
  const adjustments = dimensionInputs.flatMap(([id, label]) => Number($("#" + id).value) < 0.001 ? [`${label}=0.001 m`] : []);
  try {
    await api(`/api/actors/${actor.name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_type: $("#actor-type").value,
        carla_blueprint: $("#carla-blueprint").value,
        xosc_export_mode: $("#actor-action").value,
        length_m: Number($("#length").value),
        width_m: Number($("#width").value),
        height_m: Number($("#height").value),
        controller_name: $("#controller-name").value,
        controller_xml: $("#controller-xml").value,
      }),
    });
    await refresh(actor.name);
    if (adjustments.length) {
      const message = `Vehicle dimensions must be at least 0.001 m. Applied ${adjustments.join(", ")}.`;
      setStatus(message);
      if (state.scenario.settings.adjustment_warnings_enabled) window.alert(message);
    }
  } catch (error) {
    markFieldInvalid(sourceField, error.message);
  }
}

/** Select the CARLA catalog category associated with one scenario actor type. */
function blueprintEntries(actorType) {
  const catalog = state.scenario.carla_blueprints || {};
  const category = actorType === "pedestrian" ? "pedestrians" : `${actorType}s`;
  return catalog[category] || [];
}

/** Resolve one blueprint so its physical dimensions can seed the inspector. */
function blueprintEntry(actorType, id) {
  return blueprintEntries(actorType).find((entry) => entry.id === id);
}

/** Repopulate blueprint choices after an actor-type change without stale options. */
function renderBlueprintOptions(actorType, selectedId) {
  const select = $("#carla-blueprint");
  select.replaceChildren(new Option("Default", ""));
  blueprintEntries(actorType).forEach((entry) => {
    select.add(new Option(entry.label, entry.id));
  });
  select.value = selectedId || "";
}

/** Rebuild trajectory rows and bind each editable value to an immediate update. */
function renderPoints() {
  const actor = selectedActor();
  const body = $("#waypoint-body");
  body.replaceChildren();
  if (!actor) return;
  actor.waypoints.forEach((point, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td><button id="waypoint-${index}-insert" type="button" class="insert" aria-label="Insert (+) trajectory point before point ${index + 1}">+</button><button id="waypoint-${index}-remove" type="button" class="remove" aria-label="Remove (−) trajectory point ${index + 1}">−</button></td>` +
      ["time_s", "x_m", "y_m", "speed_mps"]
        .map((key) => `<td><input id="waypoint-${index}-${key}" type="number" step="any" data-key="${key}"></td>`)
        .join("");
    const fieldLabels = {
      time_s: "time in seconds",
      x_m: "X coordinate in metres",
      y_m: "Y coordinate in metres",
      speed_mps: "speed in metres per second",
    };
    row.querySelectorAll("input[data-key]").forEach((input) => {
      input.value = point[input.dataset.key] ?? "";
      input.setAttribute("aria-label", `${actor.name}, trajectory point ${index + 1}, ${fieldLabels[input.dataset.key]}`);
      input.onchange = () => updateWaypoint(index, input.dataset.key, input.value);
      input.ondblclick = () => { input.focus(); input.select(); };
      input.onkeydown = (event) => { if (event.key === "Enter") input.blur(); };
    });
    row.querySelector(".insert").onclick = () => insertWaypoint(index);
    row.querySelector(".remove").onclick = () => deleteWaypoint(index);
    body.append(row);
  });
}

/** Route speed edits through their specialized endpoint and restore rejected input. */
async function updateWaypoint(index, key, rawValue) {
  const actor = selectedActor();
  // A number input reports invalid or cleared content as "", which Number() would
  // silently turn into 0, so treat blank input as a rejection rather than a value.
  const value = String(rawValue).trim() === "" ? NaN : Number(rawValue);
  if (!actor || !Number.isFinite(value)) {
    setStatus("Waypoint values must be numeric");
    renderPoints();
    const input = document.getElementById(`waypoint-${index}-${key}`);
    input?.setAttribute("aria-invalid", "true");
    toggleDescribedBy(input, "status", true);
    input?.focus();
    return;
  }
  const path = key === "speed_mps"
    ? `/api/actors/${actor.name}/waypoints/${index}/speed`
    : `/api/actors/${actor.name}/waypoints/${index}`;
  const payload = key === "speed_mps" ? { speed_mps: value } : { [key]: value };
  try {
    state.scenario = await api(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.selected = actor.name;
    render();
    setStatus("Trajectory updated");
  } catch (error) {
    setStatus(error.message);
    await refresh(actor.name);
    const input = document.getElementById(`waypoint-${index}-${key}`);
    input?.setAttribute("aria-invalid", "true");
    toggleDescribedBy(input, "status", true);
    input?.focus();
  }
}

/** Insert a backend-derived waypoint before the selected table position. */
async function insertWaypoint(index) {
  const actor = selectedActor();
  if (!actor) return;
  try {
    state.scenario = await api(`/api/actors/${actor.name}/waypoints/${index}/insert`, { method: "POST" });
    state.selected = actor.name;
    render();
  } catch (error) {
    setStatus(error.message);
  }
}

/** Delete one point and accept the backend's recalculated trajectory timing. */
async function deleteWaypoint(index) {
  const actor = selectedActor();
  if (!actor || !confirm(`Remove trajectory point ${index + 1} from ${actor.name}?`)) return;
  try {
    state.scenario = await api(`/api/actors/${actor.name}/waypoints/${index}`, { method: "DELETE" });
    state.selected = actor.name;
    render();
    const remainingCount = selectedActor()?.waypoints.length || 0;
    const nextFocus = remainingCount
      ? document.getElementById(`waypoint-${Math.min(index, remainingCount - 1)}-remove`)
      : $("#add-point");
    nextFocus?.focus();
  } catch (error) {
    setStatus(error.message);
  }
}

/** Prefer sampled curve distances and fall back to straight control-point segments. */
function profileDistances(actor) {
  if (actor.profile_distances_m?.length === actor.waypoints.length) return actor.profile_distances_m;
  const distances = [0];
  for (let index = 1; index < actor.waypoints.length; index += 1) {
    const previous = actor.waypoints[index - 1]; const current = actor.waypoints[index];
    distances.push(distances.at(-1) + Math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m));
  }
  return distances;
}

/** Draw the editable speed profile and retain its geometry for pointer hit testing. */
function renderSpeedProfile() {
  const panel = $("#speed-profile");
  const enabled = state.scenario.settings.show_speed_profile && !state.scenario.settings.map_mode;
  panel.hidden = !enabled;
  if (!enabled) return;
  const actor = selectedActor(); const canvas = $("#speed-profile-canvas"); const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio; canvas.height = rect.height * devicePixelRatio;
  const context = canvas.getContext("2d"); context.scale(devicePixelRatio, devicePixelRatio); context.clearRect(0, 0, canvas.width, canvas.height);
  if (!actor?.waypoints.length) return;
  const distances = profileDistances(actor); const speeds = actor.waypoints.map((point) => Number(point.speed_mps || 0));
  const pad = { left: 42, right: 15, top: 12, bottom: 25 }; const width = rect.width - pad.left - pad.right; const height = rect.height - pad.top - pad.bottom;
  const maxDistance = Math.max(distances.at(-1), 1); const maxSpeed = Math.max(1, ...speeds) * 1.15;
  const pointFor = (index) => [pad.left + width * distances[index] / maxDistance, pad.top + height * (1 - speeds[index] / maxSpeed)];
  context.strokeStyle = "#718697"; context.lineWidth = 1; context.beginPath(); context.moveTo(pad.left, pad.top); context.lineTo(pad.left, pad.top + height); context.lineTo(pad.left + width, pad.top + height); context.stroke();
  context.strokeStyle = actorColor(actor); context.lineWidth = 2; context.beginPath(); actor.waypoints.forEach((_, index) => { const point = pointFor(index); index ? context.lineTo(...point) : context.moveTo(...point); }); context.stroke();
  actor.waypoints.forEach((point, index) => {
    const position = pointFor(index); context.fillStyle = "#eb5e28"; context.beginPath(); context.arc(...position, 5, 0, Math.PI * 2); context.fill();
    const labels = [];
    if (state.scenario.settings.show_point_indices) labels.push(`#${index + 1}`);
    if (state.scenario.settings.show_waypoint_times) labels.push(`t=${Number(point.time_s).toFixed(2)}s`);
    if (state.scenario.settings.show_speed_labels) labels.push(`v=${Number(point.speed_mps || 0).toFixed(2)}m/s`);
    if (labels.length) drawLabel(context, position[0], position[1], labels.join(" "), "#15211c");
  });
  if (state.scenario.settings.show_segment_average_speeds) actor.segment_speeds_mps.forEach((speed, index) => {
    const left = pointFor(index); const right = pointFor(index + 1);
    drawLabel(context, (left[0] + right[0]) / 2, (left[1] + right[1]) / 2, `v_avg=${speed.toFixed(2)}m/s`, actorColor(actor));
  });
  context.fillStyle = "#52635a"; context.font = "11px Helvetica, Arial, sans-serif"; context.fillText("Distance [m]", pad.left + width / 2 - 36, rect.height - 6);
  context.save(); context.translate(11, pad.top + height / 2 + 30); context.rotate(-Math.PI / 2); context.fillText("Speed [m/s]", 0, 0); context.restore();
  state.speedProfile = { actor, pointFor, maxSpeed, pad, height };
}

/** Submit all table rows as one ordered trajectory replacement. */
async function savePoints() {
  const actor = selectedActor();
  actor.waypoints = [...$("#waypoint-body").rows].map((row) => Object.fromEntries(
    [...row.querySelectorAll("input[data-key]")].map((input) => [
      input.dataset.key,
      input.value === "" ? null : Number(input.value),
    ]),
  ));
  try {
    await api(`/api/actors/${actor.name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ waypoints: actor.waypoints }),
    });
    await refresh(actor.name);
    setStatus("Trajectory updated");
  } catch (error) {
    setStatus(error.message);
  }
}

/** Fill export metadata from an unsaved draft first, then persisted server state. */
function renderAdditionalInformation() {
  const info = state.additionalInformationDraft || state.scenario.additional_information || {};
  const header = info.file_header || {};
  setValue("#file-author", header.author || "Author");
  setValue("#file-description", header.description || "Scenario created with scenario.generator");
  setValue("#xosc-rev-minor", header.revMinor ?? header.rev_minor ?? 1);
  setValue("#xosc-map-path", info.xosc_map_path || state.scenario.map.path.split("/").at(-1) || "");
  setValue("#simulation-factor", info.simulation_time_condition_factor || info.simulation_time_factor || 1);
  const environment = info.environment || {};
  $("#environment-enabled").checked = Boolean(Object.keys(environment).length);
  $("#environment-time-enabled").checked = "time_of_day" in environment;
  $("#environment-weather-enabled").checked = ["cloud_state", "sun_intensity", "sun_azimuth", "sun_elevation", "fog_visual_range", "precipitation_type", "precipitation_intensity"].some((key) => key in environment);
  $("#environment-road-enabled").checked = "road_friction" in environment;
  setValue("#environment-name", environment.name || "environment"); setValue("#environment-time", environment.time_of_day || "");
  setValue("#environment-cloud", environment.cloud_state || "free"); setValue("#environment-sun-intensity", environment.sun_intensity ?? 1);
  setValue("#environment-sun-azimuth", environment.sun_azimuth ?? 0); setValue("#environment-sun-elevation", environment.sun_elevation ?? 1);
  setValue("#environment-fog-range", environment.fog_visual_range ?? 100000); setValue("#environment-precipitation-type", environment.precipitation_type || "dry");
  setValue("#environment-precipitation-intensity", environment.precipitation_intensity ?? 0); setValue("#environment-road-friction", environment.road_friction ?? 1);
  $("#environment-fields").hidden = !$("#environment-enabled").checked;
}

/** Serialize only enabled environment sections plus export and postprocessing data. */
function additionalInformation() {
  const environment = $("#environment-enabled").checked ? { name: $("#environment-name").value || "environment" } : undefined;
  if (environment && $("#environment-time-enabled").checked) environment.time_of_day = $("#environment-time").value || "2026-06-16T12:00:00";
  if (environment && $("#environment-weather-enabled").checked) Object.assign(environment, { cloud_state: $("#environment-cloud").value || "free", sun_intensity: Number($("#environment-sun-intensity").value), sun_azimuth: Number($("#environment-sun-azimuth").value), sun_elevation: Number($("#environment-sun-elevation").value), fog_visual_range: Number($("#environment-fog-range").value), precipitation_type: $("#environment-precipitation-type").value || "dry", precipitation_intensity: Number($("#environment-precipitation-intensity").value) });
  if (environment && $("#environment-road-enabled").checked) environment.road_friction = Number($("#environment-road-friction").value);
  return {
    file_header: {
      author: $("#file-author").value || "Author",
      description: $("#file-description").value || "Scenario created with scenario.generator",
      revMajor: 1,
      revMinor: Number($("#xosc-rev-minor").value),
    },
    xosc_map_path: $("#xosc-map-path").value.trim(),
    simulation_time_condition_factor: Math.max(Number($("#simulation-factor").value) || 1, 1),
    postprocessing_scripts: [...document.querySelectorAll("#postprocessing-tools input:checked")].map((input) => input.value),
    postprocessing_parameters: [...document.querySelectorAll("#postprocessing-parameters [data-script-parameter]")].reduce((result, input) => ({ ...result, [input.dataset.script]: { ...result[input.dataset.script], [input.dataset.scriptParameter]: input.value } }), {}),
    ...(environment ? { environment } : {}),
  };
}
