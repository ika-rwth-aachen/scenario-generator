// Playback, environmental templates, and remaining application event bindings.

/** Use the latest actor waypoint as playback's natural upper time bound. */
function scenarioEndTime() {
  const waypointTimes = state.scenario.actors.flatMap((actor) => (
    actor.waypoints.map((point) => point.time_s)
  ));
  return Math.max(1, ...waypointTimes);
}
/** Return the exact playback time, including endpoints between slider steps. */
function currentPlaybackTime() {
  return Number.isFinite(state.playbackTime)
    ? state.playbackTime
    : Number($("#time-slider").value);
}
/** Update time-dependent drawing and request expensive metrics only when visible. */
async function updatePlayback(requestedTime) {
  const slider = $("#time-slider");
  const value = Math.max(
    Number(slider.min),
    Math.min(
      Number.isFinite(requestedTime) ? requestedTime : Number(slider.value),
      scenarioEndTime(),
    ),
  );
  state.playbackTime = value;
  $("#time-output").value = `${value.toFixed(2)} s`;
  slider.setAttribute("aria-valuetext", `${value.toFixed(2)} seconds`);
  if (state.scenario.settings.show_min_ttc || state.scenario.settings.show_min_thw) {
    try {
      state.metrics = await api(`/api/metrics?time_s=${encodeURIComponent(value)}`);
    } catch (error) {
      setStatus(error.message);
    }
  } else {
    state.metrics = {};
  }
  draw();
  updateCanvasSummary();
}
/** Advance the timeline at animation-frame cadence and wrap at the scenario end. */
function playbackStep() {
  if (!state.playing) return;
  const slider = $("#time-slider");
  const endTime = scenarioEndTime();
  const nextTime = currentPlaybackTime() >= endTime
    ? Number(slider.min)
    : Math.min(currentPlaybackTime() + 0.04, endTime);
  // The range input may round an irregular endpoint visually, while the exact
  // time remains authoritative for drawing and is shown before the next frame
  // wraps playback to the beginning.
  slider.value = nextTime;
  updatePlayback(nextTime);
  requestAnimationFrame(playbackStep);
}
// Playback controls share updatePlayback so manual scrubbing and animation agree.
$("#play").onclick = () => {
  state.playing = !state.playing;
  $("#play").textContent = state.playing ? "Pause" : "Play";
  $("#play").setAttribute("aria-pressed", String(state.playing));
  if (state.playing) requestAnimationFrame(playbackStep);
};
$("#reset-playback").onclick = () => {
  state.playing = false;
  $("#play").textContent = "Play";
  $("#play").setAttribute("aria-pressed", "false");
  $("#time-slider").value = 0;
  updatePlayback(0);
};
$("#time-slider").oninput = updatePlayback;
/** Leave measurement mode and discard both world points and trajectory snap data. */
function clearMeasurement() {
  state.measurementMode = "off";
  state.measurementTool = null;
  state.measurementPoints = [];
  state.measurementSnaps = [];
  $("#measurement-result").textContent = "";
  draw();
}

/** Start a one-shot distance or radius measurement with a clean selection. */
function beginMeasurement(tool) {
  state.measurementMode = tool;
  state.measurementTool = tool;
  state.measurementPoints = [];
  state.measurementSnaps = [];
  $("#measurement-options").open = false;
  updateMeasurementResult();
  draw();
  setStatus(`${tool === "distance" ? "Distance" : "Radius"} measurement: select points on the canvas`);
  $("#map-canvas").focus();
}

