import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/components/App";
import type { SessionSnapshot } from "@/lib/types";
import {
  alertItem, installFetch, makeSnapshot, mention, MockWebSocket, mockMedia, segment,
} from "./helpers";

const DIAG = {
  version: "0.1.0", python: "3.13", environment: "test",
  vision: { ready: true, error: null, model: "MediaPipe FaceLandmarker" },
  transcription: { provider: "local", ready: true, detail: "Installed; model loads on first utterance", model: "tiny.en" },
  llm: { provider: "none", ready: false, detail: "No LLM configured; deterministic summary is used" },
  active_sessions: 0, raw_media_persistence: false, limits: {},
};

let S: SessionSnapshot;
let calls: ReturnType<typeof installFetch>;

function setupServer(extra: Record<string, (b: unknown, p: string) => unknown> = {}) {
  S = makeSnapshot();
  calls = installFetch({
    "GET /api/diagnostics": () => DIAG,
    "POST /api/sessions": () => ({ session_id: "ses_test", token: "tok_secret_123", session: S }),
    "POST /api/sessions/ses_test/control": (b) => {
      const action = (b as { action: string }).action;
      S = { ...S, status: ({ start: "live", pause: "paused", resume: "live", stop: "stopped" } as const)[action as "start"] };
      return S;
    },
    "DELETE /api/sessions/ses_test": () => ({ deleted: true }),
    ...extra,
  });
}

async function createSession(user: ReturnType<typeof userEvent.setup>) {
  render(<App />);
  await screen.findByText(/Connected/);
  await user.click(screen.getByRole("checkbox", { name: /I have read the privacy information/ }));
  await user.click(screen.getByRole("button", { name: "Create intake session" }));
  await screen.findByTestId("session-ref");
  await waitFor(() => expect(screen.getByTestId("ws-status")).toHaveTextContent("open"));
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.unstubAllGlobals();
  vi.stubGlobal("WebSocket", MockWebSocket);
});

describe("initial rendering & setup", () => {
  it("renders the setup screen with privacy information and readiness", async () => {
    setupServer();
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("VisionPath");
    expect(screen.getByText(/No camera or microphone access until you press Start capture/)).toBeInTheDocument();
    const readiness = await screen.findByTestId("readiness");
    await waitFor(() => expect(within(readiness).getByText(/Connected/)).toBeInTheDocument());
    expect(within(readiness).getByText("Ready")).toBeInTheDocument();
    expect(within(readiness).getByText(/local/)).toBeInTheDocument();
  });

  it("requires consent and a valid reference before creating a session", async () => {
    setupServer();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/Connected/);
    const create = screen.getByRole("button", { name: "Create intake session" });
    expect(create).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I have read the privacy information/ }));
    expect(create).toBeEnabled();
    await user.clear(screen.getByLabelText("Session reference"));
    await user.type(screen.getByLabelText("Session reference"), "<bad>");
    expect(create).toBeDisabled();
  });

  it("shows an unavailable state and blocks creation when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<App />);
    expect(await screen.findByText(/Backend unreachable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create intake session" })).toBeDisabled();
  });

  it("creates a session, sends the token only as a header/query, and opens the socket without activating devices", async () => {
    setupServer();
    const media = mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    expect(screen.getByTestId("session-ref")).toHaveTextContent("DEMO-001");
    expect(screen.getAllByText("SYNTHETIC DEMO").length).toBeGreaterThan(0);
    expect(MockWebSocket.last.url).toContain("/ws/sessions/ses_test?token=tok_secret_123");
    expect(media.getUserMedia).not.toHaveBeenCalled(); // never silent
    expect(screen.getByTestId("camera-message")).toHaveTextContent(/Press Start capture/);
    const body = calls.find((c) => c.method === "POST" && c.path === "/api/sessions")!.body as Record<string, unknown>;
    expect(body.consent_acknowledged).toBe(true);
  });

  it("surfaces server validation errors on creation", async () => {
    setupServer({ "POST /api/sessions": () => ({ __status: 429, body: { detail: "Maximum concurrent sessions reached" } }) });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/Connected/);
    await user.click(screen.getByRole("checkbox", { name: /I have read the privacy information/ }));
    await user.click(screen.getByRole("button", { name: "Create intake session" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Maximum concurrent sessions/);
  });
});

