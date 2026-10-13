const { expect, test } = require("@playwright/test");
const { openScenario } = require("./helpers");

test("prefixed sessions leave a root instance's cookie untouched", async ({ context, page, baseURL }) => {
  const url = new URL(baseURL);
  const legacyName = "scenario_generator_session";
  const legacyValue = "a".repeat(32);
  await context.addCookies([{ name: legacyName, value: legacyValue, path: "/", domain: url.hostname, httpOnly: true, sameSite: "Lax" }]);
  await openScenario(page);
  let cookies = await context.cookies();
  expect(cookies.find((cookie) => cookie.name === legacyName)).toMatchObject({ value: legacyValue, path: "/" });
  const scoped = cookies.find((cookie) => cookie.name.startsWith(legacyName + "_"));
  expect(scoped.path).toBe(url.pathname.replace(/\/$/, ""));
  expect(scoped.value).not.toBe(legacyValue);
  const response = await context.request.get(new URL("api/scenario", baseURL).toString());
  expect(response.headers()["set-cookie"]).toBeUndefined();
  await context.request.delete(new URL("api/session", baseURL).toString());
  cookies = await context.cookies();
  expect(cookies.filter((cookie) => cookie.name.startsWith(legacyName))).toEqual([
    expect.objectContaining({ name: legacyName, value: legacyValue, path: "/" }),
  ]);
});

test("explicit legacy migration is one-off and prefers the browser's specific cookie", async ({ context, page, baseURL }) => {
  const url = new URL(baseURL);
  const cookiePath = url.pathname.replace(/\/$/, "");
  const legacyName = "scenario_generator_session";
  await openScenario(page);
  await page.locator("#add-actor").click();
  await expect(page.locator("#actor-list .actor")).toHaveCount(3);
  const scoped = (await context.cookies()).find((cookie) => cookie.name.startsWith(legacyName + "_"));
  await context.clearCookies({ name: scoped.name });
  const cookie = (value, path) => ({ name: legacyName, value, path, domain: url.hostname, httpOnly: true, sameSite: "Lax" });
  await context.addCookies([cookie(scoped.value, cookiePath), cookie("a".repeat(32), "/")]);
  await page.reload();
  await expect(page.locator("#actor-list .actor")).toHaveCount(3);
  let cookies = (await context.cookies()).filter((item) => item.name.startsWith(legacyName));
  expect(cookies).toHaveLength(1);
  expect(cookies[0]).toMatchObject({ name: scoped.name, value: scoped.value, path: cookiePath });
  const ordinary = await context.request.get(new URL("api/scenario", baseURL).toString());
  expect(ordinary.headers()["set-cookie"]).toBeUndefined();

  await context.addCookies([cookie("a".repeat(32), "/")]);
  const deletion = await context.request.delete(new URL("api/session", baseURL).toString());
  expect(deletion.status()).toBe(200);
  expect((await context.cookies()).filter((item) => item.name.startsWith(legacyName))).toHaveLength(0);
  // Emulate an old response arriving after DELETE and restoring a stale cookie.
  await context.addCookies([scoped]);
  await page.reload();
  await expect(page.locator("#actor-list .actor")).toHaveCount(2);
  cookies = (await context.cookies()).filter((item) => item.name.startsWith(legacyName));
  expect(cookies).toHaveLength(1);
  expect(cookies[0].value).not.toBe(scoped.value);
});
