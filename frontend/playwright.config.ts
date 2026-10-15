import { defineConfig, devices } from "@playwright/test";

// End-to-end test of the dashboard against a running backend with demo data:
//   backend:  python -m uvicorn app.main:app   (dashboard built with npm run build)
//             python -m scripts.simulate demo
//   here:     npx playwright install chromium   (once)
//             E2E_EMAIL=you@example.com E2E_PASSWORD=... E2E_META_APP_SECRET=... npm run e2e
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
