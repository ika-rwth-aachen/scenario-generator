const path = require("path");
const { defineConfig } = require("@playwright/test");

const projectRoot = path.resolve(__dirname, "../..");
const defaultServerCommand = "python -m scenario_generator.webapp.server";

module.exports = defineConfig({
  testDir: __dirname,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["junit", { outputFile: "playwright-report/results.xml" }], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000",
    browserName: "chromium",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure"
  },
  webServer: {
    command: process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || defaultServerCommand,
    cwd: projectRoot,
    url: `${process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000"}/api/scenario`,
    reuseExistingServer: !process.env.CI,
    timeout: 30000
  }
});
