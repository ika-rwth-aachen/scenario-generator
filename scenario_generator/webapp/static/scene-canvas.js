// Canvas rendering, hit testing, lane snapping, measurement, and pointer interactions.

/** Fit all geometry unless a manual camera is active, and resize for pixel density. */
function viewport() {
  // A manual camera survives redraws; otherwise derive a fit-to-content camera.
  const canvas = $("#map-canvas");
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  const points = [
    ...state.scenario.map.roads.flatMap((road) => road.points),
    ...state.scenario.actors.flatMap((actor) => actor.waypoints.map((point) => [point.x_m, point.y_m])),
  ];
  if (!points.length || rect.width <= 1 || rect.height <= 1) return { canvas, minX: -10, minY: -10, scale: 18 };
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 8);
  const spanY = Math.max(maxY - minY, 8);
  const margin = 48;
  const scale = Math.min(
    120,
    Math.max(0.2, Math.min(Math.max(rect.width - 2 * margin, 120) / spanX, Math.max(rect.height - 2 * margin, 120) / spanY)),
  );
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const fitted = {
    canvas,
    minX: centerX - rect.width / (2 * scale),
    minY: centerY - rect.height / (2 * scale),
    scale,
  };
  return state.camera ? { ...fitted, ...state.camera } : fitted;
}

/** Stroke world-coordinate points using the current camera transform. */
function drawPolyline(context, points, transform) {
  context.beginPath();
  points.forEach((point, index) => {
    const transformed = transform(point);
    if (index === 0) context.moveTo(...transformed);
    else context.lineTo(...transformed);
  });
  context.stroke();
}

/** Draw a screen-space line with a fixed-size arrowhead at its target. */
function drawArrow(context, start, end) {
  const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
  context.beginPath(); context.moveTo(...start); context.lineTo(...end); context.stroke();
  context.beginPath(); context.moveTo(...end);
  context.lineTo(end[0] - 9 * Math.cos(angle - Math.PI / 6), end[1] - 9 * Math.sin(angle - Math.PI / 6));
  context.lineTo(end[0] - 9 * Math.cos(angle + Math.PI / 6), end[1] - 9 * Math.sin(angle + Math.PI / 6));
  context.closePath(); context.fill();
}

/** Assign colors by actor order so every overlay uses the same visual identity. */
function actorColor(actor) {
  return actorColors[state.scenario.actors.findIndex((entry) => entry.name === actor.name) % actorColors.length];
}

/** Derive a handle heading from neighboring editable points rather than samples. */
function waypointHeading(actor, index) {
  const before = actor.waypoints[Math.max(index - 1, 0)];
  const after = actor.waypoints[Math.min(index + 1, actor.waypoints.length - 1)];
  return Math.atan2(after.y_m - before.y_m, after.x_m - before.x_m);
}

/** Draw a readable annotation and optionally register its editable hit area. */
function drawLabel(context, x, y, text, color, target = null) {
  text = String(text).replace("\n", " | ");
  context.font = "11px Helvetica, Arial, sans-serif";
  const width = context.measureText(text).width + 6;
  context.fillStyle = "#fffefaee";
  context.fillRect(x - width / 2, y - 18, width, 15);
  context.fillStyle = color;
  context.fillText(text, x - width / 2 + 3, y - 7);
  if (target) state.labelTargets.push({ ...target, x: x - width / 2, y: y - 18, width, height: 15 });
}

/** Draw a waypoint with stronger emphasis while it is selected or dragged. */
function drawWaypointHandle(context, point, color, active) {
  const radius = active ? 7 : 6;
  context.beginPath();
  context.arc(...point, radius + 2, 0, Math.PI * 2);
  context.fillStyle = "#fffefa";
  context.fill();
  context.beginPath();
  context.arc(...point, radius, 0, Math.PI * 2);
  context.fillStyle = color;
  context.fill();
  context.strokeStyle = "#15211c";
  context.lineWidth = active ? 2 : 1;
  context.stroke();
}

/** Place a compact, high-contrast index next to a waypoint handle. */
function drawPointIndex(context, x, y, number) {
  context.fillStyle = "#15211c";
  context.font = "12px Helvetica, Arial, sans-serif";
  context.fillText(String(number), x + 10, y - 10);
}

/** Project actor dimensions and yaw into independently selectable body and box layers. */
function drawVehicleBody(context, position, dimensions, color, view, drawVehicle, drawBoundingBox, opacity = 1, inactive = false) {
  const length = Math.max(dimensions.length_m * view.scale, 5);
  const width = Math.max(dimensions.width_m * view.scale, 4);
  context.save();
  const [centerX, centerY, yaw] = position;
  const corners = [[length / 2, -width / 2], [length / 2, width / 2], [-length / 2, width / 2], [-length / 2, -width / 2]];
  const polygon = corners.map(([localX, localY]) => [
    centerX + localX * Math.cos(yaw) - localY * Math.sin(yaw),
    centerY - (localX * Math.sin(yaw) + localY * Math.cos(yaw)),
  ]);
  if (drawVehicle) {
    context.save();
    context.globalAlpha *= opacity;
    context.fillStyle = color;
    context.beginPath(); polygon.forEach((point, index) => index ? context.lineTo(...point) : context.moveTo(...point)); context.closePath(); context.fill();
    context.restore();
    context.strokeStyle = color;
    context.lineWidth = inactive ? 1.25 : 1;
    if (inactive) context.setLineDash([5, 4]);
    context.stroke();
    context.setLineDash([]);
  }
  if (drawBoundingBox) {
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.beginPath(); polygon.forEach((point, index) => index ? context.lineTo(...point) : context.moveTo(...point)); context.closePath(); context.stroke();
  }
  // A short nose marker makes the vehicle heading evident at every zoom level.
  if (drawVehicle) {
    context.fillStyle = "#15211c";
    const nose = (distance, lateral) => [centerX + distance * Math.cos(yaw) - lateral * Math.sin(yaw), centerY - (distance * Math.sin(yaw) + lateral * Math.cos(yaw))];
    const tip = nose(length / 2, 0); const left = nose(length / 2 - Math.min(length * 0.25, 12), -Math.min(width * 0.28, 7)); const right = nose(length / 2 - Math.min(length * 0.25, 12), Math.min(width * 0.28, 7));
    context.beginPath();
    context.moveTo(...tip); context.lineTo(...left); context.lineTo(...right);
    context.closePath();
    context.fill();
  }
  context.restore();
  return { length, width };
}

