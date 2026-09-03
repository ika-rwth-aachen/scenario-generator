const { expect } = require("@playwright/test");

async function openScenario(page) {
  await page.goto("./");
  await expect(page.locator("#actor-list .actor")).toHaveCount(2);
}

async function ensureVehicleMode(page) {
  if ((await page.locator("#mode-toggle").textContent()).includes("trajectory mode")) {
    await page.locator("#mode-toggle").click();
  }
  await expect(page.locator("#primary-title")).toHaveText("Actors");
}

module.exports = { ensureVehicleMode, openScenario };