describe("device permissions", () => {
  it("camera permission denied: explicit message, microphone still works, manual intake remains usable", async () => {
    setupServer({ "POST /api/sessions/ses_test/transcript": (b) => {
      S = { ...S, segments: [segment({ text: (b as { text: string }).text })] };
      return S;
    } });
    const media = mockMedia({ camera: "denied", mic: "ok" });
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    expect(await screen.findByText(/Camera permission was denied/)).toBeInTheDocument();
    expect(screen.getByTestId("mic-status")).toHaveTextContent("live");
    expect(media.getUserMedia).toHaveBeenCalledTimes(2);
    await user.type(screen.getByLabelText("Add statement manually"), "I have chest pain");
    await user.click(screen.getByRole("button", { name: "Add to transcript" }));
    expect(await screen.findByTestId("segment")).toHaveTextContent("I have chest pain");
  });

  it("microphone permission denied: explicit message and camera continues", async () => {
    setupServer();
    mockMedia({ camera: "ok", mic: "denied" });
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    expect(await screen.findByTestId("mic-message")).toHaveTextContent(/Microphone permission was denied/);
    expect(screen.getByText("Camera live")).toBeInTheDocument();
  });

  it("no devices at all: informs the user and keeps the session usable", async () => {
    setupServer();
    mockMedia({ camera: "notfound", mic: "notfound" });
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    expect(await screen.findByText(/No camera or microphone available/)).toBeInTheDocument();
    expect(screen.getByTestId("camera-message")).toHaveTextContent(/No camera was found/);
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
  });
});

describe("session controls & media release", () => {
  it("start, pause, resume and stop drive the API and release every track", async () => {
    setupServer();
    const media = mockMedia();
    const user = userEvent.setup();
    await createSession(user);

    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    expect(screen.getByTestId("session-status")).toHaveTextContent("Live");
    expect(media.tracks).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Pause" }));
    await screen.findByRole("button", { name: "Resume" });
    expect(screen.getByTestId("session-status")).toHaveTextContent("Paused");
    expect(media.tracks.every((t) => t.track.stopped)).toBe(true);
    expect(screen.getByTestId("camera-message")).toHaveTextContent(/paused/i);

    await user.click(screen.getByRole("button", { name: "Resume" }));
    await screen.findByRole("button", { name: "Pause" });
    expect(media.tracks).toHaveLength(4); // re-acquired after resume
    const fresh = media.tracks.slice(2);

    await user.click(screen.getByRole("button", { name: "Stop session" }));
    await screen.findByText(/Session stopped · devices released/);
    expect(fresh.every((t) => t.track.stopped)).toBe(true);
    expect(screen.getByTestId("session-status")).toHaveTextContent("Stopped");
    const actions = calls.filter((c) => c.path.endsWith("/control")).map((c) => (c.body as { action: string }).action);
    expect(actions).toEqual(["start", "pause", "resume", "stop"]);
  });

  it("ending the session deletes it on the server and releases devices", async () => {
    setupServer();
    const media = mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    await user.click(screen.getByRole("button", { name: /End & delete session/ }));
    await screen.findByRole("heading", { level: 1 });
    expect(media.tracks.every((t) => t.track.stopped)).toBe(true);
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/sessions/ses_test")).toBe(true);
  });

  it("reports a disconnected device when a track ends", async () => {
    setupServer();
    const media = mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    act(() => media.tracks[0].track.fire("ended"));
    expect(await screen.findAllByText(/Camera disconnected/)).not.toHaveLength(0);
  });
});