/** Interpolate position and wrapped yaw directly from the sampled playback curve. */
function positionAt(actor, timeS) {
  const curve = actor.curve;
  if (!curve?.time_s?.length) return null;
  if (timeS <= curve.time_s[0]) return [curve.x_m[0], curve.y_m[0], curve.yaw_rad[0]];
  for (let index = 1; index < curve.time_s.length; index += 1) {
    if (timeS <= curve.time_s[index]) {
      const previous = index - 1;
      const span = curve.time_s[index] - curve.time_s[previous];
      const fraction = span ? (timeS - curve.time_s[previous]) / span : 0;
      const xM = curve.x_m[previous] + (curve.x_m[index] - curve.x_m[previous]) * fraction;
      const yM = curve.y_m[previous] + (curve.y_m[index] - curve.y_m[previous]) * fraction;
      let yawDelta = curve.yaw_rad[index] - curve.yaw_rad[previous];
      while (yawDelta > Math.PI) yawDelta -= 2 * Math.PI;
      while (yawDelta < -Math.PI) yawDelta += 2 * Math.PI;
      let yaw = curve.yaw_rad[previous] + yawDelta * fraction;
      while (yaw > Math.PI) yaw -= 2 * Math.PI;
      while (yaw < -Math.PI) yaw += 2 * Math.PI;
      return [xM, yM, yaw];
    }
  }
  const last = curve.time_s.length - 1;
  return [curve.x_m[last], curve.y_m[last], curve.yaw_rad[last]];
}

/** Distinguish active motion from the faded pre-start and post-end actor poses. */
function trajectoryActiveAt(actor, timeS) {
  const curve = actor.curve;
  return Boolean(curve?.time_s?.length) && timeS >= curve.time_s[0] && timeS <= curve.time_s.at(-1);
}

