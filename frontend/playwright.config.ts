import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
export default defineConfig({
  testDir: "./tests",
  testMatch: process.env.SCANDOC_TEST_URL
    ? "hosted.spec.ts"
    : "scanner.spec.ts",
  timeout: 60000,
  workers: 1,
  use: {
    actionTimeout: 15000,
    baseURL: process.env.SCANDOC_TEST_URL || "http://127.0.0.1:8787",
    ignoreHTTPSErrors: !!process.env.SCANDOC_TEST_URL,
    launchOptions: {
      args: process.env.SCANDOC_TEST_URL ? ["--ignore-certificate-errors"] : [],
    },
    channel: process.env.CI ? undefined : "chrome",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "android-layout",
      use: { ...devices["Pixel 7"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: process.env.SCANDOC_TEST_URL
    ? undefined
    : {
        command: "../.venv/bin/scandoc serve --port 8787",
        url: "http://127.0.0.1:8787/health",
        reuseExistingServer: false,
        env: {
          SCANDOC_DATA_DIR: mkdtempSync(join(tmpdir(), "scandoc-browser-")),
        },
      },
});
