const { expect, test } = require("@playwright/test");
const { openScenario } = require("./helpers");

test("dropdown input updates both canvases without blur or canvas interaction", async ({ page }) => {
  await openScenario(page);
  await page.locator("#advanced-trajectory-settings summary").click();
  await page.evaluate(async () => {
    await updateSettings({ show_speed_profile: true });
    state.scenario = await api("/api/actors/vehicle_1", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ waypoints: [
        { time_s: 0, x_m: 0, y_m: 0, speed_mps: 0 },
        { time_s: 2, x_m: 10, y_m: 0, speed_mps: 10 },
        { time_s: 3, x_m: 20, y_m: 0, speed_mps: 10 },
      ] }),
    });
    render();

    await updatePlayback(0.5);
  });
  const before = await page.evaluate(() => ({
    canvas: $("#map-canvas").toDataURL(),
    profile: $("#speed-profile-canvas").toDataURL(),
  }));
  const select = page.locator("#trajectory-interpolation");
  await select.focus();
  // Exercise input independently: change/blur may be deferred by native menus.
  await select.evaluate((element) => {
    element.value = "catmull_rom";
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#speed-profile-method")).toHaveText("Catmull-Rom");
  await expect(select).toBeFocused();
  await expect(select).toHaveAttribute("aria-busy", "false");
  expect(await page.locator("#map-canvas").evaluate((canvas) => canvas.toDataURL())).not.toBe(before.canvas);
  expect(await page.locator("#speed-profile-canvas").evaluate((canvas) => canvas.toDataURL())).not.toBe(before.profile);
  expect(await page.evaluate(() => currentPlaybackTime())).toBe(0.5);
});