/** Project the complete scenario state; the canvas itself owns no geometry. */
function draw() {
  if (!state.scenario) return;
  state.labelTargets = [];
  const view = viewport();
  const context = view.canvas.getContext("2d");
  context.scale(devicePixelRatio, devicePixelRatio);
  context.clearRect(0, 0, view.canvas.width, view.canvas.height);
  const maxY = view.minY + view.canvas.height / devicePixelRatio / view.scale;
  const transform = (point) => [(point[0] - view.minX) * view.scale, (maxY - point[1]) * view.scale];
  context.lineCap = "round";
  drawGrid(context, view, transform);
  const drawableRoads = [...state.scenario.map.roads].sort((first, second) => Number(["outer", "section"].includes(first.kind)) - Number(["outer", "section"].includes(second.kind)));
  drawableRoads.forEach((road) => {
    if (!state.scenario.settings.show_map) return;
    if (["outer", "section"].includes(road.kind) && !state.scenario.settings.show_map_helpers) return;
    const geometry = road.render_geometry;
    const laneColors = { driving: "#434a4f", biking: "#86b6d1", sidewalk: "#d8d8d8", walking: "#d8d8d8", pedestrian: "#d8d8d8", parking: "#9da4a8", default: "#69746d" };
    if (geometry?.lanes?.length) {
      geometry.lanes.forEach((lane) => {
        const points = lane.points.map(transform);
        if (points.length < 3) return;
        context.fillStyle = laneColors[lane.type] || laneColors.default;
        context.strokeStyle = "#526579";
        context.lineWidth = 1;
        context.beginPath();
        points.forEach((point, index) => index ? context.lineTo(...point) : context.moveTo(...point));
        context.closePath();
        context.fill();
        context.stroke();
      });
    } else {
      const helperStyle = road.kind === "outer"
        ? { color: "#4f6f8f", width: 2, dash: [] }
        : road.kind === "section"
          ? { color: "#b06f2b", width: 1, dash: [5, 4] }
          : { color: "#69746d", width: Math.max(road.width_m * view.scale, 3), dash: [] };
      context.strokeStyle = helperStyle.color;
      context.lineWidth = helperStyle.width;
      context.setLineDash(helperStyle.dash);
      drawPolyline(context, road.display_points || road.points, transform);
      context.setLineDash([]);
    }
  });
  // Keep map annotations above every road fill and helper geometry.
  drawableRoads.forEach((road) => {
    if (!state.scenario.settings.show_map || (["outer", "section"].includes(road.kind) && !state.scenario.settings.show_map_helpers)) return;
    const geometry = road.render_geometry;
    if (geometry?.lanes?.length && state.scenario.settings.show_road_centerlines) {
      context.setLineDash([7, 5]);
      context.strokeStyle = "#13283f"; context.lineWidth = 3.5; drawPolyline(context, geometry.centerline, transform);
      context.strokeStyle = "#ffffff"; context.lineWidth = 1.5; drawPolyline(context, geometry.centerline, transform);
      context.setLineDash([]);
    }
    if (geometry?.labels?.length && state.scenario.settings.show_lane_numbers) geometry.labels.forEach((label) => { const point = transform([label.x_m, label.y_m]); drawLabel(context, point[0], point[1], String(label.id), "#25313a"); });
    if (state.scenario.settings.show_road_points && road.kind === "reference") road.points.forEach((point, index) => { const transformed = transform(point); context.fillStyle = "#eb5e28"; context.beginPath(); context.arc(...transformed, 4, 0, Math.PI * 2); context.fill(); drawLabel(context, transformed[0] + 12, transformed[1], String(index + 1), "#7a4a18"); });
  });
  if (state.scenario.settings.show_road_connections) {
    const roadsByName = new Map(state.scenario.map.roads.map((road) => [road.name, road]));
    state.scenario.map.roads.forEach((road) => {
      const successor = roadsByName.get(road.successor_road); if (!successor || !road.points.length || !successor.points.length) return;
      context.strokeStyle = "#007c91"; context.lineWidth = 2; context.setLineDash([4, 4]);
      drawPolyline(context, [road.points.at(-1), successor.points[0]], transform); context.setLineDash([]);
      const direction = successor.display_points || successor.points;
      if (direction.length >= 2) {
        context.fillStyle = "#007c91";
        drawArrow(context, transform(direction[0]), transform(direction[Math.min(4, direction.length - 1)]));
      }
      const source = transform(road.points.at(-1));
      context.fillStyle = "#e8fff1"; context.strokeStyle = "#007c91"; context.lineWidth = 2;
      context.beginPath(); context.arc(...source, 5, 0, Math.PI * 2); context.fill(); context.stroke();
    });
  }
  state.scenario.actors.forEach((actor) => {
    const color = actorColor(actor);
    // Muting the stroke to 80% keeps every trajectory above 3:1 against the canvas.
    context.save();
    context.globalAlpha = 0.8;
    context.strokeStyle = color;
    context.lineWidth = 3;
    const curvePoints = actor.curve?.x_m?.map((xM, index) => [xM, actor.curve.y_m[index]]) || actor.waypoints.map((point) => [point.x_m, point.y_m]);
    drawPolyline(context, curvePoints, transform);
    context.restore();
    context.fillStyle = color;
    if (state.scenario.settings.show_vehicles || state.scenario.settings.show_bounding_boxes || state.scenario.settings.show_actor_names || state.metrics[actor.name]) {
      const position = positionAt(actor, currentPlaybackTime());
      if (position) {
        const transformed = [...transform(position), position[2]];
        const dimensions = actor.dimensions;
        const active = trajectoryActiveAt(actor, currentPlaybackTime());
        const body = drawVehicleBody(context, transformed, dimensions, color, view, state.scenario.settings.show_vehicles, state.scenario.settings.show_bounding_boxes, active ? 0.62 : 0.32, !active);
        if (state.scenario.settings.show_actor_names) drawLabel(context, transformed[0], transformed[1] + body.width / 2 + 20, actor.name, color, { kind: "actor", actorName: actor.name });
        if (state.metrics[actor.name]) drawLabel(context, transformed[0], transformed[1] - body.width, state.metrics[actor.name], "#15211c");
      }
    }
    if (state.scenario.settings.show_trajectory_waypoints) {
      actor.waypoints.forEach((point, index) => {
        const transformed = transform([point.x_m, point.y_m]);
        drawWaypointHandle(context, transformed, color, actor.name === state.selected);
        if (state.scenario.settings.show_point_indices) drawPointIndex(context, transformed[0], transformed[1], index + 1);
        if (state.scenario.settings.show_waypoint_times) drawLabel(context, transformed[0] + 26, transformed[1] + 12, `t=${point.time_s.toFixed(2)}s`, color, { kind: "time", actorName: actor.name, index });
        if (state.scenario.settings.show_speed_labels && point.speed_mps !== null) drawLabel(context, transformed[0] + 26, transformed[1] + 28, `v=${point.speed_mps.toFixed(2)}m/s`, color, { kind: "speed", actorName: actor.name, index });
      });
    }
    if (state.scenario.settings.show_segment_average_speeds) actor.segment_speeds_mps.forEach((speed, index) => {
      const midpoint = actor.segment_midpoints[index] || [(actor.waypoints[index].x_m + actor.waypoints[index + 1].x_m) / 2, (actor.waypoints[index].y_m + actor.waypoints[index + 1].y_m) / 2];
      const transformed = transform(midpoint); drawLabel(context, transformed[0], transformed[1] - 14, `${speed.toFixed(2)} m/s`, color, { kind: "segment", actorName: actor.name, index });
    });
  });
  drawMeasurementOverlay(context, transform, view);
  drawKeyboardCursor(context, transform);
  drawScaleBar(context, view);
  state.view = view;
}

/** Show a compact position marker only while the canvas is operated by keyboard. */
function drawKeyboardCursor(context, transform) {
  if (document.activeElement !== $("#map-canvas") || !state.keyboardCursor || !state.keyboardCursorVisible) return;
  const [x, y] = transform(state.keyboardCursor);
  context.save();
  // A light halo keeps the marker distinct from roads and trajectories while
  // the navy ring retains sufficient non-text contrast on the canvas.
  context.strokeStyle = "#ffffff";
  context.lineWidth = 5;
  context.beginPath();
  context.arc(x, y, 7, 0, Math.PI * 2);
  context.stroke();
  context.strokeStyle = "#012a7a";
  context.lineWidth = 2.5;
  context.beginPath();
  context.arc(x, y, 7, 0, Math.PI * 2);
  context.stroke();
  context.restore();
}

