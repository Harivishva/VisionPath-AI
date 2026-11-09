export type DataState =
  | "not_measured"
  | "not_detected"
  | "unavailable"
  | "low_quality"
  | "pending_review"
  | "available";

export type Urgency =
  | "immediate_human_review"
  | "prompt_clinical_review"
  | "routine_review"
  | "insufficient_information";

export const URGENCY_LABEL: Record<Urgency, string> = {
  immediate_human_review: "Immediate human review",
  prompt_clinical_review: "Prompt clinical review",
  routine_review: "Routine review",
  insufficient_information: "Insufficient information",
};

export const STATE_LABEL: Record<DataState, string> = {
  not_measured: "Not measured",
  not_detected: "Not detected",
  unavailable: "Unavailable",
  low_quality: "Low quality",
  pending_review: "Pending review",
  available: "Available",
};

export interface Segment {
  id: string;
  text: string;
  original_text: string;
  source: "stt-local" | "stt-openai" | "manual" | "synthetic";
  created_at: string;
  t_start_s: number | null;
  t_end_s: number | null;
  edited: boolean;
}

export interface Mention {
  id: string;
  concept: string;
  label: string;
  matched_text: string;
  sentence: string;
  segment_id: string;
  polarity: "affirmed" | "negated" | "uncertain";
  reporter: "patient" | "observer" | "staff" | "unspecified";
  sudden: boolean;
  onset: { text: string; certain: boolean } | null;
  status: "pending" | "confirmed" | "rejected";
}

export interface Evidence {
  source: string;
  ref_id: string;
  text: string;
  timestamp: string;
  reporter: string;
}

export interface Alert {
  id: string;
  rule_id: string;
  title: string;
  severity: Urgency;
  explanation: string;
  recommended_action: string;
  evidence: Evidence[];
  created_at: string;
  state: "active" | "acknowledged" | "withdrawn";
  acknowledged_at: string | null;
  withdrawn_reason: string | null;
  pending_review: boolean;
}

export interface StaffObservation {
  id: string;
  text: string;
  concerning: boolean;
  urgency: Urgency;
  created_at: string;
}

export interface VisionModality {
  state: DataState;
  reason: string | null;
  frames_received: number;
  frames_processed: number;
  frames_dropped: number;
  face_frames: number;
  accepted_frames: number;
  low_quality_frames: number;
  fps: number;
  mean_latency_ms: number;
  mean_measurement: Measurement | null;
}

export interface Measurement {
  mouth_corner_vertical_offset: number;
  brow_height_offset: number;
  eye_opening_offset: number;
}

export interface AudioModality {
  state: DataState;
  reason: string | null;
  chunks_received: number;
  chunks_dropped: number;
  speech_segments: number;
  speech_seconds: number;
  clipped_chunks: number;
  low_volume_chunks: number;
}

export interface ProviderStatus {
  provider: string;
  ready: boolean;
  detail: string;
  sends_audio_externally?: boolean;
  sends_data_externally?: boolean;
  model?: string | null;
  last_error?: string | null;
}

export interface SessionErrorItem {
  id: string;
  ts: string;
  source: string;
  message: string;
  recoverable: boolean;
}

export interface SessionSnapshot {
  id: string;
  reference: string;
  synthetic: boolean;
  created_at: string;
  status: "setup" | "live" | "paused" | "stopped";
  segments: Segment[];
  mentions: Mention[];
  observations: StaffObservation[];
  alerts: Alert[];
  review: {
    chief_complaint: string;
    chief_complaint_suggestion: string;
    clinician_notes: string;
    summary_text: string;
    summary_origin: string;
    finalized: boolean;
    reviewer: string;
    finalized_at: string | null;
  };
  indicator: { level: Urgency; label: string; basis: string; disclaimer: string };
  modalities: {
    vision: VisionModality;
    audio: AudioModality;
    transcript: { state: DataState; segments: number; transcription_state: string; transcription_detail: string | null };
  };
  missing_information: string[];
  providers: { transcription: ProviderStatus; llm: ProviderStatus };
  errors: SessionErrorItem[];
  privacy: { raw_media_persisted: boolean; media_released: boolean };
  unified?: UnifiedMetrics;
}

