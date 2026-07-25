import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  expect: {
    timeout: 5000
  },
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "on-first-retry"
  },
  webServer: {
    // `npm.cmd` gibt es nur auf Windows; auf Linux/macOS (und im Docker-Build)
    // heisst das Binary `npm`.
    command: `${process.platform === "win32" ? "npm.cmd" : "npm"} run dev -- --port 5173`,
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 20000
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
