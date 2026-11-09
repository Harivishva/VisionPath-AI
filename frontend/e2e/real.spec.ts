import { expect, test } from "@playwright/test";
import { createSession, snapshot } from "./helpers";

// Exercises the machine's ACTUAL webcam and microphone. Run deliberately:
//   E2E_REAL_DEVICES=1 npx playwright test --project=real-devices
test.skip(!process.env.E2E_REAL_DEVICES, "set E2E_REAL_DEVICES=1 to use the real webcam and microphone");

test("@real actual webcam and microphone reach the CV and audio pipelines", async ({ page }) => {
  const h = await createSession(page, { synthetic: true, reference: "DEMO-REAL-DEVICES" });
  await page.getByRole("button", { name: "Start capture" }).click();
  await expect(page.getByText("Camera live")).toBeVisible({ timeout: 20_000 });
  const dev = await page.evaluate(async () => {
    const list = await navigator.mediaDevices.enumerateDevices();
    const v = document.querySelector("video") as HTMLVideoElement;
    return {
      cams: list.filter((d) => d.kind === "videoinput").length,
      mics: list.filter((d) => d.kind === "audioinput").length,
      videoSize: [v.videoWidth, v.videoHeight],
    };
  });
  console.log("REAL_DEVICES", JSON.stringify(dev));
  expect(dev.videoSize[0]).toBeGreaterThan(0);

  await expect.poll(async () => (await snapshot(page, h)).modalities.vision.frames_processed, { timeout: 30_000 }).toBeGreaterThan(10);
  await expect.poll(async () => (await snapshot(page, h)).modalities.audio.chunks_received, { timeout: 30_000 }).toBeGreaterThan(8);
  await page.waitForTimeout(4000);
  const s = await snapshot(page, h);
  const v = s.modalities.vision;
  console.log("REAL_VISION", JSON.stringify({ state: v.state, processed: v.frames_processed, face_frames: v.face_frames, accepted: v.accepted_frames, low_quality: v.low_quality_frames, fps: v.fps, latency_ms: v.mean_latency_ms, dropped: v.frames_dropped, reason: v.reason }));
  console.log("REAL_AUDIO", JSON.stringify({ state: s.modalities.audio.state, chunks: s.modalities.audio.chunks_received, dropped: s.modalities.audio.chunks_dropped, noise_floor: s.modalities.audio.noise_floor_dbfs }));
  await expect(page.getByTestId("audio-metrics")).toContainText("dBFS");

  if (process.env.E2E_SCREENSHOT) await page.screenshot({ path: process.env.E2E_SCREENSHOT });
  await page.getByRole("button", { name: "Stop session" }).click();
  await expect(page.getByText(/devices released/)).toBeVisible();
  expect((await snapshot(page, h)).privacy.media_released).toBe(true);
  expect(await page.evaluate(() => !!(document.querySelector("video") as HTMLVideoElement).srcObject)).toBe(false);
});
