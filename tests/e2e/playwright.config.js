const path = require("path");
const { defineConfig } = require("@playwright/test");

const projectRoot = path.resolve(__dirname, "../..");
const python = process.env.PLAYWRIGHT_PYTHON || "python";
const defaultServerCommand = `${python} -m scenario_generator.webapp.server`;
const configuredBasePath = process.env.SCENARIO_GENERATOR_BASE_PATH || "";
const applicationBaseURL = new URL(process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000");
if (applicationBaseURL.pathname === "/" && configuredBasePath) {
  applicationBaseURL.pathname = configuredBasePath;
}
applicationBaseURL.pathname = `${applicationBaseURL.pathname.replace(/\/+$/, "")}/`;

// Dedicated instances keep the cookie tests independent of the main deployment.
const cookieDeployments = [
  { name: "session-isolation", port: 8001, migrate: "false", grep: /prefixed sessions/ },
  { name: "legacy-migration", port: 8002, migrate: "true", grep: /explicit legacy migration/ },
];
const cookieBaseURL = (port) => `http://127.0.0.1:${port}/generator/`;

module.exports = defineConfig({
  testDir: __dirname,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["junit", { outputFile: "playwright-report/results.xml" }], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: applicationBaseURL.toString(),
    browserName: "chromium",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure"
  },
  projects: [
    { name: "application", testIgnore: "**/session-cookies.spec.js" },
    ...cookieDeployments.map(({ name, port, grep }) => ({
      name,
      testMatch: "**/session-cookies.spec.js",
      grep,
      use: { baseURL: cookieBaseURL(port) },
    })),
  ],
  webServer: [{
    command: process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || defaultServerCommand,
    cwd: projectRoot,
    url: new URL("api/scenario", applicationBaseURL).toString(),
    reuseExistingServer: !process.env.CI,
    timeout: 30000
  }, ...cookieDeployments.map(({ port, migrate }) => ({
    command: `${python} -m uvicorn scenario_generator.webapp.server:app --host 127.0.0.1 --port ${port}`,
    cwd: projectRoot,
    url: new URL("api/scenario", cookieBaseURL(port)).toString(),
    env: {
      SCENARIO_GENERATOR_BASE_PATH: "/generator",
      SCENARIO_GENERATOR_PROXY_STRIPS_PREFIX: "false",
      SCENARIO_GENERATOR_MIGRATE_LEGACY_COOKIES: migrate,
      SCENARIO_GENERATOR_SESSION_COOKIE_PATH: "",
      SCENARIO_GENERATOR_SECURE_COOKIES: "auto",
    },
    reuseExistingServer: false,
    timeout: 30000,
  }))]
});
