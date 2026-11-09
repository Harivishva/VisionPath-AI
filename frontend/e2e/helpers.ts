import { expect, type Page } from "@playwright/test";

export const API = "http://localhost:8000";

export interface Handle {
  id: string;
  token: string;
}

/** Opens the app, creates a session through the real UI and returns the session id/token (read from the real socket URL). */
export async function createSession(page: Page, opts: { synthetic?: boolean; reference?: string } = {}): Promise<Handle> {
  const wsUrl = new Promise<string>((resolve) => {
    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/sessions/")) resolve(ws.url());
    });
  });
  await page.goto("/");
  await expect(page.getByText(/Connected · v/)).toBeVisible();
  if (opts.reference) await page.getByLabel("Session reference").fill(opts.reference);
  const syn = page.getByRole("checkbox", { name: /Synthetic demonstration session/ });
  if ((await syn.isChecked()) !== (opts.synthetic ?? true)) await syn.click();
  await page.getByRole("checkbox", { name: /I have read the privacy information/ }).check();
  await page.getByRole("button", { name: "Create intake session" }).click();
  await expect(page.getByTestId("session-ref")).toBeVisible();
  const u = new URL(await wsUrl);
  return { id: u.pathname.split("/").pop()!, token: u.searchParams.get("token")! };
}

export async function snapshot(page: Page, h: Handle) {
  const r = await page.request.get(`${API}/api/sessions/${h.id}`, { headers: { "X-Session-Token": h.token } });
  expect(r.status()).toBe(200);
  return r.json();
}
