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

test("migrates legacy root cookies and prefers the browser's specific cookie", async ({ context, page, baseURL }) => {
  const url = new URL(baseURL);
  const cookiePath = url.pathname.replace(/\/$/, "");
  test.skip(!cookiePath, "Requires a deployment prefix");
  const name = "scenario_generator_session";
  const legacy = "a".repeat(32);
  const specific = "b".repeat(32);
  const cookie = (value, path) => ({ name, value, path, domain: url.hostname, httpOnly: true, sameSite: "Lax" });
  await context.addCookies([cookie(legacy, "/")]);
  await page.goto(baseURL);
  let cookies = (await context.cookies()).filter((item) => item.name === name);
  expect(cookies).toHaveLength(1);
  expect(cookies[0]).toMatchObject({ value: legacy, path: cookiePath });

  await context.addCookies([cookie(specific, cookiePath), cookie(legacy, "/")]);
  await page.reload();
  cookies = (await context.cookies()).filter((item) => item.name === name);
  expect(cookies).toHaveLength(1);
  expect(cookies[0]).toMatchObject({ value: specific, path: cookiePath });

  await context.addCookies([cookie(legacy, "/")]);
  const status = await page.evaluate(async () => {
    const prefix = document.querySelector('meta[name="scenario-generator-base-path"]').content;
    return (await fetch(`${prefix}/api/session`, { method: "DELETE" })).status;
  });
  expect(status).toBe(200);
  expect((await context.cookies()).filter((item) => item.name === name)).toHaveLength(0);
  await page.reload();
  cookies = (await context.cookies()).filter((item) => item.name === name);
  expect(cookies).toHaveLength(1);
  expect(cookies[0].value).not.toBe(legacy);
  expect(cookies[0].value).not.toBe(specific);
});