test("rapid dropdown choices keep the latest method and ignore duplicate events", async ({ page }) => {
  await openScenario(page);
  await page.locator("#advanced-trajectory-settings summary").click();
  await page.evaluate(async () => updateSettings({ show_speed_profile: true }));
  const requestedModes = [];
  await page.route("**/api/actors/vehicle_1", async (route) => {
    requestedModes.push(route.request().postDataJSON().interpolation_mode);
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.continue();
  });
  await page.locator("#trajectory-interpolation").evaluate((element) => {
    element.focus();
    for (const mode of ["catmull_rom", "linear_velocity"]) {
      element.value = mode;
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  await expect(page.locator("#trajectory-interpolation")).toHaveValue("linear_velocity");
  await expect(page.locator("#speed-profile-method")).toHaveText("Linear velocity");
  await expect(page.locator("#trajectory-interpolation")).toHaveAttribute("aria-busy", "false");
  expect(requestedModes).toEqual(["catmull_rom", "linear_velocity"]);
  expect(await page.evaluate(() => selectedActor().dimensions.interpolation_mode)).toBe("linear_velocity");
});

test("remembers interpolation per actor and uses the last choice for new actors", async ({ page }) => {
  await openScenario(page);
  await page.locator("#advanced-trajectory-settings summary").click();
  const select = page.locator("#trajectory-interpolation");
  await expect(select).toHaveValue("hermite");
  await select.selectOption("linear_velocity");
  await expect(select).toBeEnabled();
  await page.locator("#actor-list .actor").nth(1).click();
  await expect(select).toHaveValue("hermite");
  await page.locator("#add-actor").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(3);
  await expect(select).toHaveValue("linear_velocity");
  await select.selectOption("catmull_rom");
  await expect(select).toBeEnabled();
  await page.locator("#actor-list .actor").first().click();
  await expect(select).toHaveValue("linear_velocity");
  await page.reload();
  await expect(select).toHaveValue("linear_velocity");
  await page.locator("#add-actor").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(4);
  await expect(select).toHaveValue("catmull_rom");

  const config = await page.evaluate(async () => {
    const response = await fetch(applicationUrl("/api/export/config"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    return response.json();
  });
  expect(config.vehicles.map((actor) => actor.dimensions.interpolation_mode)).toEqual([
    "linear_velocity", "hermite", "catmull_rom", "catmull_rom",
  ]);
  await page.locator("#mode-toggle").click();
  await expect(select).toBeHidden();
});

test("velocity profile measures the same motion as canvas playback in every mode", async ({ page }) => {
  await openScenario(page);
  await page.locator("#advanced-trajectory-settings summary").click();
  await page.evaluate(async () => {
    await updateSettings({ show_speed_profile: true });
    state.scenario = await api(`/api/actors/${state.selected}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ waypoints: [
        { time_s: 0, x_m: 0, y_m: 0, speed_mps: 0 },
        { time_s: 2, x_m: 10, y_m: 0, speed_mps: 10 },
        { time_s: 3, x_m: 20, y_m: 0, speed_mps: 10 },
      ] }),
    });
    render();
    await updatePlayback(0.5);
  });
  await expect(page.locator('#trajectory-interpolation option[value="constant_speed"]')).toHaveCount(0);
  const positions = [];
  const canvasImages = [];
  const profileImages = [];
  for (const mode of ["hermite", "linear_velocity", "catmull_rom"]) {
    await page.locator("#trajectory-interpolation").selectOption(mode);
    await expect.poll(() => page.evaluate(() => selectedActor().dimensions.interpolation_mode)).toBe(mode);
    await expect(page.locator("#speed-profile-method")).toHaveText({
      hermite: "Hermite", linear_velocity: "Linear velocity",
      catmull_rom: "Catmull-Rom",
    }[mode]);
    const measured = await page.evaluate(() => {
      const actor = selectedActor();
      const profile = state.speedProfile;
      const errors = profile.motionSegments.map((segment) => {
        const t0 = segment.start + (segment.end - segment.start) * 0.25;
        const t1 = segment.start + (segment.end - segment.start) * 0.75;
        const a = positionAt(actor, t0);
        const b = positionAt(actor, t1);
        return Math.abs(segment.speed - Math.hypot(b[0] - a[0], b[1] - a[1]) / (t1 - t0));
      });
      return {
        profileMatchesSeries: profile.profilePoints.every((point, i) =>
          point.time === actor.curve.time_s[i] && point.speed === actor.curve.speed_mps[i])
          && profile.profilePoints.length === actor.curve.time_s.length,
        integratedSpeedError: Math.max(...profile.motionSegments.map((segment, i) =>
          Math.abs(segment.speed - (actor.curve.speed_mps[i] + actor.curve.speed_mps[i + 1]) / 2))),
        sampleGap: Math.max(...profile.motionSegments.map((segment) => segment.end - segment.start)),
        knots: actor.waypoints.map((point) => ({ time: point.time_s, speed: point.speed_mps })),
        error: Math.max(...errors), maximum: profile.maxSpeed,
        peak: Math.max(...profile.motionSegments.map((segment) => segment.speed)),
        quarterPosition: positionAt(actor, 0.5)[0],
        playbackTime: currentPlaybackTime(),
        profilePoints: profile.profilePoints,
        canvasImage: $("#map-canvas").toDataURL(),
        profileImage: $("#speed-profile-canvas").toDataURL(),
      };
    });
    expect(measured.error).toBeLessThan(1e-8);
    expect(measured.profileMatchesSeries).toBe(true);
    expect(measured.integratedSpeedError).toBeLessThan(0.002);
    expect(measured.sampleGap).toBeLessThanOrEqual(0.020000000001);
    if (mode !== "linear_velocity") {
      measured.knots.forEach((knot) => {
        const plotted = measured.profilePoints.find((point) => point.time === knot.time);
        expect(plotted.speed).toBeCloseTo(knot.speed, 8);
      });
    }
    expect(measured.maximum).toBeGreaterThan(measured.peak);
    positions.push(measured.quarterPosition);
    expect(measured.playbackTime).toBe(0.5);
    canvasImages.push(measured.canvasImage);
    profileImages.push(measured.profileImage);
    // No doubled time coordinates/vertical stair edges and no blanket spline.
    measured.profilePoints.slice(1).forEach((point, i) => {
      expect(point.time).toBeGreaterThan(measured.profilePoints[i].time);
    });
    if (mode === "linear_velocity") {
      measured.profilePoints.filter((point) => point.time < 2).forEach((point) => {
        expect(point.speed).toBeCloseTo(5 * point.time, 7);
      });
    }

  }
  expect(Math.abs(positions[0] - positions[1])).toBeGreaterThan(0.001);
  expect(Math.abs(positions[2] - positions[1])).toBeGreaterThan(0.01);
  expect(new Set(canvasImages).size).toBeGreaterThanOrEqual(2);
  expect(new Set(profileImages).size).toBeGreaterThanOrEqual(2);
});

test("selecting a trajectory in the canvas updates its interpolation controls and profile", async ({ page }) => {
  await openScenario(page);
  await page.locator("#advanced-trajectory-settings summary").click();
  await page.evaluate(async () => {
    await api("/api/actors/vehicle_2", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interpolation_mode: "catmull_rom" }),
    });
    await updateSettings({ show_speed_profile: true });
  });
  const target = await page.evaluate(() => {
    const point = state.scenario.actors[1].waypoints[0];
    const rect = $("#map-canvas").getBoundingClientRect();
    return {
      x: rect.left + (point.x_m - state.view.minX) * state.view.scale,
      y: rect.top + rect.height - (point.y_m - state.view.minY) * state.view.scale,
    };
  });
  await page.mouse.click(target.x, target.y);
  await expect(page.locator("#actor-name")).toHaveValue("vehicle_2");
  await expect(page.locator("#trajectory-interpolation")).toHaveValue("catmull_rom");
  await expect(page.locator("#speed-profile-method")).toHaveText("Catmull-Rom");
  await page.locator("#trajectory-interpolation").selectOption("linear_velocity");
  await expect(page.locator("#speed-profile-method")).toHaveText("Linear velocity");
  expect(await page.evaluate(() => state.scenario.actors.map((actor) => actor.dimensions.interpolation_mode)))
    .toEqual(["hermite", "linear_velocity"]);
  const label = await page.evaluate(() => {
    const target = state.labelTargets.find((entry) => entry.kind === "actor" && entry.actorName === "vehicle_1");
    const rect = $("#map-canvas").getBoundingClientRect();
    return { x: rect.left + target.x + target.width / 2, y: rect.top + target.y + target.height / 2 };
  });
  await page.mouse.click(label.x, label.y);
  await expect(page.locator("#actor-name")).toHaveValue("vehicle_1");
  await expect(page.locator("#trajectory-interpolation")).toHaveValue("hermite");
  await expect(page.locator("#speed-profile-method")).toHaveText("Hermite");
});

test("trajectory and road tables renumber points after insertion and deletion", async ({ page }) => {
  await openScenario(page);
  await expect(page.locator("#waypoint-body th[scope=row]")).toHaveText(["1", "2", "3"]);
  await page.locator("#waypoint-1-insert").click();
  await expect(page.locator("#waypoint-body th[scope=row]")).toHaveText(["1", "2", "3", "4"]);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#waypoint-1-remove").click();
  await expect(page.locator("#waypoint-body th[scope=row]")).toHaveText(["1", "2", "3"]);
  await page.locator("#mode-toggle").click();
  await page.locator("#add-actor").click();
  await page.locator("#add-road-point").click();
  await page.locator("#add-road-point").click();
  await expect(page.locator("#road-body th[scope=row]")).toHaveText(["1", "2"]);
  await page.locator("#road-point-1-insert").click();
  await expect(page.locator("#road-body th[scope=row]")).toHaveText(["1", "2", "3"]);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#road-point-1-remove").click();
  await expect(page.locator("#road-body th[scope=row]")).toHaveText(["1", "2"]);
});


test("advanced trajectory settings are grouped below the aligned editing buttons", async ({ page }) => {
  await openScenario(page);
  const details = page.locator("#advanced-trajectory-settings");
  await expect(details).toBeVisible();
  await expect(details).not.toHaveAttribute("open", "");
  await expect(page.locator("#time-step")).toBeHidden();
  const layout = await page.evaluate(() => {
    const controls = $("#editing-controls");
    return {
      order: [...controls.children].map((element) => element.id),
      buttons: ["#trajectory-direction", "#waypoint-timing", "#delete-last-point", "#clear-actor"].map(
        (selector) => $(selector).getBoundingClientRect(),
      ),
    };
  });
  expect(layout.order.indexOf("clear-actor") + 1).toBe(layout.order.indexOf("advanced-trajectory-settings"));
  layout.buttons.slice(1).forEach((button) => {
    expect(button.x).toBeCloseTo(layout.buttons[0].x, 1);
    expect(button.width).toBeCloseTo(layout.buttons[0].width, 1);
  });
  await details.locator("summary").click();
  await expect(page.locator("#time-step")).toBeVisible();
  await expect(page.locator("#lane-snap")).toBeVisible();
  await expect(page.locator("#trajectory-interpolation")).toBeVisible();
  await page.locator("#mode-toggle").click();
  await expect(details).toBeHidden();
  await page.locator("#mode-toggle").click();
  await expect(details).toBeVisible();
});

test("velocity profile marker follows playback and hides at trajectory bounds", async ({ page }) => {
  await openScenario(page);
  await page.evaluate(async () => updateSettings({ show_speed_profile: true }));
  const marker = page.locator("#speed-profile-playhead");
  await expect(marker).toBeHidden();
  const markerPosition = () => page.evaluate(() => {
    const actor = selectedActor();
    const canvas = $("#speed-profile-canvas");
    const start = actor.waypoints[0].time_s;
    const end = actor.waypoints.at(-1).time_s;
    const rect = canvas.getBoundingClientRect();
    const pad = state.speedProfile.pad;
    return {
      actual: parseFloat($("#speed-profile-playhead").style.left),
      expected: pad.left + (rect.width - pad.left - pad.right) * (currentPlaybackTime() - start) / (end - start),
    };
  });
  await page.locator("#time-slider").evaluate((slider) => {
    slider.value = "0.5";
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(marker).toBeVisible();
  let position = await markerPosition();
  expect(position.actual).toBeCloseTo(position.expected, 2);
  await page.locator("#play").click();
  await expect.poll(async () => (await markerPosition()).actual).toBeGreaterThan(position.actual);
  await page.locator("#play").click();
  position = await markerPosition();
  await page.waitForTimeout(100);
  expect((await markerPosition()).actual).toBeCloseTo(position.actual, 2);
  await page.locator("#reset-playback").click();
  await expect(marker).toBeHidden();
  await page.evaluate(async () => updatePlayback(state.scenario.actors[0].waypoints.at(-1).time_s));
  await expect(marker).toBeHidden();
  await page.evaluate(async () => updatePlayback(0.75));
  await expect(marker).toBeVisible();
  await page.locator("#mode-toggle").click();
  await expect(marker).toBeHidden();
});