// Measurement buttons arm one canvas operation; completion disarms it automatically.
$("#measure-distance").onclick = () => beginMeasurement("distance");
$("#measure-radius").onclick = () => beginMeasurement("radius");
$("#clear-measure").onclick = clearMeasurement;
$("#environment-enabled").onchange = () => { $("#environment-fields").hidden = !$("#environment-enabled").checked; };
/** Normalize nested or direct template data into the environment form and draft. */
function applyEnvironmentalTemplate(raw, sourceName) {
  const environment = raw.environment && typeof raw.environment === "object" ? raw.environment : raw;
  if (!environment || typeof environment !== "object") throw new Error("Environment template must be a JSON object");
  $("#environment-enabled").checked = true;
  $("#environment-fields").hidden = false;
  setValue("#environment-name", environment.name || "environment");
  setValue("#environment-time", environment.time_of_day || "");
  setValue("#environment-cloud", environment.cloud_state || "free");
  setValue("#environment-sun-intensity", environment.sun_intensity ?? 1);
  setValue("#environment-sun-azimuth", environment.sun_azimuth ?? 0);
  setValue("#environment-sun-elevation", environment.sun_elevation ?? 1);
  setValue("#environment-fog-range", environment.fog_visual_range ?? 100000);
  setValue("#environment-precipitation-type", environment.precipitation_type || "dry");
  setValue("#environment-precipitation-intensity", environment.precipitation_intensity ?? 0);
  setValue("#environment-road-friction", environment.road_friction ?? 1);
  $("#environment-time-enabled").checked = "time_of_day" in environment;
  $("#environment-weather-enabled").checked = ["cloud_state", "sun_intensity", "sun_azimuth", "sun_elevation", "fog_visual_range", "precipitation_type", "precipitation_intensity"].some((key) => key in environment);
  $("#environment-road-enabled").checked = "road_friction" in environment;
  state.additionalInformationDraft = additionalInformation();
  setStatus(`Loaded environmental template: ${sourceName}`);
}

/** Populate the selector from backend-listed templates bundled with the application. */
async function loadEnvironmentalTemplateOptions() {
  try {
    const { templates } = await api("/api/environment-templates");
    const select = $("#environment-template-select");
    templates.forEach((template) => select.add(new Option(template.replace(/\.json$/, "").replaceAll("_", " "), template)));
  } catch (error) {
    setStatus(error.message);
  }
}

// Templates can come from a local file or from the bundled backend catalog.
$("#environment-template").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    applyEnvironmentalTemplate(JSON.parse(await file.text()), file.name);
    const select = $("#environment-template-select");
    if (!select.querySelector('option[value="custom"]')) {
      select.add(new Option("Custom", "custom"));
    }
    select.value = "custom";
  } catch (error) {
    setStatus(error.message);
  }
};
$("#environment-template-select").onchange = async () => {
  const template = $("#environment-template-select").value;
  if (!template || template === "custom") return;
  try {
    const environment = await api(
      `/api/environment-templates/${encodeURIComponent(template)}`,
    );
    applyEnvironmentalTemplate(environment, template);
    $("#environment-template").value = "";
  } catch (error) {
    setStatus(error.message);
  }
};
$("#save-environment-template").onclick = async () => {
  const environment = additionalInformation().environment;
  if (!environment) {
    setStatus("Enable environmental conditions before saving a template");
    return;
  }
  const filename = `${String(environment.name || "environment").replace(/[^a-z0-9_-]+/gi, "_")}.json`;
  const content = JSON.stringify(environment, null, 2);
  try {
    if ("showSaveFilePicker" in window) {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{
          description: "JSON template",
          accept: { "application/json": [".json"] },
        }],
      });
      const writable = await handle.createWritable();
      await writable.write(content);
      await writable.close();
    } else {
      const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    }
    setStatus("Environmental template saved");
  } catch (error) {
    if (error.name !== "AbortError") setStatus(error.message);
  }
};
[
  "#file-author", "#file-description", "#xosc-rev-minor", "#xosc-map-path",
  "#simulation-factor", "#environment-enabled",
  "#environment-name", "#environment-time-enabled", "#environment-time",
  "#environment-weather-enabled", "#environment-cloud", "#environment-sun-intensity",
  "#environment-sun-azimuth", "#environment-sun-elevation", "#environment-fog-range",
  "#environment-precipitation-type", "#environment-precipitation-intensity",
  "#environment-road-enabled", "#environment-road-friction",
].forEach((selector) => {
  const input = $(selector);
  input.addEventListener("input", () => { if (state.scenario) state.additionalInformationDraft = additionalInformation(); });
  input.addEventListener("change", () => { if (state.scenario) state.additionalInformationDraft = additionalInformation(); });
});
// Profile dragging edits a node only on pointer release, avoiding request floods.
$("#speed-profile-canvas").onpointerdown = (event) => {
  const profile = state.speedProfile;
  if (!profile) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let nearest = null;
  profile.actor.waypoints.forEach((_, index) => {
    const [nodeX, nodeY] = profile.pointFor(index);
    const distance = Math.hypot(nodeX - x, nodeY - y);
    if (distance < 12 && (!nearest || distance < nearest.distance)) {
      nearest = { index, distance };
    }
  });
  if (!nearest) return;
  state.profileDragIndex = nearest.index;
  event.currentTarget.setPointerCapture(event.pointerId);
};
$("#speed-profile-canvas").onpointerup = async (event) => {
  if (state.profileDragIndex === undefined) return;
  const profile = state.speedProfile;
  const rect = event.currentTarget.getBoundingClientRect();
  const y = event.clientY - rect.top;
  const speed = Math.max(
    0,
    profile.maxSpeed * (1 - (y - profile.pad.top) / profile.height),
  );
  const index = state.profileDragIndex;
  delete state.profileDragIndex;
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
  try {
    await api(`/api/actors/${profile.actor.name}/waypoints/${index}/speed`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_mps: speed }),
    });
    await refresh(profile.actor.name);
  } catch (error) {
    setStatus(error.message);
  }
};
/** Show exactly one table panel while preserving mode-specific button visibility. */
function showTableTab(tab) {
  document.querySelectorAll(".table-content").forEach((panel) => {
    const selected = panel.id === `${tab}-panel`;
    panel.hidden = !selected;
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const selected = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
}
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.onclick = () => showTableTab(button.dataset.tab);
  button.onkeydown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll("[data-tab]:not([hidden])")];
    const currentIndex = tabs.indexOf(button);
    const targetIndex = event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
        : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    showTableTab(tabs[targetIndex].dataset.tab);
    tabs[targetIndex].focus();
  };
});
showTableTab("waypoints");
loadEnvironmentalTemplateOptions();
refresh().then(() => { $("#time-slider").max = scenarioEndTime(); updatePlayback(); }).catch((error) => setStatus(error.message));