/** Draw a zoom-adaptive metric grid and emphasize visible world axes. */
function drawGrid(context, view, transform) {
  const width = view.canvas.width / devicePixelRatio;
  const height = view.canvas.height / devicePixelRatio;
  const maxX = view.minX + width / view.scale;
  const maxY = view.minY + height / view.scale;
  const stepM = view.scale >= 10 ? 5 : view.scale >= 2 ? 10 : 50;
  context.strokeStyle = "#dce3dd";
  context.lineWidth = 1;
  for (let xM = Math.floor(view.minX / stepM) * stepM; xM <= maxX; xM += stepM) {
    const x = transform([xM, 0])[0]; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
  }
  for (let yM = Math.floor(view.minY / stepM) * stepM; yM <= maxY; yM += stepM) {
    const y = transform([0, yM])[1]; context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
  }
  if (view.minX <= 0 && maxX >= 0) {
    const x = transform([0, 0])[0]; context.strokeStyle = "#b8c4bb"; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
  }
  if (view.minY <= 0 && maxY >= 0) {
    const y = transform([0, 0])[1]; context.strokeStyle = "#b8c4bb"; context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
  }
}

/** Choose a stable 1/2/5 metric interval near 120 pixels for the scale bar. */
function drawScaleBar(context, view) {
  const targetPx = 120;
  const rawLengthM = targetPx / view.scale;
  const exponent = rawLengthM > 0 ? Math.floor(Math.log10(rawLengthM)) : 0;
  const base = 10 ** exponent;
  const lengthM = [1, 2, 5, 10].map((multiplier) => multiplier * base).find((candidate) => candidate * view.scale >= targetPx) || 10 * base;
  const lengthPx = lengthM * view.scale;
  const height = view.canvas.height / devicePixelRatio;
  const x0 = 24;
  const y0 = height - 28;
  const x1 = x0 + lengthPx;
  context.fillStyle = "#fffefae8";
  context.strokeStyle = "#b8c4bb";
  context.lineWidth = 1;
  context.fillRect(x0 - 8, y0 - 24, lengthPx + 16, 36);
  context.strokeRect(x0 - 8, y0 - 24, lengthPx + 16, 36);
  context.strokeStyle = "#15211c";
  context.lineWidth = 3;
  context.beginPath(); context.moveTo(x0, y0); context.lineTo(x1, y0); context.stroke();
  context.lineWidth = 2;
  context.beginPath(); context.moveTo(x0, y0 - 6); context.lineTo(x0, y0 + 6); context.moveTo(x1, y0 - 6); context.lineTo(x1, y0 + 6); context.stroke();
  context.fillStyle = "#15211c";
  context.font = "11px Helvetica, Arial, sans-serif";
  const label = `${Number.isInteger(lengthM) ? lengthM.toFixed(0) : lengthM} m`;
  const textWidth = context.measureText(label).width;
  context.fillText(label, (x0 + x1 - textWidth) / 2, y0 - 13);
}

/** Render selected measurement points plus distance/path or circumcircle results. */
function drawMeasurementOverlay(context, transform, view) {
  const points = state.measurementPoints;
  if (!points.length) return;
  const color = "#183b56";
  const canvasPoints = points.map(transform);
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.setLineDash([6, 4]);
  context.beginPath();
  canvasPoints.forEach((point, index) => index ? context.lineTo(...point) : context.moveTo(...point));
  context.stroke();
  context.setLineDash([]);
  canvasPoints.forEach((point, index) => {
    context.beginPath(); context.arc(...point, 5, 0, Math.PI * 2);
    context.fillStyle = "#fffefa"; context.fill();
    context.strokeStyle = color; context.lineWidth = 2; context.stroke();
    context.fillStyle = color; context.font = "12px Helvetica, Arial, sans-serif";
    context.fillText(String(index + 1), point[0] + 9, point[1] - 9);
  });

  const mode = state.measurementTool;
  if (mode === "distance" && points.length >= 2) {
    const [start, end] = points;
    const direct = Math.hypot(end[0] - start[0], end[1] - start[1]);
    const [firstSnap, secondSnap] = (state.measurementSnaps || []).slice(0, 2);
    const pathDistance = firstSnap && secondSnap && firstSnap.actorName === secondSnap.actorName ? Math.abs(secondSnap.distanceAlong - firstSnap.distanceAlong) : null;
    const midpoint = transform([(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]);
    const text = pathDistance === null ? `${direct.toFixed(3)} m` : `${direct.toFixed(3)} m / path ${pathDistance.toFixed(3)} m`;
    drawLabel(context, midpoint[0], midpoint[1] - 4, text, color);
    return;
  }
  if (mode !== "radius" || points.length < 3) return;
  const circle = circleFromPoints(...points);
  if (!circle) return;
  const center = transform(circle.center);
  const radiusPx = circle.radius * view.scale;
  if (radiusPx < 20000) {
    context.strokeStyle = "#4a4a4a"; context.lineWidth = 1;
    context.setLineDash([4, 6]); context.beginPath(); context.arc(...center, radiusPx, 0, Math.PI * 2); context.stroke(); context.setLineDash([]);
  }
  context.strokeStyle = color; context.lineWidth = 2;
  context.beginPath(); context.moveTo(center[0] - 6, center[1]); context.lineTo(center[0] + 6, center[1]); context.moveTo(center[0], center[1] - 6); context.lineTo(center[0], center[1] + 6); context.stroke();
  const curvature = circle.radius > 0 ? 1 / circle.radius : 0;
  const label = transform(points[1]);
  drawLabel(context, label[0], label[1] - 8, `R=${circle.radius.toFixed(3)} m, k=${curvature.toFixed(6)} 1/m`, color);
}

/** Resolve an editable annotation under the pointer from hit areas built by draw(). */
function canvasLabelTarget(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left; const y = event.clientY - rect.top;
  return state.labelTargets?.find((target) => x >= target.x && x <= target.x + target.width && y >= target.y && y <= target.y + target.height) || null;
}

/** Prompt for an annotation value and dispatch to the matching atomic backend edit. */
async function editCanvasLabel(target) {
  const actor = state.scenario.actors.find((entry) => entry.name === target.actorName);
  if (!actor) return;
  if (target.kind === "actor") {
    const newName = window.prompt("Actor name", actor.name)?.trim();
    if (!newName || newName === actor.name) return;
    try {
      const result = await api(`/api/actors/${actor.name}/rename`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newName }) });
      state.scenario = result.scenario; state.selected = result.name; render();
    } catch (error) { setStatus(error.message); }
    return;
  }
  const current = target.kind === "segment" ? actor.segment_speeds_mps[target.index] : actor.waypoints[target.index][target.kind === "time" ? "time_s" : "speed_mps"];
  const label = target.kind === "time" ? "Waypoint time [s]" : target.kind === "speed" ? "Point speed [m/s]" : "Segment average speed [m/s]";
  const rawValue = window.prompt(label, Number(current).toFixed(3));
  if (rawValue === null) return;
  const value = Number(rawValue);
  if (!Number.isFinite(value)) { setStatus("Value must be numeric"); return; }
  const path = target.kind === "segment" ? `/api/actors/${actor.name}/segments/${target.index}` : target.kind === "speed" ? `/api/actors/${actor.name}/waypoints/${target.index}/speed` : `/api/actors/${actor.name}/waypoints/${target.index}`;
  const payload = target.kind === "segment" ? { speed_mps: value } : target.kind === "speed" ? { speed_mps: value } : { time_s: value };
  try { state.scenario = await api(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); state.selected = actor.name; render(); } catch (error) { setStatus(error.message); }
}

