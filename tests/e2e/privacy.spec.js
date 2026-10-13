const { expect, test } = require("@playwright/test");
const { openScenario } = require("./helpers");

test("data privacy dialogs delete the current session from application and docs", async ({ page }) => {
  await openScenario(page);

  await page.locator("#add-actor").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(3);
  await page.locator("#data-privacy").click();
  await expect(page.locator("#data-privacy-dialog")).toBeVisible();
  const privacyLink = page.locator("#data-privacy-dialog a");
  const deleteButton = page.locator("#delete-my-data");
  await expect(privacyLink).toHaveText("scenario.center privacy policy");
  await expect(privacyLink).toHaveAttribute(
    "href",
    "https://scenario.center/privacy-policy/",
  );
  const [linkStyle, buttonStyle] = await Promise.all([
    privacyLink.evaluate((element) => ({
      fontSize: getComputedStyle(element).fontSize,
      left: element.getBoundingClientRect().left,
    })),
    deleteButton.evaluate((element) => ({
      fontSize: getComputedStyle(element).fontSize,
      left: element.getBoundingClientRect().left,
    })),
  ]);
  expect(buttonStyle).toEqual(linkStyle);
  await page.mouse.click(1, 1);
  await expect(page.locator("#data-privacy-dialog")).toBeHidden();

  await page.locator("#data-privacy").click();
  await page.locator("#delete-my-data").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(2);

  await page.locator("#add-actor").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(3);
  await page.goto("docs/README.md");
  await page.locator("#docs-data-privacy").click();
  await expect(page.locator("#docs-data-privacy-dialog")).toBeVisible();
  await page.mouse.click(1, 1);
  await expect(page.locator("#docs-data-privacy-dialog")).toBeHidden();
  await page.locator("#docs-data-privacy").click();
  await Promise.all([
    page.waitForNavigation(),
    page.locator("#docs-delete-my-data").click(),
  ]);
  await expect(page.locator("#documentation-main")).toBeVisible();

  await openScenario(page);
  await expect(page.locator("#actor-list .actor")).toHaveCount(2);
});
