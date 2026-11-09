import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { API, createSession, snapshot } from "./helpers";

test("fallback demonstration: synthetic statement -> alert -> review -> FHIR download -> cleanup (no devices needed)", async ({ page }) => {
  const h = await createSession(page, { synthetic: true, reference: "DEMO-E2E" });

  // 1. Synthetic transcript is clearly labelled and extraction preserves negation / uncertainty.
  await page.getByRole("button", { name: "Load synthetic statement" }).click();
  await expect(page.getByTestId("segment")).toContainText("synthetic demo patient");
  await expect(page.getByTestId("segment").getByText("SYNTHETIC DEMO", { exact: true })).toBeVisible();
  const symptoms = page.getByTestId("symptom");
  await expect(symptoms).toHaveCount(4);
  const row = (label: string) => symptoms.filter({ has: page.getByText(label, { exact: true }) });
  await expect(row("Chest pain / discomfort")).toContainText("negated");
  await expect(row("Weakness (arm/leg/one side)")).toContainText("uncertain");
  await expect(row("Weakness (arm/leg/one side)")).toContainText("not a definite time");

  // 2. Transparent rule trigger with exact evidence; indicator is a workflow label, not ESI.
  await expect(page.getByTestId("indicator-label")).toHaveText("Immediate human review");
  const alerts = page.getByTestId("alert-card");
  await expect(alerts.filter({ hasText: "R-NEURO-SUDDEN" }).first()).toContainText("Since about an hour ago my face feels like it is drooping");
  await expect(page.getByText("not ESI").first()).toBeVisible();
  // Chest pain was negated, so no chest-pain rule fired.
  await expect(alerts.filter({ hasText: "R-CHEST-001" })).toHaveCount(0);

  // 3. Unavailable modalities are reported as such, never as normal.
  await expect(page.getByTestId("vision-metrics")).toContainText("n/a");
  await expect(page.getByText("Not measured").first()).toBeVisible();

  // 4. Acknowledge an alert; finalizing is blocked until every extracted symptom is reviewed.
  await alerts.first().getByRole("button", { name: /Acknowledge/ }).click();
  await expect(alerts.first()).toContainText("Acknowledged");
  await page.getByRole("tab", { name: "Summary" }).click();
  await page.getByRole("textbox", { name: "Chief complaint" }).fill("Facial droop and speech difficulty (synthetic)");
  await page.getByRole("button", { name: "Generate draft" }).click();
  await expect(page.getByLabel(/Intake summary/)).toHaveValue(/SYNTHETIC DEMO DATA/);
  await page.getByRole("button", { name: "Save edits" }).click();
  await page.getByRole("button", { name: "Finalize review" }).click();
  await expect(page.getByText(/Confirm or reject \d+ pending/)).toBeVisible();
  await page.getByRole("tab", { name: "Intake" }).click();
  for (const name of ["Facial droop / facial change", "Speech difficulty / slurred speech", "Chest pain / discomfort", "Weakness (arm/leg/one side)"]) {
    await page.getByRole("button", { name: `Confirm ${name}` }).click();
  }
  await expect(page.getByText("all reviewed")).toBeVisible();

  // 5. Finalize, then generate and validate FHIR.
  await page.getByRole("tab", { name: "Summary" }).click();
  await page.getByLabel("Reviewer").fill("RN-DEMO");
  await page.getByRole("button", { name: "Finalize review" }).click();
  await expect(page.getByText(/Finalized/).first()).toBeVisible();
  await page.getByRole("tab", { name: "FHIR export" }).click();
  await page.getByRole("button", { name: "Generate & validate" }).click();
  await expect(page.getByText("Local structural validation passed")).toBeVisible();
  await expect(page.getByTestId("fhir-validation")).toContainText("Flag");
  await expect(page.getByTestId("fhir-validation")).not.toContainText(/compliant/i);

  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download JSON" }).click()]);
  const path = await download.path();
  const bundle = JSON.parse(fs.readFileSync(path!, "utf-8"));
  expect(bundle.resourceType).toBe("Bundle");
  expect(JSON.stringify(bundle)).toContain("HTEST"); // synthetic data tagged as test data
  const types = bundle.entry.map((e: { resource: { resourceType: string } }) => e.resource.resourceType);
  expect(types).toEqual(expect.arrayContaining(["Patient", "Encounter", "Observation", "Flag", "DocumentReference"]));
  await expect(page.getByText(/validated bundle saved/).first()).toBeVisible();

  // 6. Server-side check, then full cleanup.
  const snap = await snapshot(page, h);
  expect(snap.review.finalized).toBe(true);
  await page.getByRole("button", { name: /End & delete session/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const gone = await page.request.get(`${API}/api/sessions/${h.id}`, { headers: { "X-Session-Token": h.token } });
  expect(gone.status()).toBe(404);
});

test("manual transcript, correction and staff observation drive rules without any device or LLM", async ({ page }) => {
  await createSession(page, { synthetic: false, reference: "MANUAL-E2E" });
  await page.getByLabel("Add statement manually").fill("I have a headache and I do not have a fever.");
  await page.getByRole("button", { name: "Add to transcript" }).click();
  await expect(page.getByTestId("indicator-label")).toHaveText("Routine review");
  await page.getByRole("button", { name: /Correct transcript segment/ }).click();
  await page.getByLabel("Correct transcript text").fill("I have chest pain and I do not have a fever.");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByTestId("segment")).toContainText("corrected");
  await expect(page.getByTestId("indicator-label")).toHaveText("Immediate human review");
  await expect(page.getByTestId("symptom").filter({ has: page.getByText("Fever", { exact: true }) })).toContainText("negated");
  await page.getByText("Record a staff observation").click();
  await page.getByLabel("Staff observation").fill("Patient appears pale");
  await page.getByLabel(/Mark as concerning/).check();
  await page.getByRole("button", { name: "Add observation" }).click();
  await expect(page.getByTestId("alert-card").filter({ hasText: "R-STAFF-001" })).toBeVisible();
});

// Chromium's synthetic camera sporadically ends its own track (observed ~1 in 3 runs, with no stop()
// call from the app). The app reports that correctly ("Camera disconnected"), but this test needs a
// live camera for several seconds, so it is retried. Real-device behaviour is covered by real.spec.ts.
test.describe("synthetic devices", () => {
  test.describe.configure({ retries: 3 });

  test("capture pipeline end to end with Chromium's synthetic camera and microphone", async ({ page }) => {
    const h = await createSession(page, { synthetic: true });
    await page.getByRole("button", { name: "Start capture" }).click();
    await expect(page.getByText("Camera live")).toBeVisible();
    await expect(page.getByTestId("session-status")).toContainText("Live");
    // Frames and audio really reach the backend pipelines and come back with honest results.
    await expect.poll(async () => (await snapshot(page, h)).modalities.vision.frames_processed, { timeout: 30_000 }).toBeGreaterThan(3);
    await expect.poll(async () => (await snapshot(page, h)).modalities.audio.chunks_received, { timeout: 30_000 }).toBeGreaterThan(4);
    const snap = await snapshot(page, h);
    // The synthetic test pattern has no face: must be reported as not detected, never as a normal finding.
    expect(["not_detected", "low_quality"]).toContain(snap.modalities.vision.state);
    expect(snap.modalities.vision.face_frames).toBe(0);
    await expect(page.getByText(/Visual analysis unavailable — no face in frame|Insufficient signal/)).toBeVisible();
    await expect(page.getByTestId("vision-metrics")).toContainText("/s");

    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByTestId("camera-message")).toContainText(/paused/i);
    const hasStream = () => page.evaluate(() => !!(document.querySelector("video") as HTMLVideoElement | null)?.srcObject);
    expect(await hasStream()).toBe(false);
    const before = (await snapshot(page, h)).modalities.vision.frames_received;
    await page.waitForTimeout(1500);
    expect((await snapshot(page, h)).modalities.vision.frames_received).toBe(before); // nothing sent while paused

    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("Camera live")).toBeVisible();
    await page.getByRole("button", { name: "Stop session" }).click();
    await expect(page.getByText(/Session stopped · devices released/)).toBeVisible();
    expect(await hasStream()).toBe(false);
    const final = await snapshot(page, h);
    expect(final.status).toBe("stopped");
    expect(final.privacy).toEqual({ raw_media_persisted: false, media_released: true });
    await page.getByRole("tab", { name: "Diagnostics" }).click();
    await expect(page.getByTestId("diagnostics-view")).toContainText("Raw media persistence");
  });

  test("a camera that disconnects mid-session is reported and frame upload stops; Pause then Resume recovers", async ({ page }) => {
    const h = await createSession(page, { synthetic: true });
    await page.getByRole("button", { name: "Start capture" }).click();
    await expect(page.getByText("Camera live")).toBeVisible();
    await expect.poll(async () => (await snapshot(page, h)).modalities.vision.frames_received, { timeout: 30_000 }).toBeGreaterThan(2);
    // Simulate the device being unplugged: the browser fires `ended` on the live video track.
    await page.evaluate(() => {
      const t = (document.querySelector("video") as HTMLVideoElement).srcObject as MediaStream;
      t.getVideoTracks()[0].dispatchEvent(new Event("ended"));
    });
    await expect(page.getByTestId("camera-message")).toContainText(/Camera disconnected/);
    await page.waitForTimeout(800);
    const frozen = (await snapshot(page, h)).modalities.vision.frames_received;
    await page.waitForTimeout(1500);
    expect((await snapshot(page, h)).modalities.vision.frames_received).toBe(frozen); // no more frames uploaded
    await expect(page.getByTestId("session-status")).toContainText("Live"); // session itself stays usable
    await page.getByRole("button", { name: "Pause" }).click();
    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("Camera live")).toBeVisible();
    await page.getByRole("button", { name: "Stop session" }).click();
  });
});