describe("live events", () => {
  it("renders honest vision states: low quality and no face are never shown as normal", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    const base = { face_confidence: null, quality: { brightness: 20, sharpness: 80 }, head_pose_deg: null, measurement: null, smoothed: null, overlay: null, latency_ms: 30, processed_at: "" };
    act(() => MockWebSocket.last.emit("vision.observation", { ...base, state: "low_quality", reason: "Image too dark for reliable landmark geometry", face_detected: true }));
    expect(await screen.findByText(/Insufficient signal: Image too dark/)).toBeInTheDocument();
    act(() => MockWebSocket.last.emit("vision.observation", { ...base, state: "not_detected", reason: "No face", face_detected: false }));
    expect(await screen.findByText(/Visual analysis unavailable — no face in frame/)).toBeInTheDocument();
    expect(screen.getByText("No face detected")).toBeInTheDocument();
    expect(screen.getByTestId("vision-metrics")).toHaveTextContent("n/a"); // model confidence never invented
  });

  it("shows measured geometry only when the backend supplies a quality-gated measurement", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    act(() => MockWebSocket.last.emit("vision.observation", {
      state: "pending_review", reason: null, face_detected: true, face_confidence: null,
      quality: { brightness: 120, sharpness: 90 }, head_pose_deg: { yaw: 3, pitch: -2, roll: 1 },
      measurement: { mouth_corner_vertical_offset: 0.01, brow_height_offset: 0.0, eye_opening_offset: 0.02 },
      smoothed: { mouth_corner_vertical_offset: 0.012, brow_height_offset: 0.001, eye_opening_offset: 0.021 },
      overlay: { oval: [[0.2, 0.2], [0.8, 0.2], [0.5, 0.9]] }, latency_ms: 25, processed_at: "",
    }));
    expect(await screen.findByText("Face tracked")).toBeInTheDocument();
    expect(screen.getByTestId("geometry")).toHaveTextContent("0.012");
    expect(screen.getByTestId("geometry")).toHaveTextContent(/not a clinical measurement/i);
    expect(screen.getByTestId("vision-metrics")).toHaveTextContent("3° / -2°");
  });

  it("keeps live counters current from periodic video/audio status events", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    const base = makeSnapshot().modalities;
    act(() => {
      MockWebSocket.last.emit("video.status", { ...base.vision, state: "not_detected", frames_processed: 42, accepted_frames: 0, fps: 4.8, mean_latency_ms: 37 });
      MockWebSocket.last.emit("audio.status", { ...base.audio, state: "pending_review", speech_segments: 2, speech_seconds: 5.5 });
    });
    const vm = await screen.findByTestId("vision-metrics");
    expect(vm).toHaveTextContent("4.8 /s");
    expect(vm).toHaveTextContent("37 ms");
    expect(vm).toHaveTextContent("0/42");
    expect(screen.getByTestId("audio-metrics")).toHaveTextContent("5.5 s");
  });

  it("renders audio features and insufficient-audio-quality state", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByRole("button", { name: "Pause" });
    act(() => MockWebSocket.last.emit("audio.feature", {
      rms_dbfs: -20, voice_activity: true, f0_hz: null, f0_periodicity: 0.2, zero_crossing_rate: 0.1,
      clipping_fraction: 0.02, quality: "low_quality", quality_reason: "Signal clipping detected; reduce input gain",
    }));
    const m = await screen.findByTestId("audio-metrics");
    expect(m).toHaveTextContent("-20 dBFS");
    expect(m).toHaveTextContent("withheld"); // F0 not guessed
    expect(m).toHaveTextContent(/Insufficient audio quality: Signal clipping/);
    expect(screen.getByRole("meter", { name: "Microphone level" })).toHaveAttribute("aria-valuenow", "-20");
  });

  it("shows transcription in progress and unavailable provider states", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    act(() => MockWebSocket.last.emit("transcript.partial", { id: "seg_x", state: "transcribing" }));
    expect(await screen.findAllByText(/transcribing/i)).not.toHaveLength(0);
    S = makeSnapshot({ providers: { transcription: { provider: "none", ready: false, detail: "No transcription provider configured; use manual transcript entry" }, llm: S.providers.llm } });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    expect(await screen.findByTestId("stt-unavailable")).toHaveTextContent(/Spoken words are not transcribed/);
  });

  it("renders a safety alert with evidence, and acknowledgement calls the API", async () => {
    const ackedAlert = alertItem({ state: "acknowledged", acknowledged_at: new Date().toISOString() });
    setupServer({
      "POST /api/sessions/ses_test/alerts/alt_1/ack": () => {
        S = { ...S, alerts: [ackedAlert] };
        return S;
      },
    });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({
      segments: [segment()], mentions: [mention()], alerts: [alertItem()],
      indicator: { level: "immediate_human_review", label: "Immediate human review", basis: "Highest triggered rule: R-CHEST-001", disclaimer: "" },
    });
    act(() => {
      MockWebSocket.last.emit("session.snapshot", S);
      MockWebSocket.last.emit("safety.alert", { alert: alertItem() });
    });
    const card = await screen.findByTestId("alert-card");
    expect(screen.getByTestId("indicator-label")).toHaveTextContent("Immediate human review");
    expect(card).toHaveTextContent("Chest pain or discomfort reported");
    expect(card).toHaveTextContent('"I have chest pain"'); // exact evidence
    expect(card).toHaveTextContent("R-CHEST-001");
    expect(card).toHaveTextContent("pending review");
    expect(screen.getByText(/not ESI/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: /Acknowledge alert/ }));
    await waitFor(() => expect(screen.getByTestId("alert-card")).toHaveTextContent(/Acknowledged/));
  });

  it("shows an alert even while camera and microphone are unavailable", async () => {
    setupServer();
    mockMedia({ camera: "denied", mic: "denied" });
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("button", { name: "Start capture" }));
    await screen.findByText(/Camera permission was denied/);
    S = makeSnapshot({ status: "live", alerts: [alertItem()], indicator: { level: "immediate_human_review", label: "Immediate human review", basis: "x", disclaimer: "" } });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    expect(await screen.findByTestId("alert-card")).toBeInTheDocument();
    expect(screen.getByTestId("camera-message")).toBeInTheDocument();
  });

  it("warns about reconnection instead of silently losing data", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      setupServer();
      mockMedia();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await createSession(user);
      act(() => MockWebSocket.last.onclose?.());
      expect(await screen.findByText(/Reconnecting to the backend/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("transcript and symptoms review", () => {
  it("corrects a transcript segment and keeps the original visible", async () => {
    setupServer({
      "PATCH /api/sessions/ses_test/transcript/seg_1": (b) => {
        S = { ...S, segments: [segment({ text: (b as { text: string }).text, edited: true })] };
        return S;
      },
    });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({ segments: [segment({ text: "I have chess pain", original_text: "I have chess pain" })] });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    await user.click(await screen.findByRole("button", { name: /Correct transcript segment/ }));
    const box = screen.getByLabelText("Correct transcript text");
    await user.clear(box);
    await user.type(box, "I have chest pain");
    await user.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(screen.getByTestId("segment")).toHaveTextContent("I have chest pain"));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ text: "I have chest pain" });
    expect(screen.getByText("corrected")).toBeInTheDocument();
  });

  it("shows polarity, uncertain onset and lets staff confirm or reject extracted symptoms", async () => {
    setupServer({
      "PATCH /api/sessions/ses_test/symptoms/sym_1": (b) => {
        S = { ...S, mentions: [{ ...S.mentions[0], status: (b as { status: "confirmed" }).status }] };
        return S;
      },
    });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({ mentions: [mention({ polarity: "uncertain", onset: { text: "this morning", certain: false } })] });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    const row = await screen.findByTestId("symptom");
    expect(row).toHaveTextContent("uncertain");
    expect(row).toHaveTextContent(/not a definite time/);
    await user.click(within(row).getByRole("button", { name: "Confirm Chest pain / discomfort" }));
    await waitFor(() => expect(screen.getByTestId("symptom")).toHaveTextContent("confirmed"));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ status: "confirmed" });
  });
});

