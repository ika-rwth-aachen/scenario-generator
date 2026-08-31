const AxeBuilder = require("@axe-core/playwright").default;
const { expect, test } = require("@playwright/test");
const { openScenario } = require("./helpers");

const wcagTags = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

async function expectNoAxeViolations(page) {
  const result = await new AxeBuilder({ page }).withTags(wcagTags).analyze();
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}

test("main application states have no automated WCAG A or AA violations", async ({ page }) => {
  await openScenario(page);
  await expectNoAxeViolations(page);

  await page.getByText("View", { exact: true }).click();
  await expectNoAxeViolations(page);
  await page.getByText("View", { exact: true }).click();

  for (const [trigger, closeButton] of [
    ["#scenario-load-button", "#close-scenario-load"],
    ["#map-load-button", "#close-map-load"],
  ]) {
    await page.locator(trigger).click();
    await expectNoAxeViolations(page);
    await page.locator(closeButton).click();
  }

  for (const [trigger, dialog] of [
    ["#about", "#about-dialog"],
    ["#help", "#help-dialog"],
    ["#data-privacy", "#data-privacy-dialog"],
    ["#edit-postprocessing", "#postprocessing-dialog"],
  ]) {
    if (trigger === "#help") await page.getByText("Help", { exact: true }).click();
    await page.locator(trigger).click();
    await expect(page.locator(dialog)).toBeVisible();
    await expectNoAxeViolations(page);
    await page.locator(dialog).press("Escape");
  }
});

test("essential controls reflow and remain available at 320 CSS pixels", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 });
  await openScenario(page);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(1024);

  await page.setViewportSize({ width: 320, height: 800 });

  await expect(page.locator(".actors")).toBeVisible();
  await expect(page.locator(".inspector")).toBeVisible();
  await expect.poll(() => page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }))).toEqual({ viewport: 320, document: 320 });
  await expectNoAxeViolations(page);

  for (const [trigger, dialog] of [
    ["#about", "#about-dialog"],
    ["#data-privacy", "#data-privacy-dialog"],
    ["#edit-postprocessing", "#postprocessing-dialog"],
  ]) {
    await page.locator(trigger).click();
    await expect(page.locator(dialog)).toBeVisible();
    const bounds = await page.locator(dialog).boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(320);
    await expectNoAxeViolations(page);
    await page.keyboard.press("Escape");
    await expect(page.locator(dialog)).toBeHidden();
  }

  await page.addStyleTag({ content: `
    * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; }
    p { margin-bottom: 2em !important; }
  ` });
  await expect.poll(() => page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }))).toEqual({ viewport: 320, document: 320 });
});

test("canvas editing and table tabs support keyboard operation", async ({ page }) => {
  await openScenario(page);
  for (const [menuTrigger, customTrigger] of [
    ["#scenario-load-button", "#scenario-upload-button"],
    ["#map-load-button", "#map-upload-button"],
  ]) {
    await page.locator(menuTrigger).click();
    const chooserPromise = page.waitForEvent("filechooser");
    await page.locator(customTrigger).focus();
    await page.locator(customTrigger).press("Enter");
    await chooserPromise;
  }
  const rows = page.locator("#waypoint-body tr");
  const initialCount = await rows.count();

  await page.locator("#map-canvas").focus();
  expect(await page.evaluate(() => state.keyboardCursorVisible)).toBe(false);
  await page.keyboard.press("ArrowRight");
  expect(await page.evaluate(() => state.keyboardCursorVisible)).toBe(true);
  await page.keyboard.press("Enter");
  await expect(rows).toHaveCount(initialCount + 1);

  await page.getByText("Measure", { exact: true }).click();
  await page.locator("#measure-distance").click();
  await expect(page.locator("#map-canvas")).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await expect(page.locator("#measurement-result")).toContainText("Distance:");

  await page.locator("#map-canvas").press("Tab");
  await expect(page.locator("#canvas-fullscreen")).toBeFocused();

  const activeTab = page.locator('[role="tab"][aria-selected="true"]');
  await expect(activeTab).toHaveCount(1);
  await activeTab.press("ArrowRight");
  await expect(page.locator('[role="tab"][aria-selected="true"]')).toBeFocused();
});

