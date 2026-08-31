// Scenario import, individual exports, and batch export quality confirmation.

/** Turn a response body into a temporary browser download without retaining it. */
async function downloadResponse(response, fileName) {
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Apply one imported snapshot consistently for uploaded and bundled resources. */
function applyLoadedSnapshot(snapshot, kind) {
  state.scenario = snapshot;
  if (kind === "scenario") state.additionalInformationDraft = null;
  state.selected = state.scenario.actors[0]?.name;
  state.playbackTime = 0;
  render();
  setStatus(state.scenario.map_load_hint
    ? `Map not found: ${state.scenario.map_load_hint}. Load the XODR file manually.`
    : "Ready");
}

/** Import a scenario or map and replace the browser state with the server result. */
async function upload(input, path) {
  const file = input.files[0];
  if (!file) return;
  const isMapUpload = path === "/api/map";
  $(isMapUpload ? "#map-upload-button" : "#scenario-upload-button").removeAttribute("aria-describedby");
  const data = new FormData();
  data.append("file", file);
  setStatus(isMapUpload ? "Loading map..." : "Loading scenario...", true);
  try {
    applyLoadedSnapshot(
      await api(path, { method: "POST", body: data }),
      isMapUpload ? "map" : "scenario",
    );
  } catch (error) {
    setStatus(error.message);
    const trigger = $(isMapUpload ? "#map-upload-button" : "#scenario-upload-button");
    trigger.setAttribute("aria-describedby", "status");
    trigger.focus();
  } finally {
    input.value = "";
  }
}

$("#map-upload").onchange = (event) => upload(event.target, "/api/map");
$("#scenario-upload").onchange = (event) => upload(event.target, "/api/import");

/** Populate one bundled-default selector and explain deployments without entries. */
async function populateDefaultSelector(kind) {
  const select = $(`#default-${kind}-select`);
  const button = $(`#load-default-${kind}`);
  const hint = $(`#default-${kind}-hint`);
  try {
    const { defaults } = await api(`/api/default-${kind}s`);
    select.replaceChildren();
    defaults.forEach((entry) => select.add(new Option(entry.label, entry.name)));
    const available = defaults.length > 0;
    select.disabled = !available;
    button.disabled = !available;
    hint.textContent = available
      ? `${defaults.length} bundled ${kind}${defaults.length === 1 ? "" : "s"} available.`
      : `No default ${kind}s are bundled yet. Use Upload ${kind}.`;
    if (!available) select.add(new Option(`No default ${kind}s available`, ""));
  } catch (error) {
    select.replaceChildren(new Option(`Default ${kind}s unavailable`, ""));
    select.disabled = true;
    button.disabled = true;
    hint.textContent = error.message;
  }
}

/** Load the selected bundled resource without routing it through a file picker. */
async function loadSelectedDefault(kind) {
  const select = $(`#default-${kind}-select`);
  const button = $(`#load-default-${kind}`);
  if (!select.value) return;
  button.removeAttribute("aria-describedby");
  setStatus(`Loading default ${kind}...`, true);
  try {
    const snapshot = await api(
      `/api/default-${kind}s/${encodeURIComponent(select.value)}`,
      { method: "POST" },
    );
    applyLoadedSnapshot(snapshot, kind);
    $(`#${kind}-load-dialog`).close();
  } catch (error) {
    setStatus(error.message);
    button.setAttribute("aria-describedby", "status");
    button.focus();
  }
}

$("#load-default-scenario").onclick = () => loadSelectedDefault("scenario");
$("#load-default-map").onclick = () => loadSelectedDefault("map");
$("#scenario-load-button").onclick = () => $("#scenario-load-dialog").showModal();
$("#map-load-button").onclick = () => $("#map-load-dialog").showModal();
$("#close-scenario-load").onclick = () => $("#scenario-load-dialog").close();
$("#close-map-load").onclick = () => $("#map-load-dialog").close();
["scenario", "map"].forEach((kind) => {
  const dialog = $(`#${kind}-load-dialog`);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});
$("#scenario-upload-button").onclick = () => {
  $("#scenario-load-dialog").close();
  $("#scenario-upload").click();
};
$("#map-upload-button").onclick = () => {
  $("#map-load-dialog").close();
  $("#map-upload").click();
};
populateDefaultSelector("scenario");
populateDefaultSelector("map");

/** Require explicit confirmation when an XOSC quality report contains findings. */
async function confirmXoscExport() {
  try {
    const report = await api("/api/quality-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_name: $("#output-name").value, additional_information: additionalInformation() }),
    });
    const problems = state.scenario.settings.show_sqc_errors ? report.problems : [];
    const warnings = state.scenario.settings.show_sqc_warnings ? report.warnings : [];
    if (!problems.length && !warnings.length) return true;
    const findings = [
      ...problems.map((entry) => `Problem: ${entry}`),
      ...warnings.map((entry) => `Warning: ${entry}`),
    ].join("\n");
    $("#export-quality-result").textContent = findings;
    sizeQualityDialog({ problems, warnings }, "#export-quality-dialog");
    $("#export-quality-dialog").showModal();
    return await new Promise((resolve) => { exportQualityConfirmation = resolve; });
  } catch (error) {
    setStatus(`Scenario Quality Checker failed: ${error.message}`);
    return false;
  }
}

document.querySelectorAll("[data-export]").forEach((button) => {
  // Single exports download the response directly instead of storing browser state.
  button.onclick = async () => {
    const format = button.dataset.export;
    setStatus(`Creating ${format.toUpperCase()}...`);
    try {
      if (format === "xosc" && !(await confirmXoscExport())) { setStatus("Export cancelled"); return; }
      const response = await api(`/api/export/${format}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_name: $("#output-name").value, additional_information: additionalInformation() }),
      }, false);
      const baseName = $("#output-name").value.trim() || "scenario";
      const fileName = format === "config" ? `${baseName}_config.json` : `${baseName}.${format}`;
      await downloadResponse(response, fileName);
      setStatus("Download ready");
    } catch (error) {
      setStatus(error.message);
    }
  };
});

$("#generate-files").onclick = async () => {
  // The backend creates one ZIP so the selected outputs share identical metadata.
  const formats = ["mcap", "xosc", "xodr", "json"].filter((format) => $("#batch-export-" + format).checked);
  if (!formats.length) {
    markFieldInvalid($("#batch-export-mcap"), "Select at least one export format before generating files.");
    return;
  }
  setStatus("Generating selected files...");
  try {
    if (formats.includes("xosc") && !(await confirmXoscExport())) { setStatus("Export cancelled"); return; }
    const response = await api("/api/export-bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_name: $("#output-name").value, formats, additional_information: additionalInformation() }),
    });
    await downloadResponse(
      response,
      `${$("#output-name").value || "scenario"}_exports.zip`,
    );
    setStatus("Export bundle ready");
  } catch (error) {
    setStatus(error.message);
  }
};

$("#fit-view").onclick = () => { state.camera = null; draw(); };
window.onresize = draw;