describe("summary editing and finalization", () => {
  it("edits chief complaint/summary, saves, and finalizes the review", async () => {
    setupServer({
      "PUT /api/sessions/ses_test/summary": (b) => {
        const body = b as Record<string, string>;
        S = { ...S, review: { ...S.review, chief_complaint: body.chief_complaint, clinician_notes: body.clinician_notes, summary_text: body.summary_text, summary_origin: "clinician" } };
        return S;
      },
      "POST /api/sessions/ses_test/summary/draft\\?mode=deterministic": () => {
        S = { ...S, review: { ...S.review, summary_text: "DRAFT TEXT", summary_origin: "deterministic" } };
        return { text: "DRAFT TEXT", origin: "deterministic", notice: null };
      },
      "GET /api/sessions/ses_test": () => S,
      "POST /api/sessions/ses_test/review": (b) => {
        S = { ...S, review: { ...S.review, finalized: (b as { finalize: boolean }).finalize, finalized_at: new Date().toISOString() } };
        return S;
      },
    });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({ review: { ...makeSnapshot().review, chief_complaint_suggestion: "Chest pain / discomfort" } });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    await user.click(screen.getByRole("tab", { name: "Summary" }));
    expect(screen.getByText(/Machine suggestion \(unconfirmed\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use suggestion" }));
    expect(screen.getByRole("textbox", { name: "Chief complaint" })).toHaveValue("Chest pain / discomfort");
    expect(screen.getByRole("button", { name: "Draft with LLM" })).toBeDisabled(); // not configured
    await user.click(screen.getByRole("button", { name: "Generate draft" }));
    await waitFor(() => expect(screen.getByLabelText(/Intake summary/)).toHaveValue("DRAFT TEXT"));
    await user.type(screen.getByLabelText("Clinician notes"), "seen by RN");
    await user.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ chief_complaint: "Chest pain / discomfort", clinician_notes: "seen by RN" });
    await user.click(screen.getByRole("button", { name: "Finalize review" }));
    expect(await screen.findByText(/Finalized/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Chief complaint" })).toBeDisabled();
  });

  it("separates observed, reported, machine-interpreted and clinician content and keeps missing-data states distinct", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({
      mentions: [mention({ status: "confirmed" })], alerts: [alertItem()],
      modalities: { ...makeSnapshot().modalities, vision: { ...makeSnapshot().modalities.vision, state: "low_quality", reason: "Image too dark" }, audio: { ...makeSnapshot().modalities.audio, state: "not_detected" } },
    });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    await user.click(screen.getByRole("tab", { name: "Summary" }));
    const view = screen.getByTestId("summary-view");
    expect(within(view).getByText("Patient / observer-reported")).toBeInTheDocument();
    expect(within(view).getByText("Observed technical signals")).toBeInTheDocument();
    expect(within(view).getByText(/Machine interpretation/)).toBeInTheDocument();
    expect(within(view).getByText("Low quality")).toBeInTheDocument();
    expect(within(view).getByText("Not detected")).toBeInTheDocument();
    expect(within(view).getByText("Not measured")).toBeInTheDocument();
    expect(within(view).getByTestId("verification-list")).toHaveTextContent(/Chief complaint not confirmed/);
  });
});

describe("FHIR preview, validation and download", () => {
  const BUNDLE = { resourceType: "Bundle", type: "collection", entry: [{ resource: { resourceType: "Patient" } }] };
  const result = (over = {}) => ({
    bundle: BUNDLE,
    validation: { valid: true, errors: [], resource_counts: { Patient: 1, Encounter: 1 }, references_checked: 4, standard: "FHIR R4 (fhir.resources R4B)", disclaimer: "Local structural validation only." },
    notes: [], finalized: true, exportable: true, generated_at: "", ...over,
  });

  it("generates, previews, copies and downloads a validated bundle", async () => {
    setupServer({ "POST /api/sessions/ses_test/fhir": () => result() });
    mockMedia();
    const createUrl = vi.fn(() => "blob:fake");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() }));
    const writeText = vi.fn(async () => undefined);
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } }); // after setup(): user-event installs its own stub
    await createSession(user);
    S = makeSnapshot({ review: { ...makeSnapshot().review, finalized: true, finalized_at: new Date().toISOString() } });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    await user.click(screen.getByRole("tab", { name: "FHIR export" }));
    expect(screen.getByRole("button", { name: "Download JSON" })).toBeDisabled(); // nothing generated yet
    await user.click(screen.getByRole("button", { name: "Generate & validate" }));
    const v = await screen.findByTestId("fhir-validation");
    expect(v).toHaveTextContent("Local structural validation passed");
    expect(v).toHaveTextContent("Patient × 1");
    expect(v).not.toHaveTextContent(/compliant/i);
    expect(screen.getByTestId("fhir-json")).toHaveTextContent('"resourceType": "Bundle"');
    await user.click(screen.getByRole("button", { name: "Copy JSON" }));
    expect(writeText).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Download JSON" }));
    expect(createUrl).toHaveBeenCalled();
    expect(await screen.findByText(/validated bundle saved/)).toBeInTheDocument();
  });

  it("shows validation errors and blocks download when the bundle is invalid", async () => {
    setupServer({
      "POST /api/sessions/ses_test/fhir": () => result({
        validation: { valid: false, errors: [{ resource: "Observation[3]", path: "reference", message: "Reference 'urn:uuid:x' does not resolve inside the bundle" }], resource_counts: { Observation: 1 }, references_checked: 1, standard: "FHIR R4", disclaimer: "d" },
        exportable: false,
      }),
    });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    S = makeSnapshot({ review: { ...makeSnapshot().review, finalized: true } });
    act(() => MockWebSocket.last.emit("session.snapshot", S));
    await user.click(screen.getByRole("tab", { name: "FHIR export" }));
    await user.click(screen.getByRole("button", { name: "Generate & validate" }));
    expect(await screen.findByText("Validation failed")).toBeInTheDocument();
    expect(screen.getByTestId("fhir-errors")).toHaveTextContent(/does not resolve/);
    expect(screen.getByRole("button", { name: "Download JSON" })).toBeDisabled();
  });

  it("keeps download disabled until the review is finalized", async () => {
    setupServer({ "POST /api/sessions/ses_test/fhir": () => result({ finalized: false, exportable: false }) });
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("tab", { name: "FHIR export" }));
    await user.click(screen.getByRole("button", { name: "Generate & validate" }));
    expect(await screen.findByText(/Draft · not finalized/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download JSON" })).toBeDisabled();
    expect(screen.getByText(/Finalize the review first/)).toBeInTheDocument();
  });
});

