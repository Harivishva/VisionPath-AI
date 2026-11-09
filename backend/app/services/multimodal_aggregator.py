"""Combines vision, audio, transcript and staff data into one honest session snapshot.

Missing data states are kept distinct (not_measured / not_detected / unavailable / low_quality /
pending_review) and are never converted into normal findings.
"""
from __future__ import annotations

from app.schemas.models import DataState
from app.services.safety_rules import overall_indicator
from app.services.session_manager import Session
from app.services.unified import build_unified


def vision_modality(s: Session, vision_ready: bool, vision_error: str | None) -> dict:
    summ = s.vision.summary()
    state, reason = summ["state"], None
    if not vision_ready:
        state, reason = DataState.UNAVAILABLE.value, f"Vision model not ready: {vision_error}"
    elif s.last_vision is not None and s.last_vision.state in (
            DataState.LOW_QUALITY, DataState.NOT_DETECTED, DataState.UNAVAILABLE) and summ["accepted_frames"] == 0:
        state, reason = s.last_vision.state.value, s.last_vision.reason
    elif summ["state"] == DataState.NOT_MEASURED.value:
        reason = "No camera frames processed in this session"
    return {"state": state, "reason": reason, **summ, "experimental": True}


def audio_modality(s: Session) -> dict:
    summ = s.audio.summary() if s.audio else {"state": DataState.NOT_MEASURED.value}
    reason = None
    if summ["state"] == DataState.NOT_MEASURED.value:
        reason = "No microphone audio processed in this session"
    elif summ["state"] == DataState.NOT_DETECTED.value:
        reason = "Audio received but no speech segment detected"
    return {**summ, "reason": reason}


def transcript_modality(s: Session) -> dict:
    if not s.segments:
        state = DataState.NOT_MEASURED.value
    elif any(m.status == "pending" for m in s.mentions):
        state = DataState.PENDING_REVIEW.value
    else:
        state = DataState.AVAILABLE.value
    return {"state": state, "segments": len(s.segments), "transcription_state": s.transcription_state,
            "transcription_detail": s.transcription_detail}


def missing_information(s: Session) -> list[str]:
    out: list[str] = []
    if not s.review.chief_complaint:
        out.append("Chief complaint not confirmed by staff")
    for m in s.mentions:
        if m.status == "rejected" or m.polarity == "negated":
            continue
        if m.polarity == "uncertain":
            out.append(f"{m.label}: mention was uncertain; verify with patient/observer")
        elif m.onset is None:
            out.append(f"{m.label}: onset/duration not stated")
        elif not m.onset.certain:
            out.append(f"{m.label}: onset stated with uncertainty (\"{m.onset.text}\")")
    out.append("Vital signs, age, sex, medications and history are not collected by this prototype")
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def snapshot(s: Session, engine, vision_ready: bool, vision_error: str | None,
             stt_status: dict, llm_status: dict) -> dict:
    return {
        "id": s.id,
        "reference": s.reference,
        "synthetic": s.synthetic,
        "created_at": s.created_at,
        "status": s.status,
        "patient_reported_notes": s.patient_reported_notes,
        "segments": [x.model_dump(mode="json") for x in s.segments],
        "mentions": [x.model_dump(mode="json") for x in s.mentions],
        "observations": [x.model_dump(mode="json") for x in s.observations],
        "alerts": [x.model_dump(mode="json") for x in s.alerts],
        "review": s.review.model_dump(mode="json"),
        "indicator": overall_indicator(s.alerts, s.has_clinical_data()),
        "modalities": {
            "vision": vision_modality(s, vision_ready, vision_error),
            "audio": audio_modality(s),
            "transcript": transcript_modality(s),
        },
        "unified": build_unified(s, s.settings),
        "missing_information": missing_information(s),
        "providers": {"transcription": stt_status, "llm": llm_status},
        "errors": s.errors[-10:],
        "privacy": {"raw_media_persisted": s.raw_media_persisted, "media_released": s.media_stopped},
    }
