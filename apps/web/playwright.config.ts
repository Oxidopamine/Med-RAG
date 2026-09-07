import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "html",
  // The mocked API answers at once, so a result that takes longer than the default five
  // seconds is a starved browser, not a slow product: two engines share the runner in CI.
  expect: { timeout: 10_000 },
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
    // ms-playwright/webkit-2336. This is commented out unconditionally, and CI runs
    // ubuntu-latest only, so **Safari is currently covered nowhere** - an earlier note
    // here claimed a macOS CI job that does not exist. Re-enable behind a
    // `process.platform !== "win32"` guard to get it back on Linux CI.
    // { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
