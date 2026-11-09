import { expect, test } from "@playwright/test";
import { createSession } from "./helpers";

const SIZES = [
  { name: "1366x768", width: 1366, height: 768 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1024x700", width: 1024, height: 700 },
];

for (const size of SIZES) {
  test(`layout at ${size.name}: no horizontal overflow, intake workspace fits without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await createSession(page, { synthetic: true });
    await page.getByRole("button", { name: "Load synthetic statement" }).click();
    await expect(page.getByTestId("alert-card").first()).toBeVisible();

    const overflow = () =>
      page.evaluate(() => ({
        x: document.documentElement.scrollWidth - window.innerWidth,
        y: document.documentElement.scrollHeight - window.innerHeight,
      }));

    for (const tab of ["Intake", "Summary", "FHIR export", "Diagnostics"]) {
      await page.getByRole("tab", { name: tab }).click();
      const o = await overflow();
      expect(o.x, `horizontal overflow on ${tab}`).toBeLessThanOrEqual(0);
      expect(o.y, `page-level vertical scroll on ${tab}`).toBeLessThanOrEqual(0); // panels scroll internally
    }

    // At the target laptop sizes every primary control is visible without scrolling the page.
    await page.getByRole("tab", { name: "Intake" }).click();
    if (size.width >= 1366) for (const el of [page.getByRole("button", { name: "Start capture" }), page.getByTestId("camera-stage"), page.getByTestId("alerts-panel"), page.getByLabel("Add statement manually")]) {
      await expect(el).toBeInViewport();
    }
    // Regression: the camera stage must keep its 4:3 shape (it once got squeezed to a thin strip by flex-shrink).
    const stage = await page.getByTestId("camera-stage").boundingBox();
    expect(stage!.height).toBeGreaterThan(stage!.width / (4 / 3) - 4);
    expect(stage!.height).toBeGreaterThanOrEqual(Math.min(size.height * 0.4, 280));
    if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/intake-${size.name}.png` });
  });
}