let postprocessingScripts = [];
let activePostprocessingScript;
/** Build parameter inputs for the selected script using saved values or defaults. */
function renderPostprocessingParameters() {
  const script = postprocessingScripts.find((item) => item.name === activePostprocessingScript);
  const values = state.scenario?.additional_information?.postprocessing_parameters?.[script?.name] || {};
  const container = $("#postprocessing-parameters");
  container.replaceChildren();
  if (!script) {
    container.textContent = "Select a tool to configure its parameters.";
    return;
  }
  const heading = document.createElement("strong");
  heading.textContent = script.name;
  const formats = document.createElement("p");
  formats.textContent = `Applicable: ${script.formats.join(", ")}`;
  container.append(heading, formats);
  script.parameters.forEach((parameter) => {
    const label = document.createElement("label");
    label.textContent = parameter.label;
    const input = document.createElement("input");
    input.dataset.scriptParameter = parameter.name;
    input.dataset.script = script.name;
    input.type = parameter.type;
    input.value = values[parameter.name] ?? parameter.default;
    label.append(input);
    container.append(label);
  });
}
// Script metadata drives both selection and parameter controls without hardcoding tools.
api("/api/postprocessing-scripts").then(({ scripts }) => {
  postprocessingScripts = scripts;
  const selected = new Set(state.scenario?.additional_information?.postprocessing_scripts || []);
  $("#postprocessing-tools").replaceChildren();
  scripts.forEach((script, index) => {
    const row = document.createElement("div");
    row.className = "postprocessing-tool";
    const input = document.createElement("input");
    input.id = `postprocessing-tool-${index}`;
    input.type = "checkbox";
    input.value = script.name;
    input.checked = selected.has(script.name);
    input.setAttribute("aria-label", `Enable ${script.name} postprocessing`);
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.scriptName = script.name;
    button.textContent = script.name;
    button.setAttribute("aria-label", `Configure ${script.name} postprocessing`);
    row.append(input, button);
    $("#postprocessing-tools").append(row);
  });
  $("#postprocessing-tools").querySelectorAll("input").forEach((input) => input.onchange = () => { state.additionalInformationDraft = additionalInformation(); });
  $("#postprocessing-tools").querySelectorAll("button").forEach((button) => button.onclick = () => { activePostprocessingScript = button.dataset.scriptName; renderPostprocessingParameters(); });
  activePostprocessingScript = scripts[0]?.name;
  renderPostprocessingParameters();
}).catch((error) => setStatus(error.message));
$("#edit-postprocessing").onclick = () => $("#postprocessing-dialog").showModal();
$("#close-postprocessing").onclick = () => $("#postprocessing-dialog").close();

// The visible environmental-template button keeps its native picker keyboard reachable.
$("#environment-template-button").onclick = () => $("#environment-template").click();
