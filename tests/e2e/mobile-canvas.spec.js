const { expect, test } = require("@playwright/test");
const { openScenario } = require("./helpers");

test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

/** Dispatch native Chromium touch input so Pointer Events and capture are exercised. */
async function dispatchTouch(session, type, points) {
  await session.send("Input.dispatchTouchEvent", {
    type,
    touchPoints: points.map(({ id, x, y }) => ({ id, x, y })),
  });
}

test("touch gestures pan and pinch-zoom the canvas without adding points", async ({ page }) => {
  await openScenario(page);
  const canvas = page.locator("#map-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const bounds = await canvas.boundingBox();
  expect(bounds).not.toBeNull();
  const initialPointCount = await page.locator("#waypoint-body tr").count();
  const session = await page.context().newCDPSession(page);

  const initialView = await page.evaluate(() => ({ ...state.view }));

  const start = { id: 0, x: bounds.x + bounds.width - 30, y: bounds.y + 30 };
  await dispatchTouch(session, "touchStart", [start]);
  await dispatchTouch(session, "touchMove", [{ ...start, x: start.x - 45, y: start.y + 20 }]);
  await dispatchTouch(session, "touchEnd", []);

  await page.waitForFunction((view) => state.camera && (state.camera.minX !== view.minX || state.camera.minY !== view.minY), initialView);
  const pannedCamera = await page.evaluate(() => ({ ...state.camera }));
  expect(pannedCamera.minX).not.toBe(initialView.minX);
  await expect(page.locator("#waypoint-body tr")).toHaveCount(initialPointCount);

  const first = { id: 0, x: bounds.x + bounds.width * 0.35, y: bounds.y + bounds.height * 0.35 };
  const second = { id: 1, x: bounds.x + bounds.width * 0.65, y: bounds.y + bounds.height * 0.35 };
  await dispatchTouch(session, "touchStart", [first, second]);
  await dispatchTouch(session, "touchMove", [
    { ...first, x: bounds.x + bounds.width * 0.2, y: first.y + 15 },
    { ...second, x: bounds.x + bounds.width * 0.8, y: second.y + 15 },
  ]);
  await dispatchTouch(session, "touchEnd", []);

  const pinchedCamera = await page.evaluate(() => ({ ...state.camera }));
  expect(pinchedCamera.scale).toBeGreaterThan(pannedCamera.scale);
  expect(pinchedCamera.minY).not.toBe(pannedCamera.minY);
  await expect(page.locator("#waypoint-body tr")).toHaveCount(initialPointCount);
});

test("touch taps add points and dragging a point still edits it", async ({ page }) => {
  await openScenario(page);
  const canvas = page.locator("#map-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const bounds = await canvas.boundingBox();
  expect(bounds).not.toBeNull();
  const rows = page.locator("#waypoint-body tr");
  const initialPointCount = await rows.count();

  await page.touchscreen.tap(bounds.x + bounds.width - 30, bounds.y + 30);
  await expect(rows).toHaveCount(initialPointCount + 1);

  const target = await page.evaluate(() => {
    const canvasRect = document.querySelector("#map-canvas").getBoundingClientRect();
    const actor = state.scenario.actors.find((entry) => entry.name === state.selected);
    const index = actor.waypoints.length - 1;
    const point = actor.waypoints[index];
    return {
      actorName: actor.name,
      index,
      initialX: point.x_m,
      x: canvasRect.left + (point.x_m - state.view.minX) * state.view.scale,
      y: canvasRect.top + canvasRect.height - (point.y_m - state.view.minY) * state.view.scale,
    };
  });
  const session = await page.context().newCDPSession(page);
  const start = { id: 0, x: target.x, y: target.y };
  await dispatchTouch(session, "touchStart", [start]);
  await dispatchTouch(session, "touchMove", [{ ...start, x: start.x - 20 }]);
  await dispatchTouch(session, "touchEnd", []);

  await expect.poll(() => page.evaluate(({ actorName, index }) => (
    state.scenario.actors.find((actor) => actor.name === actorName).waypoints[index].x_m
  ), target)).not.toBe(target.initialX);
  await expect(rows).toHaveCount(initialPointCount + 1);
});


test("canceling one pinch pointer continues panning with the remaining finger", async ({ page }) => {
  await openScenario(page);
  const canvas = page.locator("#map-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const bounds = await canvas.boundingBox();
  const session = await page.context().newCDPSession(page);
  const first = { id: 0, x: bounds.x + bounds.width * 0.3, y: bounds.y + 30 };
  const second = { id: 1, x: bounds.x + bounds.width * 0.7, y: bounds.y + 30 };
  await dispatchTouch(session, "touchStart", [first, second]);
  const camera = await page.evaluate(() => {
    const canvas = document.querySelector("#map-canvas");
    const [pointerId] = touchGesture.pointerIds;
    canvas.dispatchEvent(new PointerEvent("pointercancel", { pointerId, pointerType: "touch" }));
    if (touchGesture !== null || !state.panStart) throw new Error("Remaining finger did not resume panning");
    return { ...currentCamera() };
  });
  await dispatchTouch(session, "touchMove", [first, { ...second, x: second.x - 25, y: second.y + 20 }]);
  await expect.poll(() => page.evaluate(() => state.camera?.minX)).not.toBe(camera.minX);
  await dispatchTouch(session, "touchEnd", []);
});

test("pointer cancellation only restores the drag owned by that pointer", async ({ page }) => {
  await openScenario(page);
  await page.locator("#map-canvas").scrollIntoViewIfNeeded();
  const target = await page.evaluate(() => {
    const rect = document.querySelector("#map-canvas").getBoundingClientRect();
    const actor = state.scenario.actors.find((entry) => entry.name === state.selected);
    const point = actor.waypoints[1];
    return { x: rect.left + (point.x_m - state.view.minX) * state.view.scale,
      y: rect.top + rect.height - (point.y_m - state.view.minY) * state.view.scale };
  });
  const session = await page.context().newCDPSession(page);
  const finger = { id: 0, ...target };
  await dispatchTouch(session, "touchStart", [finger]);
  await dispatchTouch(session, "touchMove", [{ ...finger, x: finger.x + 20 }]);
  const result = await page.evaluate(() => {
    const canvas = document.querySelector("#map-canvas");
    const drag = state.dragTarget;
    if (!drag || !state.didDrag) throw new Error("Expected an active waypoint drag");
    const point = drag.actor.waypoints[drag.index];
    const preview = [point.x_m, point.y_m];
    canvas.dispatchEvent(new PointerEvent("pointercancel", { pointerId: drag.pointerId + 100, pointerType: "touch" }));
    const unrelatedPreserved = state.dragTarget === drag && point.x_m === preview[0] && point.y_m === preview[1];
    canvas.dispatchEvent(new PointerEvent("pointercancel", { pointerId: drag.pointerId, pointerType: "touch" }));
    return { unrelatedPreserved, cleared: state.dragTarget === null, restored: [point.x_m, point.y_m], original: drag.originalPoint };
  });
  expect(result.unrelatedPreserved).toBe(true);
  expect(result.cleared).toBe(true);
  expect(result.restored).toEqual(result.original);
  await dispatchTouch(session, "touchEnd", []);
});