describe("diagnostics and keyboard accessibility", () => {
  it("shows concise diagnostics without secrets", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    await user.click(screen.getByRole("tab", { name: "Diagnostics" }));
    const view = await screen.findByTestId("diagnostics-view");
    await within(view).findByText("Connected");
    expect(view).toHaveTextContent("Vision model");
    expect(view).toHaveTextContent("1 detected");
    expect(view).toHaveTextContent("0.1.0");
    expect(view.textContent).not.toMatch(/tok_secret|api_key/i);
    expect(screen.getByTestId("error-list")).toHaveTextContent(/No processing errors/);
  });

  it("supports arrow-key navigation between tabs with a roving tabindex", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    const intakeTab = screen.getByRole("tab", { name: "Intake" });
    intakeTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Summary" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Summary" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Intake" })).toHaveAttribute("tabindex", "-1");
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Diagnostics" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Intake" })).toHaveFocus();
  });

  it("operates the setup form and start control with the keyboard only", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/Connected/);
    screen.getByLabelText("Session reference").focus();
    await user.tab(); // synthetic checkbox
    await user.tab(); // notes
    await user.tab(); // consent
    expect(screen.getByRole("checkbox", { name: /I have read the privacy/ })).toHaveFocus();
    await user.keyboard(" ");
    await user.tab();
    expect(screen.getByRole("button", { name: "Create intake session" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await screen.findByTestId("session-ref");
    const start = screen.getByRole("button", { name: "Start capture" });
    start.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();
  });

  it("labels every interactive control", async () => {
    setupServer();
    mockMedia();
    const user = userEvent.setup();
    await createSession(user);
    for (const b of screen.getAllByRole("button")) expect(b).toHaveAccessibleName();
    for (const t of screen.getAllByRole("textbox")) expect(t).toHaveAccessibleName();
  });
});