test("canvas pointer jitter remains a click while deliberate movement drags", async ({ page }) => {
  await openScenario(page);
  const canvas = page.locator("#map-canvas");
  await canvas.focus();
  await page.keyboard.press("ArrowRight");
  expect(await page.evaluate(() => state.keyboardCursorVisible)).toBe(true);

  const target = await page.evaluate(() => {
    const canvasRect = document.querySelector("#map-canvas").getBoundingClientRect();
    const actor = state.scenario.actors.find((entry) => entry.name === state.selected);
    const point = actor.waypoints[0];
    return {
      actorName: actor.name,
      initialX: point.x_m,
      initialY: point.y_m,
      clientX: canvasRect.left + (point.x_m - state.view.minX) * state.view.scale,
      clientY: canvasRect.top + canvasRect.height - (point.y_m - state.view.minY) * state.view.scale,
    };
  });

  await page.mouse.move(target.clientX, target.clientY);
  await page.mouse.down();
  await page.mouse.move(target.clientX + 2, target.clientY + 2);
  await page.mouse.up();
  expect(await page.evaluate(() => state.keyboardCursorVisible)).toBe(false);
  expect(await page.evaluate(({ actorName }) => {
    const point = state.scenario.actors.find((actor) => actor.name === actorName).waypoints[0];
    return [point.x_m, point.y_m];
  }, target)).toEqual([target.initialX, target.initialY]);

  await page.mouse.move(target.clientX, target.clientY);
  await page.mouse.down();
  await page.mouse.move(target.clientX + 12, target.clientY);
  await page.mouse.up();
  await expect.poll(() => page.evaluate(({ actorName }) => (
    state.scenario.actors.find((actor) => actor.name === actorName).waypoints[0].x_m
  ), target)).not.toBe(target.initialX);
});

test("documentation pages expose a meaningful title and pass automated checks", async ({ page }) => {
  await page.goto("/docs/01-intersection-conflict.md");
  await expect(page).toHaveTitle(/Create an intersecting conflict.*scenario\.generator/);
  await expectNoAxeViolations(page);

  await page.locator("#docs-about").click();
  await expectNoAxeViolations(page);
  await page.locator("#docs-about-dialog").press("Escape");
  await page.locator("#docs-data-privacy").click();
  await expect(page.locator("#docs-data-privacy-dialog")).toBeVisible();
  await expect(page.locator("#docs-data-privacy-dialog a")).toHaveAttribute(
    "href",
    "https://scenario.center/privacy-policy/",
  );
  await expectNoAxeViolations(page);
  await page.locator("#docs-data-privacy-dialog").press("Escape");

  await page.setViewportSize({ width: 320, height: 800 });
  await expect.poll(() => page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }))).toEqual({ viewport: 320, document: 320 });
});

test("map mode, dynamic rows, parameter editing, and fullscreen remain accessible", async ({ page }) => {
  await openScenario(page);

  await page.locator("#mode-toggle").click();
  await page.locator("#add-actor").click();
  await expectNoAxeViolations(page);

  await page.locator("#mode-toggle").click();
  await page.locator("#actor-form summary", { hasText: "Parameter declarations" }).click();
  await page.locator("#edit-parameters").click();
  await expectNoAxeViolations(page);
  await page.locator("#parameter-dialog").press("Escape");

  await page.locator("#canvas-fullscreen").click();
  await expect(page.locator(".actors")).toHaveJSProperty("inert", true);
  await expect(page.locator(".inspector")).toHaveJSProperty("inert", true);
  await expect(page.locator("#canvas-fullscreen")).toBeFocused();
  await expectNoAxeViolations(page);
  await page.keyboard.press("Escape");
  await expect(page.locator("#canvas-fullscreen")).toBeFocused();
  await expect(page.locator(".actors")).toHaveJSProperty("inert", false);
});

test("validation errors identify and focus the field that needs correction", async ({ page }) => {
  await openScenario(page);

  const speed = page.locator("#waypoint-0-speed_mps");
  await speed.fill("-1");
  await speed.press("Tab");
  await expect(speed).toHaveAttribute("aria-invalid", "true");
  await expect(speed).toHaveAttribute("aria-describedby", "status");
  await expect(speed).toBeFocused();
  await expect(page.locator("#status")).not.toHaveText("Ready");

  await page.locator("#batch-export-mcap").uncheck();
  await page.locator("#generate-files").click();
  await expect(page.locator("#batch-export-mcap")).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#batch-export-mcap")).toBeFocused();
  await expect(page.locator("#status")).toContainText("Select at least one export format");
});
