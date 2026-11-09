"""Master unified metrics document (audio status, vision, acoustic DSP, speech, guardrail, XAI)."""
from __future__ import annotations

from app.config import Settings
from app.schemas.models import now_iso
import time

from app.services import guardrails, vision_findings, waiting_room
from app.services.safety_rules import overall_indicator
from app.services.session_manager import Session


def evaluate_guardrail(s: Session, settings: Settings) -> dict:
    """Run the deterministic guardrails on FRESH metrics only and remember the result."""
    g = guardrails.evaluate(s.fresh_metrics(), settings)
    if g["hard_override_triggered"] and s.guardrail_first_at is None:
        s.guardrail_first_at = now_iso()
    g = {**g, "first_triggered_at": s.guardrail_first_at, "evaluated_at": now_iso()}
    s.guardrail = g
    return g


def build_unified(s: Session, settings: Settings) -> dict:
    m = s.fresh_metrics()
    g = s.guardrail or evaluate_guardrail(s, settings)
    indicator = overall_indicator(s.alerts, s.has_clinical_data())
    last_seg = s.segments[-1].original_text if s.segments else None
    speech = s.speech_summary()
    facs = (s.vision.facs.current() if s.vision else None) or {}
    facs_on = facs.get("state") == "pending_review"
    wr = waiting_room.state(s.monitor, time.monotonic(), settings)
    a = s.last_audio_status or {}
    dsp = s.audio.last_dsp if s.audio else None
    rppg = s.vision.rppg.current()
    return {
        "timestamp": now_iso(),
        "patient_session_id": s.id,
        "audio_status": {
            "snr_db": a.get("snr_db"),
            "noise_filter_applied": bool(a.get("noise_filter_applied", False)),
            "audio_quality": a.get("audio_quality", "not_measured"),
        },
        "extracted_metrics": {
            "vision": {
                "facial_asymmetry_pct": m["asymmetry_pct"],
                "rppg_heart_rate_bpm": m["hr_bpm"],
                "rppg_confidence": m["hr_confidence"],
                "rppg_state": (rppg or {}).get("state", "not_measured"),
                "rppg_reason": (rppg or {}).get("reason"),
                # Experimental geometric proxies relative to a per-session baseline; null until calibrated.
                "facs_action_units": {
                    "au04_distress": m["au04_distress"] if facs_on else None,
                    "au09_pain": m["au09_pain"] if facs_on else None,
                    "au12_14_palsy_velocity": m["au12_14_palsy_velocity"] if facs_on else None,
                },
                "facs_state": facs.get("state", "not_measured"),
                "facs_reason": facs.get("reason"),
            },
            "audio_dsp": {
                "gasping_detected": m["gasping_detected"],
                "speech_to_breath_ratio": m["speech_to_breath_ratio"],
                "vocal_jitter": m["vocal_jitter"],
                "pitch_instability_hz": m["pitch_instability_hz"],
                "state": (dsp or {}).get("state", "not_measured"),
                "reason": (dsp or {}).get("reason"),
            },
            "speech": {
                "raw_transcript": last_seg,
                "detected_languages": speech["detected_languages"],
                "normalized_clinical_symptoms": speech["normalized_clinical_symptoms"],
                # No dysarthria model exists in this prototype: reported as not measured, never invented.
                "dysarthria_confidence": None,
            },
        },
        "guardrail_status": {
            "hard_override_triggered": g["hard_override_triggered"],
            "triggered_rule": g["triggered_rule"],
            "forced_esi_score": g["forced_esi_score"],
            "triggered_rules": g["triggered_rules"],
            "first_triggered_at": g.get("first_triggered_at"),
            "disclaimer": g["disclaimer"],
        },
        "priority": guardrails.priority(g, indicator),
        "visual_findings": vision_findings.derive(m, facs, settings),
        "waiting_room_monitoring": {
            "is_monitored": wr["is_monitored"],
            "acuity_escalation_alert": wr["acuity_escalation_alert"],
            "baseline_hr_delta": wr["baseline_hr_delta"],
            "phase": wr["phase"],
            "reasons": wr["reasons"],
            "alert_at": wr["alert_at"],
        },
        "xai_attributions": guardrails.attributions(m, indicator, settings),
        # True once staff have finalized the review, i.e. the FHIR export is no longer 'preliminary'.
        "fhir_bundle_ready": bool(s.review.finalized and s.has_clinical_data()),
    }
