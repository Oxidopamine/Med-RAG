import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "html",
  use: {
    baseURL: "http://127.0.0.1:3127",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run build && npm run start -- --hostname 127.0.0.1 --port 3127",
    env: { NEXT_DIST_DIR: ".next-e2e" },
    url: "http://127.0.0.1:3127",
    reuseExistingServer: false,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    // webkit excluded on Windows: Defender blocks libcurl.dll bundled with
    // ms-playwright/webkit-2336. Safari coverage is provided by CI on macOS.
    // { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
