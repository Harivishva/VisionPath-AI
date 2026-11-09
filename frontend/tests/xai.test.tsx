import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { XaiPanel } from "@/components/XaiPanel";
import { AcuityBanner } from "@/components/AcuityBanner";
import type { Intake } from "@/lib/useIntake";
import type { UnifiedMetrics } from "@/lib/types";

const idle: UnifiedMetrics = {
  timestamp: "2026-11-04T18:00:00Z",
  audio_status: { snr_db: null, noise_filter_applied: false, audio_quality: "not_measured" },
  extracted_metrics: {
    vision: { facial_asymmetry_pct: null, rppg_heart_rate_bpm: null, rppg_confidence: null, rppg_state: "not_measured", rppg_reason: null, facs_action_units: { au04_distress: null, au09_pain: null, au12_14_palsy_velocity: null }, facs_state: "not_measured", facs_reason: null },
    audio_dsp: { gasping_detected: null, speech_to_breath_ratio: null, vocal_jitter: null, pitch_instability_hz: null, state: "not_measured", reason: null },
    speech: { raw_transcript: null, detected_languages: [], normalized_clinical_symptoms: [], dysarthria_confidence: null },
  },
  guardrail_status: { hard_override_triggered: false, triggered_rule: null, forced_esi_score: null, triggered_rules: [], first_triggered_at: null, disclaimer: "Prototype ESI-style priority. Not an official ESI assessment." },
  priority: { level: null, source: "none" },
  patient_session_id: "ses_x",
  waiting_room_monitoring: { is_monitored: false, acuity_escalation_alert: false, baseline_hr_delta: null, phase: "off", reasons: [], alert_at: null },
  fhir_bundle_ready: false,
  visual_findings: [],
  xai_attributions: { asymmetry_weight_pct: null, heart_rate_weight_pct: null, gasping_weight_pct: null, transcript_weight_pct: null, method: "weighted_signal_share" },
};

const active: UnifiedMetrics = {
  ...idle,
  extracted_metrics: {
    vision: { facial_asymmetry_pct: 11.2, rppg_heart_rate_bpm: 134, rppg_confidence: 0.88, rppg_state: "pending_review", rppg_reason: null, facs_action_units: { au04_distress: 0.82, au09_pain: 0.74, au12_14_palsy_velocity: 0.045 }, facs_state: "pending_review", facs_reason: null },
    audio_dsp: { gasping_detected: true, speech_to_breath_ratio: 0.32, vocal_jitter: 0.045, pitch_instability_hz: 18.2, state: "pending_review", reason: null },
    speech: { raw_transcript: "x", detected_languages: ["ta", "en"], normalized_clinical_symptoms: ["Substernal Chest Pressure", "Acute Dyspnea"], dysarthria_confidence: null },
  },
  guardrail_status: {
    hard_override_triggered: true, triggered_rule: "ESI_LEVEL_2_TACHYCARDIA_AND_GASPING_OVERRIDE", forced_esi_score: 2,
    triggered_rules: [
      { id: "ESI_LEVEL_2_TACHYCARDIA", name: "TACHYCARDIA", level: 2, evidence: "rPPG heart rate 134 BPM > 130" },
      { id: "ESI_LEVEL_2_GASPING_LOW_SPEECH_RATIO", name: "GASPING", level: 2, evidence: "bursts and ratio 0.32 < 0.40" },
    ],
    first_triggered_at: "2026-11-04T18:00:00Z", disclaimer: "Prototype ESI-style priority. Not an official ESI assessment.",
  },
  priority: { level: 2, source: "guardrail" },
  visual_findings: [{ signal: "asymmetry", label: "Marked facial asymmetry", value: 11.2, unit: "%", evidence: "Mouth/brow offset 11.2%", level: "alert", experimental: true }],
  waiting_room_monitoring: { is_monitored: true, acuity_escalation_alert: true, baseline_hr_delta: "+31 BPM", phase: "monitoring", reasons: ["Heart rate rose 31 BPM above baseline (80 -> 111)"], alert_at: "2026-11-04T18:05:00Z" },
  xai_attributions: { asymmetry_weight_pct: 20, heart_rate_weight_pct: 25, gasping_weight_pct: 45, transcript_weight_pct: 10, method: "weighted_signal_share" },
};

