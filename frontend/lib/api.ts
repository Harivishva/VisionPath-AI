import type { Diagnostics, EhrSyncResult, FhirResult, SessionSnapshot, UnifiedMetrics } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body) headers["Content-Type"] = "application/json";
  if (token) headers["X-Session-Token"] = token;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Backend unreachable. Is the API running on " + API_URL + "?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface CreateSessionInput {
  reference: string;
  synthetic: boolean;
  patient_reported_notes: string;
  consent_acknowledged: boolean;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/api/health"),
  diagnostics: () => request<Diagnostics>("/api/diagnostics"),
  createSession: (body: CreateSessionInput) =>
    request<{ session_id: string; token: string; session: SessionSnapshot }>("/api/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  get: (id: string, t: string) => request<SessionSnapshot>(`/api/sessions/${id}`, {}, t),
  remove: (id: string, t: string) => request<{ deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }, t),
  control: (id: string, t: string, action: "start" | "pause" | "resume" | "stop") =>
    request<SessionSnapshot>(`/api/sessions/${id}/control`, { method: "POST", body: JSON.stringify({ action }) }, t),
  addTranscript: (id: string, t: string, text: string) =>
    request<SessionSnapshot>(`/api/sessions/${id}/transcript`, { method: "POST", body: JSON.stringify({ text }) }, t),
  loadSynthetic: (id: string, t: string) =>
    request<SessionSnapshot>(`/api/sessions/${id}/synthetic-transcript`, { method: "POST" }, t),
  editTranscript: (id: string, t: string, segId: string, text: string) =>
    request<SessionSnapshot>(`/api/sessions/${id}/transcript/${segId}`, { method: "PATCH", body: JSON.stringify({ text }) }, t),
  reviewSymptom: (id: string, t: string, mid: string, body: { status?: string; polarity?: string; onset_text?: string }) =>
    request<SessionSnapshot>(`/api/sessions/${id}/symptoms/${mid}`, { method: "PATCH", body: JSON.stringify(body) }, t),
  addObservation: (id: string, t: string, body: { text: string; concerning: boolean; urgency: string }) =>
    request<SessionSnapshot>(`/api/sessions/${id}/observations`, { method: "POST", body: JSON.stringify(body) }, t),
  ack: (id: string, t: string, alertId: string) =>
    request<SessionSnapshot>(`/api/sessions/${id}/alerts/${alertId}/ack`, { method: "POST" }, t),
  draftSummary: (id: string, t: string, mode: "deterministic" | "llm") =>
    request<{ text: string; origin: string; notice: string | null }>(`/api/sessions/${id}/summary/draft?mode=${mode}`, { method: "POST" }, t),
  editSummary: (id: string, t: string, body: { chief_complaint?: string; clinician_notes?: string; summary_text?: string }) =>
    request<SessionSnapshot>(`/api/sessions/${id}/summary`, { method: "PUT", body: JSON.stringify(body) }, t),
  review: (id: string, t: string, finalize: boolean, reviewer = "") =>
    request<SessionSnapshot>(`/api/sessions/${id}/review`, { method: "POST", body: JSON.stringify({ finalize, reviewer }) }, t),
  generateFhir: (id: string, t: string) => request<FhirResult>(`/api/sessions/${id}/fhir`, { method: "POST" }, t),
  waitingRoom: (id: string, t: string, action: "start" | "stop" | "ack") =>
    request<UnifiedMetrics["waiting_room_monitoring"]>(`/api/sessions/${id}/waiting-room/${action}`, { method: "POST" }, t),
  ehrSync: (bundle: Record<string, unknown>, t: string) =>
    request<EhrSyncResult>("/api/ehr/sync", { method: "POST", body: JSON.stringify(bundle) }, t),
};

export function wsUrl(sessionId: string, token: string): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
}
