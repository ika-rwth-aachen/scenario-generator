const { expect, test } = require("@playwright/test");
const { ensureVehicleMode, openScenario } = require("./helpers");

const actorName = `e2e-actor-${Date.now()}`;
const storedActorName = actorName.replaceAll("-", "_");

test("edits actors, trajectory rows, and clears trajectories", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  await page.locator("#actor-list .actor").first().click();
  await page.locator("#actor-name").fill(actorName);
  await page.locator("#actor-name").press("Tab");
  await expect(page.locator("#actor-list")).toContainText(storedActorName);

  await page.locator("#length").fill("5.2");
  await page.locator("#length").press("Tab");
  await expect(page.locator("#length")).toHaveValue("5.2");

  const waypointRows = page.locator("#waypoint-body tr");
  await expect(waypointRows).toHaveCount(3);
  await waypointRows.nth(1).locator("input").first().fill("1.25");
  await waypointRows.nth(1).locator("input").first().press("Tab");
  await expect(waypointRows.nth(1).locator("input").first()).toHaveValue("1.25");

  await page.locator("#add-point").click();
  await expect(waypointRows).toHaveCount(4);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#clear-actor").click();
  await expect(waypointRows).toHaveCount(0);
});

test("controls actor type, action, controller, playback, and help", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);

  await page.locator("#actor-type").selectOption("pedestrian");
  await expect(page.locator("#actor-type")).toHaveValue("pedestrian");
  await page.locator("#actor-action").selectOption("route");
  await expect(page.locator("#actor-action")).toHaveValue("route");

  await page.locator("#actor-form summary", { hasText: "Controller" }).click();
  await page.locator("#controller-name").fill("test-controller");
  await page.locator("#controller-name").press("Tab");
  await expect(page.locator("#controller-name")).toHaveValue("test-controller");
  await page.locator("#actor-form summary", { hasText: "Controller" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#clear-controller").click();
  await expect(page.locator("#controller-name")).toHaveValue("");

  await page.locator("#play").click();
  await expect(page.locator("#play")).toHaveText("Pause");
  await page.locator("#reset-playback").click();
  await expect(page.locator("#play")).toHaveText("Play");

  const helpOptions = page.locator(".help-options");
  await page.getByText("Help", { exact: true }).click();
  await expect(helpOptions).toHaveAttribute("open", "");
  await page.locator("#show-tooltips").check();
  await page.locator("#primary-title").click();
  await expect(helpOptions).not.toHaveAttribute("open", "");
  await page.locator("#fit-view").focus();
  await expect(page.locator("#accessible-tooltip")).toContainText(
    /Reset the viewport.*roads and trajectories fit on the canvas/,
  );
  await page.keyboard.press("Escape");
  await expect(page.locator("#accessible-tooltip")).toBeHidden();
  await page.locator("#fit-view").hover();
  await expect(page.locator("#accessible-tooltip")).toBeVisible();
  const tooltipBounds = await page.locator("#accessible-tooltip").boundingBox();
  expect(tooltipBounds).not.toBeNull();
  await page.mouse.move(tooltipBounds.x + 8, tooltipBounds.y + 8);
  await page.waitForTimeout(350);
  await expect(page.locator("#accessible-tooltip")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByText("Help", { exact: true }).click();
  await page.locator("#help").click();
  await expect(page.locator("#help-dialog")).toBeVisible();
  await expect(helpOptions).not.toHaveAttribute("open", "");
  await page.mouse.click(1, 1);
  await expect(page.locator("#help-dialog")).toBeHidden();
});

test("restarts playback when the scenario end is not aligned to the slider step", async ({ page }) => {
  await openScenario(page);
  const playback = await page.evaluate(() => {
    const slider = document.querySelector("#time-slider");
    const actor = state.scenario.actors.find((entry) => entry.name === state.selected);
    actor.waypoints.at(-1).time_s = 13.333333333333332;
    slider.max = scenarioEndTime();
    slider.value = "13.33";
    state.playbackTime = 13.33;
    state.playing = true;
    playbackStep();
    const endTime = state.playbackTime;
    const endOutput = document.querySelector("#time-output").value;
    playbackStep();
    state.playing = false;
    return { endTime, endOutput, restartedAt: state.playbackTime };
  });
  expect(playback.endTime).toBe(13.333333333333332);
  expect(playback.endOutput).toBe("13.33 s");
  expect(playback.restartedAt).toBe(0);
});

test("edits and persists parameter declarations", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  await page.locator("#actor-form summary", { hasText: "Parameter declarations" }).click();
  await page.locator("#edit-parameters").click();
  const dialog = page.locator("#parameter-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("tbody tr")).toHaveCount(1);
  await dialog.locator("tbody tr input").nth(0).fill("trafficDensity");
  await dialog.locator("tbody tr select").selectOption("double");
  await dialog.locator("tbody tr input").nth(1).fill("0.4");
  await dialog.getByRole("button", { name: "Save declarations" }).click();
  await expect(dialog).toBeHidden();
  await page.locator("#actor-form summary", { hasText: "Parameter declarations" }).click();
  await page.locator("#edit-parameters").click();
  await expect(dialog.locator("tbody tr")).toHaveCount(1);
  await expect(dialog.locator("tbody tr input").nth(0)).toHaveValue("trafficDensity");
  await dialog.getByRole("button", { name: "Close" }).click();
});

test("renders imported parameter declarations without HTML injection", async ({ page }) => {
  await openScenario(page);
  await page.evaluate(() => {
    window.__parameterXss = false;
    renderParameterRows('<ParameterDeclaration name="&quot; autofocus onfocus=&quot;window.__parameterXss=true" parameterType="string" value="safe"/>');
  });

  const row = page.locator("#parameter-body tr");
  await expect(row.locator("input").first()).toHaveValue('" autofocus onfocus="window.__parameterXss=true');
  await expect(row.locator("[onfocus]")).toHaveCount(0);
  expect(await page.evaluate(() => window.__parameterXss)).toBe(false);
});

test("shows progress only while an OpenDRIVE map is loading", async ({ page }) => {
  await openScenario(page);
  let releaseUpload;
  await page.route("**/api/map", async (route) => {
    await new Promise((resolve) => { releaseUpload = resolve; });
    await route.abort();
  });

  await page.locator("#map-upload").setInputFiles({
    name: "map.xodr",
    mimeType: "application/xml",
    buffer: Buffer.from("<OpenDRIVE />"),
  });
  await expect(page.locator("#status")).toHaveText("Loading map...");
  await expect(page.locator("#status")).toHaveClass(/loading/);
  await expect.poll(() => typeof releaseUpload).toBe("function");
  releaseUpload();
  await expect(page.locator("#status")).not.toHaveClass(/loading/);
});

test("isolates scenario changes between browser sessions", async ({ browser }) => {
  const firstSession = await browser.newContext();
  const secondSession = await browser.newContext();
  const firstPage = await firstSession.newPage();
  const secondPage = await secondSession.newPage();

  try {
    await openScenario(firstPage);
    await ensureVehicleMode(firstPage);
    await firstPage.locator("#add-actor").click();
    await expect(firstPage.locator("#actor-list .actor")).toHaveCount(3);

    await openScenario(secondPage);
    await expect(secondPage.locator("#actor-list .actor")).toHaveCount(2);
  } finally {
    await firstSession.close();
    await secondSession.close();
  }
});