const intake = (unified: UnifiedMetrics | null, extra = {}) =>
  ({ unified, dsp: null, audioQuality: null, vision: null, ...extra }) as unknown as Intake;

describe("XaiPanel", () => {
  it("shows the active override, the forced level and the rule evidence", () => {
    render(<XaiPanel intake={intake(active)} />);
    expect(screen.getByTestId("guardrail-status")).toHaveTextContent("Guardrail Status: ACTIVE OVERRIDE");
    expect(screen.getByTestId("guardrail-status")).toHaveTextContent("rPPG heart rate 134 BPM > 130");
    expect(screen.getByTestId("priority-badge")).toHaveTextContent("Level 2");
    expect(screen.getByTestId("priority-badge")).toHaveTextContent("Forced by a deterministic guardrail");
  });

  it("renders the attribution bars with their percentages", () => {
    render(<XaiPanel intake={intake(active)} />);
    const bars = within(screen.getByTestId("attributions"));
    expect(bars.getByRole("meter", { name: "Acoustic gasping contribution" })).toHaveAttribute("aria-valuenow", "45");
    expect(bars.getByText("45%")).toBeInTheDocument();
    expect(bars.getByText("20%")).toBeInTheDocument();
  });

  it("does not show an override or a level when nothing is known (missing is not normal)", () => {
    render(<XaiPanel intake={intake(idle)} />);
    expect(screen.getByTestId("guardrail-status")).toHaveTextContent("monitoring");
    expect(screen.getByTestId("guardrail-status")).not.toHaveTextContent("ACTIVE OVERRIDE");
    expect(screen.getByTestId("priority-badge")).toHaveTextContent("No level");
    expect(screen.getByTestId("attributions")).toHaveTextContent("No signal is contributing");
    expect(within(screen.getByTestId("xai-metrics")).getAllByText("—").length).toBeGreaterThan(3);
  });

  it("renders before the first backend event and explains a too-quiet microphone", () => {
    render(<XaiPanel intake={intake(null, { audioQuality: { rms: 0.003, snr_db: null, noise_filter_applied: false, noise_filter_error: null, audio_quality: "too_quiet" } })} />);
    expect(screen.getByTestId("xai-status-lines")).toHaveTextContent("Nothing is transcribed unless a voice is clearly present");
  });

  it("lists visual findings with their evidence, and says an empty list is not a clearance", () => {
    const { unmount } = render(<XaiPanel intake={intake(active)} />);
    expect(screen.getByTestId("visual-findings")).toHaveTextContent("Marked facial asymmetry");
    expect(screen.getByTestId("visual-findings")).toHaveTextContent("Mouth/brow offset 11.2%");
    unmount();
    render(<XaiPanel intake={intake(idle)} />);
    expect(screen.getByTestId("visual-findings")).toHaveTextContent("not a clearance");
  });

  it("shows FACS proxies, languages and normalized symptoms, and the monitor state", () => {
    render(<XaiPanel intake={intake(active)} />);
    const grid = within(screen.getByTestId("xai-metrics"));
    expect(grid.getByText("0.82")).toBeInTheDocument();
    expect(grid.getByText("0.74")).toBeInTheDocument();
    expect(screen.getByTestId("speech-normalized")).toHaveTextContent("ta, en");
    expect(screen.getByTestId("speech-normalized")).toHaveTextContent("Substernal Chest Pressure; Acute Dyspnea");
    expect(screen.getByTestId("waiting-room")).toHaveTextContent("Stop waiting-room monitor");
    expect(screen.getByTestId("waiting-room")).toHaveTextContent("+31 BPM");
  });

  it("flashes the re-triage banner only while an escalation alert is active", () => {
    const sess = { id: "ses_x" };
    const { rerender } = render(<AcuityBanner intake={intake(active, { session: sess, token: "t", guard: async () => null })} />);
    expect(screen.getByTestId("acuity-banner")).toHaveTextContent("Heart rate rose 31 BPM");
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeInTheDocument();
    rerender(<AcuityBanner intake={intake(idle, { session: sess, token: "t", guard: async () => null })} />);
    expect(screen.queryByTestId("acuity-banner")).toBeNull();
  });
});