/** Apply the active canvas tool at an exact world coordinate for pointer and keyboard users. */
async function activateCanvasPoint(x_m, y_m) {
  const actor = selectedActor();
  if (!actor) return;
  const view = state.view;
  const measurementMode = state.measurementMode;
  if (measurementMode !== "off") {
    const requiredPoints = measurementMode === "distance" ? 2 : 3;
    if (state.measurementPoints.length >= requiredPoints) { state.measurementPoints = []; state.measurementSnaps = []; }
    const snap = nearestTrajectoryPoint(x_m, y_m);
    state.measurementPoints.push(snap?.point || [x_m, y_m]);
    state.measurementSnaps = [...(state.measurementSnaps || []), snap ? { actorName: snap.actorName, distanceAlong: snap.distanceAlong } : null];
    updateMeasurementResult();
    if (state.measurementPoints.length === requiredPoints) {
      state.measurementMode = "off";
      setStatus(`${measurementMode === "distance" ? "Distance" : "Radius"} measurement complete`);
    }
    draw(); return;
  }
  if (state.connectionSource) {
    const target = laneAt(x_m, y_m);
    if (!target) { setStatus("Connect mode: select a position inside a lane"); return; }
    try {
      state.scenario = await api("/api/map/connections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_index: state.connectionSource.roadIndex, source_lane_id: state.connectionSource.laneId, target_index: target.roadIndex, target_lane_id: target.laneId }) });
      state.connectionMode = false; state.connectionSource = null; state.selectedRoad = target.roadIndex; render(); setStatus("Road lanes connected");
    } catch (error) { setStatus(error.message); }
    return;
  }
  if (state.connectionMode) {
    const source = laneAt(x_m, y_m);
    if (!source) { setStatus("Connect mode: select a position inside a source lane"); return; }
    state.connectionSource = source; state.selectedRoad = source.roadIndex;
    setStatus(`Connect mode: source lane ${source.laneId}; select a target lane`);
    renderMapEditor(); draw(); return;
  }
  if (state.scenario.settings.map_mode) {
    let road = selectedRoadIndex();
    if (road === undefined) {
      state.scenario = await api("/api/map/roads", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      road = state.scenario.map.roads.length - 1;
      state.selectedRoad = road;
    }
    const selectedRoad = laneAt(x_m, y_m);
    if (selectedRoad) {
      state.selectedRoad = selectedRoad.roadIndex;
      renderMapEditor(); draw(); return;
    }
    const points = state.scenario.map.roads[road]?.points || [];
    const point = snappedRoadPoint(x_m, y_m, road);
    await api(`/api/map/roads/${road}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ points: [...points, point] }) });
    await refresh();
  } else {
    await api(`/api/actors/${actor.name}/waypoints`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ x_m, y_m, snap_distance_m: state.scenario.settings.lane_snap_enabled ? 16 / view.scale : 0 }) });
    await refresh(actor.name);
  }
}

// A canvas click is routed through label selection and then the shared coordinate action.
$("#map-canvas").onclick = async (event) => {
  if (state.didDrag || state.suppressCanvasClick) { state.didDrag = false; state.suppressCanvasClick = false; return; }
  if (!state.scenario.settings.map_mode) {
    const labelTarget = canvasLabelTarget(event);
    if (labelTarget) {
      state.selected = labelTarget.actorName;
      draw();
      setStatus("Actor selected. Double-click the label to edit its value.");
      return;
    }
  }
  const [xM, yM] = canvasWorldPoint(event);
  state.keyboardCursor = [xM, yM];
  await activateCanvasPoint(xM, yM);
};

// Editable annotations use the browser's explicit double-click event instead
// of delaying and swallowing the first click while waiting for a second one.
$("#map-canvas").ondblclick = async (event) => {
  if (state.scenario.settings.map_mode) return;
  const labelTarget = canvasLabelTarget(event);
  if (!labelTarget) return;
  event.preventDefault();
  await editCanvasLabel(labelTarget);
};

/** Convert a pointer event back into the shared world-coordinate system. */
function canvasWorldPoint(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return [
    (event.clientX - rect.left) / state.view.scale + state.view.minX,
    state.view.minY + rect.height / state.view.scale - (event.clientY - rect.top) / state.view.scale,
  ];
}

/** Initialize the keyboard cursor at the visible centre without changing the camera. */
function ensureKeyboardCursor() {
  if (state.keyboardCursor || !state.view) return;
  const rect = $("#map-canvas").getBoundingClientRect();
  state.keyboardCursor = [
    state.view.minX + rect.width / state.view.scale / 2,
    state.view.minY + rect.height / state.view.scale / 2,
  ];
}

/** Pan the camera just enough to keep the keyboard cursor on the canvas. */
function scrollKeyboardCursorIntoView() {
  if (!state.keyboardCursor || !state.view) return;
  const rect = $("#map-canvas").getBoundingClientRect();
  const current = state.camera || { minX: state.view.minX, minY: state.view.minY, scale: state.view.scale };
  const marginM = 20 / current.scale;
  const widthM = rect.width / current.scale;
  const heightM = rect.height / current.scale;
  const [cursorX, cursorY] = state.keyboardCursor;
  const minX = Math.min(cursorX - marginM, Math.max(current.minX, cursorX + marginM - widthM));
  const minY = Math.min(cursorY - marginM, Math.max(current.minY, cursorY + marginM - heightM));
  if (minX === current.minX && minY === current.minY) return;
  state.camera = { ...current, minX, minY };
}

/** Zoom around the visible centre, matching the pointer wheel's bounded scale. */
function keyboardZoom(factor) {
  const rect = $("#map-canvas").getBoundingClientRect();
  const current = state.camera || {
    minX: state.view.minX,
    minY: state.view.minY,
    scale: state.view.scale,
  };
  const centerX = current.minX + rect.width / current.scale / 2;
  const centerY = current.minY + rect.height / current.scale / 2;
  const scale = Math.min(500, Math.max(0.1, current.scale * factor));
  state.camera = {
    scale,
    minX: centerX - rect.width / scale / 2,
    minY: centerY - rect.height / scale / 2,
  };
  draw();
  setStatus(`Canvas zoom ${Math.round(scale * 100) / 100} pixels per metre`);
}

const mapCanvas = $("#map-canvas");
mapCanvas.setAttribute("aria-keyshortcuts", "ArrowUp ArrowDown ArrowLeft ArrowRight Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight Enter Space + - F");
mapCanvas.addEventListener("focus", ensureKeyboardCursor);
mapCanvas.addEventListener("blur", () => { state.keyboardCursorVisible = false; draw(); });
mapCanvas.addEventListener("keydown", async (event) => {
  ensureKeyboardCursor();
  const direction = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, 1],
    ArrowDown: [0, -1],
  }[event.key];
  if (direction) {
    event.preventDefault();
    state.keyboardCursorVisible = true;
    // A fixed metre step becomes unusably coarse or fine as the view scales, so
    // move by a constant on-screen distance instead.
    const pixelStep = event.shiftKey ? 50 : 10;
    const stepM = pixelStep / (state.camera?.scale || state.view.scale);
    if (event.altKey) {
      const current = state.camera || { minX: state.view.minX, minY: state.view.minY, scale: state.view.scale };
      state.camera = { ...current, minX: current.minX + direction[0] * stepM, minY: current.minY + direction[1] * stepM };
    } else {
      state.keyboardCursor[0] += direction[0] * stepM;
      state.keyboardCursor[1] += direction[1] * stepM;
      scrollKeyboardCursorIntoView();
      $("#canvas-summary").textContent = `Canvas cursor at X ${state.keyboardCursor[0].toFixed(1)} metres, Y ${state.keyboardCursor[1].toFixed(1)} metres.`;
    }
    draw();
    return;
  }
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    state.keyboardCursorVisible = true;
    await activateCanvasPoint(...state.keyboardCursor);
    mapCanvas.focus();
    return;
  }
  if (["+", "="].includes(event.key)) { event.preventDefault(); keyboardZoom(1.15); return; }
  if (["-", "_"].includes(event.key)) { event.preventDefault(); keyboardZoom(1 / 1.15); return; }
  if (event.key.toLowerCase() === "f") {
    event.preventDefault();
    state.camera = null;
    state.keyboardCursor = null;
    // Draw once so state.view reflects the fitted extent, then recentre the
    // cursor within it and draw again so its marker stays visible.
    draw();
    ensureKeyboardCursor();
    draw();
    setStatus("Canvas view fitted to the scenario");
  }
});

/** Find the closest actor control point within a zoom-adjusted drag tolerance. */
function nearestWaypoint(xM, yM) {
  const radiusM = 18 / state.view.scale;
  let result = null;
  state.scenario.actors.forEach((actor) => actor.waypoints.forEach((point, index) => {
    const distance = Math.hypot(point.x_m - xM, point.y_m - yM);
    const selectedPenalty = actor.name === state.selected ? 0 : radiusM * 0.35;
    if (distance <= radiusM && (!result || distance + selectedPenalty < result.distance + result.selectedPenalty)) result = { actor, index, distance, selectedPenalty };
  }));
  return result;
}

/** Find the closest rendered map control point within the drag tolerance. */
function nearestRoadPoint(xM, yM) {
  const radiusM = 12 / state.view.scale;
  let result = null;
  state.scenario.map.roads.forEach((road, roadIndex) => road.points.forEach((point, pointIndex) => {
    const distance = Math.hypot(point[0] - xM, point[1] - yM);
    if (distance <= radiusM && (!result || distance < result.distance)) result = { road, roadIndex, pointIndex, distance };
  }));
  return result;
}

/** Test lane selection with the even-odd ray-casting rule. */
function pointInPolygon(point, polygon) {
  let inside = false;
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index, index += 1) {
    const [x, y] = polygon[index]; const [previousX, previousY] = polygon[previous];
    if ((y > point[1]) !== (previousY > point[1]) && point[0] < (previousX - x) * (point[1] - y) / (previousY - y) + x) inside = !inside;
  }
  return inside;
}

/** Resolve the lane under a point, preferring imported geometry on overlaps. */
function laneAt(xM, yM) {
  let fallback = null;
  for (let roadIndex = 0; roadIndex < state.scenario.map.roads.length; roadIndex += 1) {
    const road = state.scenario.map.roads[roadIndex];
    if (road.kind !== "reference") continue;
    const lane = road.render_geometry?.lanes?.find((entry) => pointInPolygon([xM, yM], entry.points));
    if (lane) return { roadIndex, laneId: lane.id };
    const points = road.display_points || road.points;
    let nearest = null;
    for (let index = 1; index < points.length; index += 1) {
      const start = points[index - 1]; const end = points[index]; const dx = end[0] - start[0]; const dy = end[1] - start[1]; const lengthSquared = dx * dx + dy * dy;
      if (!lengthSquared) continue;
      const rawFraction = ((xM - start[0]) * dx + (yM - start[1]) * dy) / lengthSquared;
      if (rawFraction < 0 || rawFraction > 1) continue;
      const fraction = rawFraction;
      const projected = [start[0] + fraction * dx, start[1] + fraction * dy]; const distance = Math.hypot(xM - projected[0], yM - projected[1]);
      if (!nearest || distance < nearest.distance) nearest = { distance, heading: Math.atan2(dy, dx), projected };
    }
    if (!nearest) continue;
    const lateral = (xM - nearest.projected[0]) * -Math.sin(nearest.heading) + (yM - nearest.projected[1]) * Math.cos(nearest.heading);
    const laneIds = lateral >= 0 ? [...Array(Math.floor(road.lane_count / 2)).keys()].map((index) => index + 1) : [...Array(Math.ceil(road.lane_count / 2)).keys()].map((index) => -index - 1);
    let covered = 0;
    for (const laneId of laneIds) {
      covered += Number(road.lane_widths_m[laneId] ?? road.lane_width_m);
      if (Math.abs(lateral) <= covered && (!fallback || nearest.distance < fallback.distance)) fallback = { roadIndex, laneId, distance: nearest.distance };
    }
  }
  return fallback && { roadIndex: fallback.roadIndex, laneId: fallback.laneId };
}

/** Snap new road points to nearby endpoints while avoiding self-snapping. */
function snappedRoadPoint(xM, yM, activeRoadIndex) {
  const radiusM = 12 / state.view.scale;
  let nearest = null;
  state.scenario.map.roads.forEach((road, roadIndex) => {
    if (road.kind !== "reference" || !road.points.length) return;
    [0, road.points.length - 1].forEach((pointIndex) => {
      if (roadIndex === activeRoadIndex && pointIndex === road.points.length - 1) return;
      const point = road.points[pointIndex]; const distance = Math.hypot(point[0] - xM, point[1] - yM);
      if (distance <= radiusM && (!nearest || distance < nearest.distance)) nearest = { point, distance };
    });
  });
  return nearest ? [...nearest.point] : [xM, yM];
}

/** Project a measurement click onto the nearest trajectory and track path distance. */
function nearestTrajectoryPoint(xM, yM) {
  const radiusM = 12 / state.view.scale;
  let best = null;
  state.scenario.actors.forEach((actor) => {
    const curve = actor.curve;
    if (!curve?.x_m || curve.x_m.length < 2) return;
    let distanceAlong = 0;
    for (let index = 1; index < curve.x_m.length; index += 1) {
      const start = [curve.x_m[index - 1], curve.y_m[index - 1]];
      const end = [curve.x_m[index], curve.y_m[index]];
      const dx = end[0] - start[0]; const dy = end[1] - start[1]; const length = Math.hypot(dx, dy);
      if (!length) continue;
      const fraction = Math.max(0, Math.min(1, ((xM - start[0]) * dx + (yM - start[1]) * dy) / (length * length)));
      const point = [start[0] + fraction * dx, start[1] + fraction * dy]; const distance = Math.hypot(point[0] - xM, point[1] - yM);
      if (distance <= radiusM && (!best || distance < best.distance)) best = { point, actorName: actor.name, distanceAlong: distanceAlong + length * fraction, distance };
      distanceAlong += length;
    }
  });
  return best;
}

/** Summarize the current one-shot measurement and its remaining point count. */
function updateMeasurementResult() {
  const mode = state.measurementTool;
  const points = state.measurementPoints;
  const requiredPoints = mode === "distance" ? 2 : 3;
  if (points.length < requiredPoints) {
    const remaining = requiredPoints - points.length;
    $("#measurement-result").textContent = `${mode === "distance" ? "Distance" : "Radius"}: ${remaining} point${remaining === 1 ? "" : "s"} left`;
    return;
  }
  if (mode === "distance") {
    const [start, end] = points;
    const direct = Math.hypot(end[0] - start[0], end[1] - start[1]);
    const [firstSnap, secondSnap] = (state.measurementSnaps || []).slice(0, 2);
    const pathDistance = firstSnap && secondSnap && firstSnap.actorName === secondSnap.actorName ? Math.abs(secondSnap.distanceAlong - firstSnap.distanceAlong) : null;
    $("#measurement-result").textContent = pathDistance === null ? `Distance: ${direct.toFixed(3)} m` : `Distance: ${direct.toFixed(3)} m | Trajectory path: ${pathDistance.toFixed(3)} m`;
    return;
  }
  const circle = circleFromPoints(...points);
  $("#measurement-result").textContent = circle === null ? "Radius: points are collinear" : `Radius: ${circle.radius.toFixed(3)} m | Curvature: ${(1 / circle.radius).toFixed(6)} 1/m`;
}

/** Compute a circumcircle, returning null for collinear or unstable selections. */
function circleFromPoints(first, second, third) {
  const determinant = 2 * (first[0] * (second[1] - third[1]) + second[0] * (third[1] - first[1]) + third[0] * (first[1] - second[1]));
  if (Math.abs(determinant) < 1e-9) return null;
  const firstSq = first[0] ** 2 + first[1] ** 2; const secondSq = second[0] ** 2 + second[1] ** 2; const thirdSq = third[0] ** 2 + third[1] ** 2;
  const xM = (firstSq * (second[1] - third[1]) + secondSq * (third[1] - first[1]) + thirdSq * (first[1] - second[1])) / determinant;
  const yM = (firstSq * (third[0] - second[0]) + secondSq * (first[0] - third[0]) + thirdSq * (second[0] - first[0])) / determinant;
  return { center: [xM, yM], radius: Math.hypot(first[0] - xM, first[1] - yM) };
}

// Pointer gestures keep previews local and persist geometry only when released.
$("#map-canvas").onpointerdown = (event) => {
  if (state.keyboardCursorVisible) {
    state.keyboardCursorVisible = false;
    draw();
  }
  if (event.button === 2) {
    state.panStart = { x: event.clientX, y: event.clientY, camera: state.camera || { minX: state.view.minX, minY: state.view.minY, scale: state.view.scale } };
    event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault(); return;
  }
  if (state.measurementMode !== "off") return;
  const [xM, yM] = canvasWorldPoint(event);
  // A time label may overlap its handle. The handle must still be draggable;
  // labels remain editable when clicked away from the handle itself.
  const trajectoryTarget = state.scenario.settings.map_mode ? null : nearestWaypoint(xM, yM);
  if (!trajectoryTarget && canvasLabelTarget(event)) return;
  if (state.scenario.settings.map_mode) {
    const target = nearestRoadPoint(xM, yM);
    if (!target) return;
    state.dragTarget = { ...target, kind: "road", startClientX: event.clientX, startClientY: event.clientY }; state.didDrag = false;
    event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault(); return;
  }
  const target = trajectoryTarget;
  if (!target) return;
  state.dragTarget = { ...target, kind: "actor", startClientX: event.clientX, startClientY: event.clientY }; state.selected = target.actor.name; state.didDrag = false;
  event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault();
};

$("#map-canvas").onpointermove = (event) => {
  if (state.panStart) {
    const start = state.panStart; state.camera = { minX: start.camera.minX - (event.clientX - start.x) / start.camera.scale, minY: start.camera.minY + (event.clientY - start.y) / start.camera.scale, scale: start.camera.scale }; draw(); return;
  }
  if (!state.dragTarget) return;
  if (!state.didDrag && Math.hypot(event.clientX - state.dragTarget.startClientX, event.clientY - state.dragTarget.startClientY) < 5) return;
  const [xM, yM] = canvasWorldPoint(event);
  if (state.dragTarget.kind === "road") state.dragTarget.road.points[state.dragTarget.pointIndex] = [xM, yM];
  else { const point = state.dragTarget.actor.waypoints[state.dragTarget.index]; point.x_m = xM; point.y_m = yM; }
  state.didDrag = true; draw();
};

$("#map-canvas").onpointerup = async (event) => {
  if (state.panStart) { state.panStart = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); return; }
  const target = state.dragTarget; if (!target) return;
  state.dragTarget = null;
  if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  if (!state.didDrag) { state.suppressCanvasClick = true; return; }
  try {
    if (target.kind === "road") {
      target.road.points[target.pointIndex] = snappedRoadPoint(target.road.points[target.pointIndex][0], target.road.points[target.pointIndex][1], target.roadIndex);
      await api(`/api/map/roads/${target.roadIndex}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ points: target.road.points }) });
      await refresh(); setStatus("Road point moved");
    } else {
      const point = target.actor.waypoints[target.index];
      await api(`/api/actors/${target.actor.name}/waypoints/${target.index}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ x_m: point.x_m, y_m: point.y_m, snap_distance_m: state.scenario.settings.lane_snap_enabled ? 16 / state.view.scale : 0 }) });
      await refresh(target.actor.name); setStatus("Trajectory point moved");
    }
  } catch (error) { setStatus(error.message); await refresh(target.kind === "actor" ? target.actor.name : undefined); }
};

$("#map-canvas").onpointercancel = (event) => {
  state.dragTarget = null;
  state.panStart = null;
  state.didDrag = false;
  if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
};

$("#map-canvas").oncontextmenu = (event) => event.preventDefault();
$("#map-canvas").onwheel = (event) => {
  event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); const current = state.camera || { minX: state.view.minX, minY: state.view.minY, scale: state.view.scale };
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15; const scale = Math.min(500, Math.max(0.1, current.scale * factor));
  const worldX = current.minX + (event.clientX - rect.left) / current.scale; const worldY = current.minY + rect.height / current.scale - (event.clientY - rect.top) / current.scale;
  state.camera = { scale, minX: worldX - (event.clientX - rect.left) / scale, minY: worldY - rect.height / scale + (event.clientY - rect.top) / scale }; draw();
};

// Actor and point buttons reuse the same backend mutations as their canvas actions.
$("#delete-actor").onclick = async () => {
  const actor = selectedActor();
  if (!actor || !confirm(`Delete ${actor.name}?`)) return;
  try {
    await api(`/api/actors/${actor.name}`, { method: "DELETE" });
    await refresh();
  } catch (error) {
    setStatus(error.message);
  }
};

$("#add-point").onclick = () => {
  const actor = selectedActor();
  if (actor) insertWaypoint(actor.waypoints.length);
};
