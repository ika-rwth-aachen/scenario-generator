const { expect, test } = require("@playwright/test");
const path = require("path");
const { ensureVehicleMode, openScenario } = require("./helpers");

test("switches view options, environmental information, and map mode", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  await page.getByText("View", { exact: true }).click();
  const viewOptions = page.locator("#view-options");
  await viewOptions.getByText("Velocity profile", { exact: true }).check();
  await expect(page.locator("#speed-profile")).toBeVisible();

  await page.getByText("Additional scenario information", { exact: true }).click();
  await page.locator("#environment-enabled").check();
  await expect(page.locator("#environment-fields")).toBeVisible();
  await page.locator("#environment-name").fill("clear_day");

  await page.locator("#mode-toggle").click();
  await expect(page.locator("#primary-title")).toHaveText("Roads");
  await expect(page.locator("#speed-profile")).toBeHidden();
  const roads = page.locator("#road-list .actor");
  await expect(roads).toHaveCount(0);
  await page.locator("#add-actor").click();
  await expect(roads).toHaveCount(1);
  await page.locator("#add-actor").click();
  await expect(roads).toHaveCount(2);
  await page.locator("#mode-toggle").click();
  await expect(page.locator("#primary-title")).toHaveText("Actors");
  await expect(page.locator("#inspector-title")).toHaveText("Actor inspector");
  await page.locator("#mode-toggle").click();
  await expect(page.locator("#primary-title")).toHaveText("Roads");
});

test("expands the canvas below the header with its toolbar and playback", async ({ page }) => {
  await openScenario(page);

  const fullscreenButton = page.locator("#canvas-fullscreen");
  await expect(fullscreenButton).toHaveAttribute("aria-label", "Enter fullscreen");
  await fullscreenButton.click();

  await expect(page.locator("body")).toHaveClass(/canvas-expanded/);
  await expect(fullscreenButton).toHaveAttribute("aria-label", "Exit fullscreen");
  await expect(page.locator('[data-fullscreen-icon="enter"]')).toBeHidden();
  await expect(page.locator('[data-fullscreen-icon="exit"]')).toBeVisible();
  await expect(page.locator("header")).toBeVisible();
  await expect(page.locator("#mode-toggle")).toBeVisible();
  await expect(page.locator("#fit-view")).toBeVisible();
  await expect(page.getByText("View", { exact: true })).toBeVisible();
  await expect(page.getByText("Measure", { exact: true })).toBeVisible();
  await expect(page.locator("#map-canvas")).toBeVisible();
  await expect(page.locator("#play")).toBeVisible();
  await expect(page.locator("#reset-playback")).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const header = document.querySelector("header").getBoundingClientRect();
    const presentation = document.querySelector("#canvas-presentation").getBoundingClientRect();
    const canvas = document.querySelector("#map-canvas").getBoundingClientRect();
    const toolbar = document.querySelector(".canvas-toolbar").getBoundingClientRect();
    return {
      startsBelowHeader: Math.abs(presentation.top - header.bottom) < 1,
      toolbarOverlaysCanvas: toolbar.top >= canvas.top && toolbar.bottom <= canvas.bottom,
    };
  })).toEqual({ startsBelowHeader: true, toolbarOverlaysCanvas: true });
  await fullscreenButton.click();

  await expect(page.locator("body")).not.toHaveClass(/canvas-expanded/);
  await expect(fullscreenButton).toHaveAttribute("aria-label", "Enter fullscreen");
});

test("keeps the View menu inside a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 800 });
  await openScenario(page);

  await page.getByText("View", { exact: true }).click();
  const menuBounds = await page.locator("#view-options").boundingBox();

  expect(menuBounds).not.toBeNull();
  expect(menuBounds.x).toBeGreaterThanOrEqual(0);
  expect(menuBounds.x + menuBounds.width).toBeLessThanOrEqual(700);
});

