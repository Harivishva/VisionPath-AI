import { vi } from "vitest";
import type { SessionSnapshot } from "@/lib/types";

export function makeSnapshot(over: Partial<SessionSnapshot> = {}): SessionSnapshot {
  return {
    id: "ses_test",
    reference: "DEMO-001",
    synthetic: true,
    created_at: new Date().toISOString(),
    status: "setup",
    segments: [],
    mentions: [],
    observations: [],
    alerts: [],
    review: {
      chief_complaint: "", chief_complaint_suggestion: "", clinician_notes: "", summary_text: "",
      summary_origin: "none", finalized: false, reviewer: "", finalized_at: null,
    },
    indicator: {
      level: "insufficient_information", label: "Insufficient information",
      basis: "No reported symptoms or staff observations have been entered",
      disclaimer: "Prototype workflow aid. Not an ESI level.",
    },
    modalities: {
      vision: {
        state: "not_measured", reason: null, frames_received: 0, frames_processed: 0, frames_dropped: 0,
        face_frames: 0, accepted_frames: 0, low_quality_frames: 0, fps: 0, mean_latency_ms: 0, mean_measurement: null,
      },
      audio: {
        state: "not_measured", reason: null, chunks_received: 0, chunks_dropped: 0, speech_segments: 0,
        speech_seconds: 0, clipped_chunks: 0, low_volume_chunks: 0,
      },
      transcript: { state: "not_measured", segments: 0, transcription_state: "idle", transcription_detail: null },
    },
    missing_information: ["Chief complaint not confirmed by staff"],
    providers: {
      transcription: { provider: "local", ready: true, detail: "Installed", model: "tiny.en" },
      llm: { provider: "none", ready: false, detail: "No LLM configured" },
    },
    errors: [],
    privacy: { raw_media_persisted: false, media_released: false },
    ...over,
  };
}

export const mention = (over = {}) => ({
  id: "sym_1", concept: "chest_pain", label: "Chest pain / discomfort", matched_text: "chest pain",
  sentence: "I have chest pain", segment_id: "seg_1", polarity: "affirmed" as const, reporter: "patient" as const,
  sudden: false, onset: null, status: "pending" as const, ...over,
});

export const segment = (over = {}) => ({
  id: "seg_1", text: "I have chest pain", original_text: "I have chest pain", source: "manual" as const,
  created_at: new Date().toISOString(), t_start_s: null, t_end_s: null, edited: false, ...over,
});

export const alertItem = (over = {}) => ({
  id: "alt_1", rule_id: "R-CHEST-001", title: "Chest pain or discomfort reported",
  severity: "immediate_human_review" as const, explanation: "Reported chest pain",
  recommended_action: "Alert the responsible clinician now for bedside assessment per local protocol.",
  evidence: [{ source: "symptom_mention", ref_id: "sym_1", text: '"I have chest pain"', timestamp: new Date().toISOString(), reporter: "patient" }],
  created_at: new Date().toISOString(), state: "active" as const, acknowledged_at: null, withdrawn_reason: null,
  pending_review: true, ...over,
});

// ---------------------------------------------------------------- mock WebSocket
export class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "blob";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }
  send(d: unknown) { this.sent.push(d); }
  close() { this.readyState = 3; this.onclose?.(); }
  emit(type: string, payload: unknown, session_id = "ses_test") {
    this.onmessage?.({ data: JSON.stringify({ event_id: "evt_x", type, session_id, ts: new Date().toISOString(), payload }) });
  }
  static get last() { return MockWebSocket.instances[MockWebSocket.instances.length - 1]; }
}

// ---------------------------------------------------------------- mock fetch
type Handler = (body: unknown, url: string) => unknown | { __status: number; body: unknown };
export function installFetch(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.pathname + url.search;
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body, headers: (init.headers ?? {}) as Record<string, string> });
    for (const [key, h] of Object.entries(routes)) {
      const [m, pattern] = key.split(" ");
      if (m === method && new RegExp(`^${pattern}$`).test(path)) {
        const out = h(body, path) as { __status?: number; body?: unknown };
        if (out && typeof out === "object" && "__status" in out) {
          return new Response(JSON.stringify(out.body), { status: out.__status! });
        }
        return new Response(JSON.stringify(out), { status: 200 });
      }
    }
    return new Response(JSON.stringify({ detail: `no mock for ${method} ${path}` }), { status: 404 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

// ---------------------------------------------------------------- mock media
export class FakeTrack {
  stopped = false;
  private listeners: Record<string, (() => void)[]> = {};
  stop = vi.fn(() => { this.stopped = true; });
  addEventListener(ev: string, cb: () => void) { (this.listeners[ev] ||= []).push(cb); }
  fire(ev: string) { (this.listeners[ev] || []).forEach((c) => c()); }
}
export class FakeStream {
  constructor(public tracks: FakeTrack[]) {}
  getTracks() { return this.tracks; }
}

export type MediaMode = "ok" | "denied" | "notfound";
export function mockMedia(cfg: { camera?: MediaMode; mic?: MediaMode } = {}) {
  const tracks: { kind: "video" | "audio"; track: FakeTrack }[] = [];
  const getUserMedia = vi.fn(async (c: MediaStreamConstraints) => {
    const kind = c.video ? "video" : "audio";
    const mode = (kind === "video" ? cfg.camera : cfg.mic) ?? "ok";
    if (mode === "denied") throw Object.assign(new Error("denied"), { name: "NotAllowedError" });
    if (mode === "notfound") throw Object.assign(new Error("none"), { name: "NotFoundError" });
    const t = new FakeTrack();
    tracks.push({ kind, track: t });
    return new FakeStream([t]) as unknown as MediaStream;
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia, enumerateDevices: vi.fn(async () => [{ kind: "videoinput" }, { kind: "audioinput" }]) },
  });
  class FakeAudioContext {
    state = "running";
    audioWorklet = { addModule: vi.fn(async () => undefined) };
    createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() }; }
    async resume() { /* noop */ }
    async close() { /* noop */ }
  }
  class FakeWorkletNode {
    port = { onmessage: null as unknown, close: vi.fn() };
    connect() { /* noop */ }
    disconnect() { /* noop */ }
  }
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: vi.fn(async () => undefined) });
  return { getUserMedia, tracks };
}
