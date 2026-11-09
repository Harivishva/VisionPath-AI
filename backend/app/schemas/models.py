"""Strict Pydantic schemas shared by the API, services and WebSocket events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataState(str, Enum):
    """Distinct missing-data states. They are never interchangeable."""

    NOT_MEASURED = "not_measured"
    NOT_DETECTED = "not_detected"
    UNAVAILABLE = "unavailable"
    LOW_QUALITY = "low_quality"
    PENDING_REVIEW = "pending_review"
    AVAILABLE = "available"


class Urgency(str, Enum):
    """Prototype workflow labels. NOT Emergency Severity Index (ESI) levels."""

    IMMEDIATE = "immediate_human_review"
    PROMPT = "prompt_clinical_review"
    ROUTINE = "routine_review"
    INSUFFICIENT = "insufficient_information"


URGENCY_RANK = {Urgency.INSUFFICIENT: 0, Urgency.ROUTINE: 1, Urgency.PROMPT: 2, Urgency.IMMEDIATE: 3}
URGENCY_LABEL = {
    Urgency.IMMEDIATE: "Immediate human review",
    Urgency.PROMPT: "Prompt clinical review",
    Urgency.ROUTINE: "Routine review",
    Urgency.INSUFFICIENT: "Insufficient information",
}


# ---------- Requests ----------
class SessionCreate(Strict):
    reference: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._\- ]+$")
    synthetic: bool = False
    patient_reported_notes: str = Field(default="", max_length=2000)
    consent_acknowledged: bool = False


class TranscriptCreate(Strict):
    text: str = Field(min_length=1, max_length=4000)


class TranscriptEdit(Strict):
    text: str = Field(min_length=1, max_length=4000)


class SymptomReview(Strict):
    status: Literal["confirmed", "rejected", "pending"] | None = None
    polarity: Literal["affirmed", "negated", "uncertain"] | None = None
    onset_text: str | None = Field(default=None, max_length=200)


class StaffObservationCreate(Strict):
    text: str = Field(min_length=1, max_length=1000)
    concerning: bool = False
    urgency: Urgency = Urgency.PROMPT


class SummaryEdit(Strict):
    chief_complaint: str | None = Field(default=None, max_length=300)
    clinician_notes: str | None = Field(default=None, max_length=4000)
    summary_text: str | None = Field(default=None, max_length=6000)


class ReviewRequest(Strict):
    finalize: bool = True
    reviewer: str = Field(default="", max_length=80)


class ControlRequest(Strict):
    action: Literal["start", "pause", "resume", "stop"]


class RuleConfig(Strict):
    id: str
    enabled: bool = True
    severity: Urgency


class RulesUpdate(Strict):
    rules: list[RuleConfig]


# ---------- Domain ----------
class TranscriptSegment(Strict):
    id: str = Field(default_factory=lambda: new_id("seg_"))
    text: str
    original_text: str
    source: Literal["stt-local", "stt-openai", "manual", "synthetic"]
    created_at: str = Field(default_factory=now_iso)
    t_start_s: float | None = None
    t_end_s: float | None = None
    edited: bool = False


class Onset(Strict):
    text: str
    certain: bool = True


class SymptomMention(Strict):
    id: str = Field(default_factory=lambda: new_id("sym_"))
    concept: str
    label: str
    matched_text: str
    sentence: str
    segment_id: str
    polarity: Literal["affirmed", "negated", "uncertain"]
    reporter: Literal["patient", "observer", "staff", "unspecified"] = "unspecified"
    sudden: bool = False
    severity_hint: str | None = None
    onset: Onset | None = None
    status: Literal["pending", "confirmed", "rejected"] = "pending"
    created_at: str = Field(default_factory=now_iso)
    origin: Literal["machine", "clinician"] = "machine"


class StaffObservation(Strict):
    id: str = Field(default_factory=lambda: new_id("obs_"))
    text: str
    concerning: bool
    urgency: Urgency
    created_at: str = Field(default_factory=now_iso)


class Evidence(Strict):
    source: Literal["transcript", "staff_observation", "symptom_mention"]
    ref_id: str
    text: str
    timestamp: str
    reporter: str = "unspecified"


class SafetyAlert(Strict):
    id: str
    rule_id: str
    title: str
    severity: Urgency
    explanation: str
    recommended_action: str
    evidence: list[Evidence]
    created_at: str = Field(default_factory=now_iso)
    state: Literal["active", "acknowledged", "withdrawn"] = "active"
    acknowledged_at: str | None = None
    withdrawn_reason: str | None = None
    pending_review: bool = True  # evidence from machine extraction not yet confirmed


class VisionMeasurement(Strict):
    """Experimental, quality-gated geometry. Normalised units; not clinical measurements."""

    mouth_corner_vertical_offset: float
    brow_height_offset: float
    eye_opening_offset: float


class VisionObservation(Strict):
    frame_ts: float | None = None
    processed_at: str = Field(default_factory=now_iso)
    state: DataState
    reason: str | None = None
    face_detected: bool = False
    face_confidence: float | None = None  # always None: model does not expose one
    quality: dict[str, float] = Field(default_factory=dict)
    head_pose_deg: dict[str, float] | None = None
    measurement: VisionMeasurement | None = None
    smoothed: VisionMeasurement | None = None
    overlay: dict[str, list[list[float]]] | None = None
    # Experimental. asymmetry_pct = 100 x max(|mouth offset|, |brow offset|) in inter-ocular units.
    asymmetry_pct: float | None = None
    rppg: dict[str, Any] | None = None  # experimental pulse estimate (see services/rppg.py)
    facs: dict[str, Any] | None = None  # experimental action-unit proxies + velocity (services/facs.py)
    latency_ms: float = 0.0


class ReviewState(Strict):
    chief_complaint: str = ""
    chief_complaint_suggestion: str = ""
    clinician_notes: str = ""
    summary_text: str = ""
    summary_origin: Literal["none", "deterministic", "llm_draft", "clinician"] = "none"
    finalized: bool = False
    reviewer: str = ""
    finalized_at: str | None = None


class EventEnvelope(Strict):
    event_id: str = Field(default_factory=lambda: new_id("evt_"))
    type: str
    session_id: str
    ts: str = Field(default_factory=now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)