test("loads bundled scenarios and offers local scenario and map uploads", async ({ page }) => {
  await openScenario(page);

  await page.locator("#scenario-load-button").click();
  await expect(page.locator("#scenario-load-dialog")).toBeVisible();
  await page.mouse.click(1, 1);
  await expect(page.locator("#scenario-load-dialog")).toBeHidden();
  await page.locator("#scenario-load-button").click();
  await expect(page.locator("#scenario-upload-button")).toHaveText("Upload scenario");
  const scenarioSelect = page.locator("#default-scenario-select");
  await expect(scenarioSelect.locator("option")).toHaveText([
    "Cut-in from left (.xosc)",
    "Cut-in from left on curved road (.json)",
    "Pass straight intersecting vehicle from right passing straight (.json)",
    "VRU crossing from left (.json)",
  ]);
  await scenarioSelect.selectOption("cut_in_from_left_on_curved_road.json");
  await page.locator("#load-default-scenario").click();
  await expect(page.locator("#scenario-load-dialog")).not.toBeVisible();
  await expect(page.locator("#actor-list")).toContainText("ego_vehicle");
  await expect(page.locator("#actor-list")).toContainText("cut_in_vehicle");
  await expect.poll(() => page.evaluate(() => ({
    roadCount: state.scenario.map.roads.length,
    mapPath: state.scenario.map.path,
  }))).toEqual({
    roadCount: 1,
    mapPath: expect.stringContaining("synthetic_curve_cut_in.xodr"),
  });

  await page.locator("#scenario-load-button").click();
  await scenarioSelect.selectOption("cut_in_from_left.xosc");
  await page.locator("#load-default-scenario").click();
  await expect(page.locator("#scenario-load-dialog")).not.toBeVisible();
  await expect(page.locator("#actor-list")).toContainText("ego_vehicle");
  await expect(page.locator("#actor-list")).toContainText("cut_in_vehicle");

  await page.locator("#scenario-load-button").click();
  await scenarioSelect.selectOption("VRU_crossing_from_left.json");
  await page.locator("#load-default-scenario").click();
  await expect(page.locator("#scenario-load-dialog")).not.toBeVisible();
  await expect(page.locator("#actor-list")).toContainText("approaching_vehicle");
  await expect(page.locator("#actor-list")).toContainText("pedestrian");
  await expect.poll(() => page.evaluate(() => ({
    roadCount: state.scenario.map.roads.length,
    mapPath: state.scenario.map.path,
  }))).toEqual({
    roadCount: 16,
    mapPath: expect.stringContaining("RITA-junction.xodr"),
  });

  await page.locator("#map-load-button").click();
  await expect(page.locator("#map-load-dialog")).toBeVisible();
  await page.mouse.click(1, 1);
  await expect(page.locator("#map-load-dialog")).toBeHidden();
  await page.locator("#map-load-button").click();
  await expect(page.locator("#map-upload-button")).toHaveText("Upload map");
  await expect(page.locator("#default-map-select option")).toHaveText([
    "Highway (.xodr)",
    "RITA junction (.xodr)",
    "Roundabout (.xodr)",
  ]);
  await expect(page.locator("#load-default-map")).toBeEnabled();
  await page.locator("#load-default-map").click();
  await expect(page.locator("#map-load-dialog")).not.toBeVisible();

  await page.locator("#map-load-button").click();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.locator("#map-upload-button").click();
  await chooserPromise;
});

test("shows, adds, and removes detection gaps", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  await page.getByText("View", { exact: true }).click();
  await page.locator("#view-options").getByText("Perception gaps", { exact: true }).check();
  await page.locator('button[data-tab="gaps"]').click();
  await expect(page.locator("#gaps-panel")).toBeVisible();

  await page.locator("#add-gap").click();
  const gaps = page.locator("#gap-body tr");
  await expect(gaps).toHaveCount(1);
  await gaps.locator("select").selectOption({ index: 1 });
  page.once("dialog", (dialog) => dialog.accept());
  await gaps.locator("button").click();
  await expect(gaps).toHaveCount(0);
});

test("can postpone modification of an imported map", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  await page.locator("#map-upload").setInputFiles(
    path.resolve(
      __dirname,
      "../../scenario_generator/webapp/documentation/examples/tutorial-straight-road.xodr",
    ),
  );

  const dialogMessages = [];
  page.on("dialog", async (dialog) => {
    dialogMessages.push(dialog.message());
    await dialog.accept();
  });

  await page.locator("#mode-toggle").click();
  await expect(page.locator("#primary-title")).toHaveText("Roads");
  await page.locator("#mode-toggle").click();
  await expect(page.locator("#primary-title")).toHaveText("Actors");
  expect(dialogMessages).toEqual([]);

  await page.locator("#mode-toggle").click();
  await page.locator("#road-name").fill("later_modified_road");
  await page.locator("#road-name").press("Tab");

  await expect.poll(() => dialogMessages).toEqual([
    "Modify the imported map? An editable copy will be created; the original uploaded file remains unchanged.",
  ]);
  await expect(page.locator("#road-name")).toHaveValue("later_modified_road");
});
