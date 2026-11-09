"""REST API."""
from __future__ import annotations

import platform
import sys
import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from app.config import VERSION
from app.schemas.models import (
    ControlRequest, ReviewRequest, RulesUpdate, SessionCreate, StaffObservation,
    StaffObservationCreate, SummaryEdit, SymptomReview, TranscriptCreate,
    TranscriptEdit, new_id, now_iso,
)
from app.services import clinical_summary as cs
from app.services import fhir_service, waiting_room
from app.services.session_manager import Session, SessionForbidden, SessionNotFound, TooManySessions
from app.services.unified import build_unified
from app.state import AppState

router = APIRouter(prefix="/api")

SYNTHETIC_STATEMENT = (
    "I am a synthetic demo patient. Since about an hour ago my face feels like it is drooping on one "
    "side and I suddenly have trouble getting my words out. I do not have any chest pain. "
    "I am not sure whether the weakness in my arm started this morning."
)


def app_state(request: Request) -> AppState:
    return request.app.state.vp


def get_session(session_id: str, request: Request, x_session_token: str | None = Header(default=None)) -> Session:
    st: AppState = request.app.state.vp
    try:
        return st.sessions.get(session_id, x_session_token)
    except SessionNotFound:
        raise HTTPException(404, "Session not found") from None
    except SessionForbidden:
        raise HTTPException(403, "Invalid session token") from None


def require_any_session(request: Request, x_session_token: str | None = Header(default=None)) -> None:
    st: AppState = request.app.state.vp
    import hmac

    if not x_session_token or not any(
        hmac.compare_digest(x_session_token, s.token) for s in st.sessions._sessions.values()
    ):
        raise HTTPException(403, "A valid session token is required")


@router.get("/health")
def health():
    return {"status": "ok", "version": VERSION, "time": now_iso()}


@router.get("/diagnostics")
def diagnostics(st: AppState = Depends(app_state)):
    return {
        "version": VERSION,
        "python": platform.python_version(),
        "platform": sys.platform,
        "vision": {"ready": st.vision.ready, "error": st.vision.error, "model": "MediaPipe FaceLandmarker"},
        "transcription": st.stt.status(),
        "llm": st.llm.status(),
        "active_sessions": st.sessions.count(),
        "raw_media_persistence": st.settings.persist_raw_media,
        "limits": {"max_frame_bytes": st.settings.max_frame_bytes,
                   "max_audio_chunk_bytes": st.settings.max_audio_chunk_bytes},
        "environment": st.settings.env,
    }


@router.post("/sessions", status_code=201)
def create_session(req: SessionCreate, st: AppState = Depends(app_state)):
    if not req.consent_acknowledged:
        raise HTTPException(422, "Privacy/consent acknowledgement is required to start a session")
    try:
        s = st.sessions.create(req)
    except TooManySessions as exc:
        raise HTTPException(429, str(exc)) from None
    if req.patient_reported_notes.strip():
        seg, new = s.add_segment(req.patient_reported_notes.strip(), "manual")
        st.commit(s, new, seg)
    return {"session_id": s.id, "token": s.token, "session": st.snapshot(s)}


