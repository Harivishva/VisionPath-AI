import { defineConfig } from "@playwright/test";

const channel = process.env.E2E_BROWSER_CHANNEL || "msedge"; // installed Edge/Chrome: no browser download needed
const base = { channel, headless: process.env.E2E_HEADED ? false : true, baseURL: "http://localhost:3000", permissions: ["camera", "microphone"] };

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  // Servers are expected to be running (see README). They are reused if already up.
  webServer: [
    {
      command: process.env.VP_BACKEND_CMD || "python -m uvicorn app.main:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/api/health",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    { command: "npm run start", url: "http://localhost:3000", reuseExistingServer: true, timeout: 60_000 },
  ],
  projects: [
    // No camera/mic hardware needed: exercises the whole pipeline with Chromium's synthetic devices.
    {
      name: "fake-devices",
      testMatch: /(workflow|devices|layout)\.spec\.ts/,
      grepInvert: /@real/,
      use: {
        ...base,
        launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
      },
    },
    // Uses the machine's actual webcam and microphone (permission prompt auto-accepted).
    // Run only deliberately:  E2E_REAL_DEVICES=1 npx playwright test --project=real-devices
    {
      name: "real-devices",
      testMatch: /real\.spec\.ts/,
      use: { ...base, launchOptions: { args: ["--use-fake-ui-for-media-stream"] } },
    },
  ],
});
