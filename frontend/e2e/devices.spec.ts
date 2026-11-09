import { expect, test } from "@playwright/test";
import { createSession } from "./helpers";

// Top-level (not in a describe group): launch options force a fresh worker.
test.use({ permissions: [], launchOptions: { args: ["--deny-permission-prompts", "--use-fake-device-for-media-stream"] } });

test("permission denial: camera and microphone denied give explicit messages and manual intake still works", async ({ page }) => {
  await createSession(page, { synthetic: false });
  await page.getByRole("button", { name: "Start capture" }).click();
  await expect(page.getByTestId("camera-message")).toContainText(/permission was denied/i);
  await expect(page.getByTestId("mic-message")).toContainText(/permission was denied/i);
  await expect(page.getByTestId("session-status")).toContainText("Live");
  await page.getByLabel("Add statement manually").fill("I have shortness of breath.");
  await page.getByRole("button", { name: "Add to transcript" }).click();
  await expect(page.getByTestId("alert-card").filter({ hasText: "R-BREATH-001" })).toBeVisible();
});