@router.get("/sessions/{session_id}")
def read_session(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    return st.snapshot(s)


@router.get("/sessions/{session_id}/unified")
def read_unified(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    """Master unified metrics document (audio status, vision, acoustic DSP, guardrail, XAI)."""
    st.refresh_guardrail(s)
    return build_unified(s, st.settings)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    st.fhir_cache.pop(session_id, None)
    await st.sessions.delete(session_id)
    return {"deleted": True, "media_released": True}


@router.post("/sessions/{session_id}/control")
def control(req: ControlRequest, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    try:
        st.control(s, req.action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return st.snapshot(s)


# ---------------- transcript ----------------
@router.post("/sessions/{session_id}/transcript", status_code=201)
def add_transcript(req: TranscriptCreate, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    seg, new = s.add_segment(req.text.strip(), "manual")
    st.commit(s, new, seg)
    return st.snapshot(s)


@router.post("/sessions/{session_id}/synthetic-transcript", status_code=201)
def add_synthetic(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    if not s.synthetic:
        raise HTTPException(409, "Synthetic content can only be loaded into a session created as synthetic/demo")
    seg, new = s.add_segment(SYNTHETIC_STATEMENT, "synthetic")
    st.commit(s, new, seg)
    return st.snapshot(s)


@router.patch("/sessions/{session_id}/transcript/{segment_id}")
def edit_transcript(segment_id: str, req: TranscriptEdit, s: Session = Depends(get_session),
                    st: AppState = Depends(app_state)):
    try:
        s.edit_segment(segment_id, req.text.strip())
    except KeyError:
        raise HTTPException(404, "Transcript segment not found") from None
    st.commit(s)
    return st.snapshot(s)


# ---------------- symptoms / observations / alerts ----------------
@router.patch("/sessions/{session_id}/symptoms/{mention_id}")
def review_symptom(mention_id: str, req: SymptomReview, s: Session = Depends(get_session),
                   st: AppState = Depends(app_state)):
    m = next((x for x in s.mentions if x.id == mention_id), None)
    if not m:
        raise HTTPException(404, "Symptom mention not found")
    if req.status:
        m.status = req.status
    if req.polarity:
        m.polarity = req.polarity
    if req.onset_text is not None:
        from app.schemas.models import Onset

        m.onset = Onset(text=req.onset_text, certain=True) if req.onset_text.strip() else None
    s._refresh_suggestion()
    st.commit(s)
    return st.snapshot(s)


@router.post("/sessions/{session_id}/observations", status_code=201)
def add_observation(req: StaffObservationCreate, s: Session = Depends(get_session),
                    st: AppState = Depends(app_state)):
    s.observations.append(StaffObservation(text=req.text.strip(), concerning=req.concerning, urgency=req.urgency))
    st.commit(s)
    return st.snapshot(s)


@router.post("/sessions/{session_id}/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    a = next((x for x in s.alerts if x.id == alert_id), None)
    if not a:
        raise HTTPException(404, "Alert not found")
    if a.state == "active":
        a.state, a.acknowledged_at = "acknowledged", now_iso()
    s.publish("session.snapshot", st.snapshot(s))
    return st.snapshot(s)


# ---------------- rules ----------------
@router.get("/rules")
def get_rules(st: AppState = Depends(app_state)):
    return {"rules": st.rules.config(),
            "note": "Prototype workflow rules. Labels are not ESI levels and not triage decisions."}


@router.put("/rules", dependencies=[Depends(require_any_session)])
def put_rules(req: RulesUpdate, st: AppState = Depends(app_state)):
    try:
        st.rules.update(req.rules)
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0])) from None
    return {"rules": st.rules.config()}


# ---------------- summary / review ----------------
@router.get("/sessions/{session_id}/summary")
def get_summary(s: Session = Depends(get_session)):
    return {"sections": cs.build_sections(s), "text": s.review.summary_text,
            "origin": s.review.summary_origin}


@router.post("/sessions/{session_id}/summary/draft")
async def draft_summary(mode: str = "deterministic", s: Session = Depends(get_session),
                        st: AppState = Depends(app_state)):
    if s.review.finalized:
        raise HTTPException(409, "Review is finalized; reopen it to regenerate the draft")
    notice = None
    text = cs.deterministic_text(s)
    origin = "deterministic"
    if mode == "llm":
        try:
            d = await st.llm.draft(cs.build_sections(s))
            text = (cs.deterministic_text(s) + "\n\nLLM-drafted narrative (review required):\n" + d.narrative)
            origin = "llm_draft"
        except (cs.LLMUnavailable, cs.LLMInvalid) as exc:
            notice = f"LLM draft unavailable ({exc}); deterministic summary used instead"
    s.review.summary_text, s.review.summary_origin = text, origin  # type: ignore[assignment]
    s.publish("clinical.summary", {"origin": origin})
    s.publish("session.snapshot", st.snapshot(s))
    return {"text": text, "origin": origin, "notice": notice}


@router.put("/sessions/{session_id}/summary")
def edit_summary(req: SummaryEdit, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    if s.review.finalized:
        raise HTTPException(409, "Review is finalized; reopen it to edit")
    if req.chief_complaint is not None:
        s.review.chief_complaint = req.chief_complaint.strip()
    if req.clinician_notes is not None:
        s.review.clinician_notes = req.clinician_notes
    if req.summary_text is not None:
        s.review.summary_text = req.summary_text
        s.review.summary_origin = "clinician"
    st.fhir_cache.pop(s.id, None)
    st.commit(s)
    return st.snapshot(s)


@router.post("/sessions/{session_id}/review")
def review(req: ReviewRequest, s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    if req.finalize:
        pending = [m for m in s.mentions if m.status == "pending"]
        if pending:
            raise HTTPException(409, f"Confirm or reject {len(pending)} pending symptom mention(s) before finalizing")
        s.review.finalized, s.review.finalized_at = True, now_iso()
        s.review.reviewer = req.reviewer
    else:
        s.review.finalized, s.review.finalized_at = False, None
    st.fhir_cache.pop(s.id, None)
    s.publish("session.snapshot", st.snapshot(s))
    return st.snapshot(s)


# ---------------- FHIR ----------------
def _fhir(s: Session, st: AppState) -> dict:
    bundle, notes = fhir_service.build_bundle(s)
    validation = fhir_service.validate_bundle(bundle)
    res = {"bundle": bundle, "validation": validation, "notes": notes, "finalized": s.review.finalized,
           "exportable": bool(validation["valid"] and s.review.finalized), "generated_at": now_iso()}
    st.fhir_cache[s.id] = res
    s.publish("fhir.validation", {"valid": validation["valid"], "errors": len(validation["errors"]),
                                  "resource_counts": validation["resource_counts"]})
    return res


@router.post("/sessions/{session_id}/fhir")
def generate_fhir(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    return _fhir(s, st)


@router.post("/ehr/sync", status_code=201, dependencies=[Depends(require_any_session)])
def ehr_sync(bundle: dict = Body(...)):
    """SIMULATED EHR hand-off: validates the FHIR Bundle and acknowledges it. Nothing leaves this server
    and nothing is stored; no Epic/Cerner system is contacted."""
    validation = fhir_service.validate_bundle(bundle)
    if not validation["valid"]:
        raise HTTPException(422, {"message": "FHIR validation failed; nothing was transmitted",
                                  "errors": validation["errors"][:25]})
    return {
        "status": "created", "simulated": True, "transmission_id": new_id("ehr_"), "received_at": now_iso(),
        "target": "simulated-ehr (no external system was contacted)",
        "resource_counts": validation["resource_counts"],
        "disclaimer": "Simulation only. " + validation["disclaimer"],
    }


# ---------------- waiting-room monitor ----------------
@router.get("/sessions/{session_id}/waiting-room")
def waiting_room_state(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    return waiting_room.state(s.monitor, time.monotonic(), st.settings)


@router.post("/sessions/{session_id}/waiting-room/start")
def waiting_room_start(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    if s.status not in ("live", "paused"):
        raise HTTPException(409, "Waiting-room monitoring needs a live session (camera data)")
    waiting_room.start(s.monitor, time.monotonic())
    st.refresh_guardrail(s, force=True)
    return waiting_room.state(s.monitor, time.monotonic(), st.settings)


@router.post("/sessions/{session_id}/waiting-room/stop")
def waiting_room_stop(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    waiting_room.stop(s.monitor)
    st.refresh_guardrail(s, force=True)
    return waiting_room.state(s.monitor, time.monotonic(), st.settings)


@router.post("/sessions/{session_id}/waiting-room/ack")
def waiting_room_ack(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    waiting_room.acknowledge(s.monitor)
    st.refresh_guardrail(s, force=True)
    return waiting_room.state(s.monitor, time.monotonic(), st.settings)


@router.get("/sessions/{session_id}/fhir")
def read_fhir(s: Session = Depends(get_session), st: AppState = Depends(app_state)):
    res = st.fhir_cache.get(s.id)
    if not res:
        raise HTTPException(404, "No FHIR bundle generated yet for this session")
    return res
