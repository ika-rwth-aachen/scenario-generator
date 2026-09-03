const path = require("path");
const { defineConfig } = require("@playwright/test");

const projectRoot = path.resolve(__dirname, "../..");
const defaultServerCommand = "python -m scenario_generator.webapp.server";
const configuredBasePath = process.env.SCENARIO_GENERATOR_BASE_PATH || "";
const configuredBaseURL = process.env.PLAYWRIGHT_BASE_URL
  || `http://localhost:8000${configuredBasePath}`;
const applicationBaseURL = new URL(`${configuredBaseURL.replace(/\/$/, "")}/`);

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
  webServer: {
    command: process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || defaultServerCommand,
    cwd: projectRoot,
    url: new URL("api/scenario", applicationBaseURL).toString(),
    reuseExistingServer: !process.env.CI,
    timeout: 30000
  }
});