export interface VisionObservation {
  state: DataState;
  reason: string | null;
  face_detected: boolean;
  face_confidence: number | null;
  quality: Record<string, number>;
  head_pose_deg: { yaw: number; pitch: number; roll: number } | null;
  measurement: Measurement | null;
  smoothed: Measurement | null;
  overlay: Record<string, number[][]> | null;
  asymmetry_pct?: number | null;
  rppg?: Rppg | null;
  facs?: Record<string, unknown> | null;
  latency_ms: number;
  processed_at: string;
}

export interface AudioFeature {
  rms_dbfs: number;
  voice_activity: boolean;
  f0_hz: number | null;
  f0_periodicity: number;
  spectral_centroid_hz?: number;
  zero_crossing_rate: number;
  clipping_fraction: number;
  quality: DataState;
  quality_reason: string | null;
}

export interface EventEnvelope<T = unknown> {
  event_id: string;
  type: string;
  session_id: string;
  ts: string;
  payload: T;
}

export interface FhirResult {
  bundle: Record<string, unknown>;
  validation: {
    valid: boolean;
    errors: { resource: string; path: string; message: string }[];
    resource_counts: Record<string, number>;
    references_checked: number;
    standard: string;
    disclaimer: string;
  };
  notes: string[];
  finalized: boolean;
  exportable: boolean;
  generated_at: string;
}

export interface Diagnostics {
  version: string;
  python: string;
  vision: { ready: boolean; error: string | null; model: string };
  transcription: ProviderStatus;
  llm: ProviderStatus;
  active_sessions: number;
  raw_media_persistence: boolean;
  limits: Record<string, number>;
  environment: string;
}

export interface Rppg {
  state: DataState;
  heart_rate_bpm: number | null;
  confidence: number | null;
  window_s: number;
  effective_fps: number | null;
  reason: string | null;
}

export interface AudioQuality {
  rms: number;
  snr_db: number | null;
  noise_filter_applied: boolean;
  noise_filter_error: string | null;
  audio_quality: "good" | "noisy" | "too_quiet" | "no_speech";
}

export interface DspMetrics {
  state: DataState;
  reason: string | null;
  speech_to_breath_ratio: number | null;
  vocal_jitter: number | null;
  gasping_detected: boolean | null;
  breath_events: number | null;
}

export interface GuardrailRule {
  id: string;
  name: string;
  level: number;
  evidence: string;
}

export interface UnifiedMetrics {
  timestamp: string;
  audio_status: { snr_db: number | null; noise_filter_applied: boolean; audio_quality: string };
  extracted_metrics: {
    vision: {
      facial_asymmetry_pct: number | null;
      rppg_heart_rate_bpm: number | null;
      rppg_confidence: number | null;
      rppg_state: DataState;
      rppg_reason: string | null;
      facs_action_units: { au04_distress: number | null; au09_pain: number | null; au12_14_palsy_velocity: number | null };
      facs_state: DataState | "calibrating";
      facs_reason: string | null;
    };
    audio_dsp: {
      gasping_detected: boolean | null;
      speech_to_breath_ratio: number | null;
      vocal_jitter: number | null;
      pitch_instability_hz: number | null;
      state: DataState;
      reason: string | null;
    };
    speech: {
      raw_transcript: string | null;
      detected_languages: string[];
      normalized_clinical_symptoms: string[];
      dysarthria_confidence: number | null;
    };
  };
  patient_session_id: string;
  waiting_room_monitoring: {
    is_monitored: boolean;
    acuity_escalation_alert: boolean;
    baseline_hr_delta: string | null;
    phase: "off" | "calibrating" | "monitoring";
    reasons: string[];
    alert_at: string | null;
  };
  fhir_bundle_ready: boolean;
  visual_findings: VisualFinding[];
  guardrail_status: {
    hard_override_triggered: boolean;
    triggered_rule: string | null;
    forced_esi_score: number | null;
    triggered_rules: GuardrailRule[];
    first_triggered_at: string | null;
    disclaimer: string;
  };
  priority: { level: number | null; source: "guardrail" | "text_rule" | "none" };
  xai_attributions: {
    asymmetry_weight_pct: number | null;
    heart_rate_weight_pct: number | null;
    gasping_weight_pct: number | null;
    transcript_weight_pct: number | null;
    method: string;
  };
}

export interface EhrSyncResult {
  status: string;
  simulated: boolean;
  transmission_id: string;
  received_at: string;
  target: string;
  resource_counts: Record<string, number>;
  disclaimer: string;
}

export interface VisualFinding {
  signal: string;
  label: string;
  value: number;
  unit: string;
  evidence: string;
  level: "notice" | "alert";
  experimental: boolean;
}
