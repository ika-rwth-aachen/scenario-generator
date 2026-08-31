const { expect, test } = require("@playwright/test");
const { ensureVehicleMode, openScenario } = require("./helpers");

test("generates the scenario configuration download", async ({ page }) => {
  await openScenario(page);
  await ensureVehicleMode(page);
  const downloadPromise = page.waitForEvent("download");
  await page.locator('button[data-export="config"]').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("scenario_config.json");
  await expect(page.locator("#status")).toHaveText("Download ready");
});
