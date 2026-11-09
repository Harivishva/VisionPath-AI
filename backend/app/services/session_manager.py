"""In-memory session store (no database: nothing about a patient touches disk)."""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.schemas.models import (
    EventEnvelope, ReviewState, SafetyAlert, SessionCreate, StaffObservation, SymptomMention,
    TranscriptSegment, VisionObservation, now_iso, new_id,
)
from app.services import clinical_terms, waiting_room
from app.services.audio_processor import AudioSessionState
from app.services.safety_rules import RuleEngine, merge_alerts
from app.services import symptom_extractor
from app.services.vision_processor import VisionSessionState

log = logging.getLogger("visionpath.session")


class SessionNotFound(KeyError):
    pass


class SessionForbidden(PermissionError):
    pass


class TooManySessions(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    token: str
    reference: str
    synthetic: bool
    patient_reported_notes: str
    settings: Settings
    created_at: str = field(default_factory=now_iso)
    status: str = "setup"  # setup | live | paused | stopped
    segments: list[TranscriptSegment] = field(default_factory=list)
    mentions: list[SymptomMention] = field(default_factory=list)
    observations: list[StaffObservation] = field(default_factory=list)
    alerts: list[SafetyAlert] = field(default_factory=list)
    review: ReviewState = field(default_factory=ReviewState)
    vision: VisionSessionState = field(default_factory=VisionSessionState)
    audio: AudioSessionState | None = None
    last_vision: VisionObservation | None = None
    errors: list[dict] = field(default_factory=list)
    transcription_state: str = "idle"  # idle | transcribing | unavailable | error
    transcription_detail: str | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    tasks: set[asyncio.Task] = field(default_factory=set)
    media_stopped: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    media_owner: object | None = None  # the single WebSocket allowed to upload media
    utt_queue: asyncio.Queue | None = None  # bounded queue of utterances awaiting transcription
    raw_media_persisted: bool = False  # always False unless explicitly configured
    last_audio_status: dict | None = None  # pre-STT RMS / SNR / quality of the latest utterance
    metrics: dict[str, tuple[Any, float]] = field(default_factory=dict)  # name -> (value, monotonic time)
    guardrail: dict | None = None
    guardrail_first_at: str | None = None
    last_unified_pub: float = 0.0
    last_known: dict[str, tuple[Any, str]] = field(default_factory=dict)  # derived values kept for export
    seg_meta: dict[str, dict] = field(default_factory=dict)  # segment id -> {"model_language", "languages", "symptoms"}
    monitor: waiting_room.MonitorState = field(default_factory=waiting_room.MonitorState)

    # ---------------- guardrail inputs ----------------
    def note_metric(self, name: str, value: Any) -> None:
        if value is not None:
            self.metrics[name] = (value, time.monotonic())
            self.last_known[name] = (value, now_iso())

    def note_vision(self, obs: VisionObservation) -> None:
        self.note_metric("asymmetry_pct", obs.asymmetry_pct)
        r = obs.rppg or {}
        if r.get("heart_rate_bpm") is not None:
            self.note_metric("hr_bpm", r["heart_rate_bpm"])
            self.note_metric("hr_confidence", r.get("confidence"))
        f = obs.facs or {}
        if f.get("state") == "pending_review":
            for k in ("au04_distress", "au09_pain", "au12_14_palsy_velocity"):
                self.note_metric(k, f.get(k))

    def note_dsp(self, d: dict) -> None:
        self.note_metric("gasping_detected", d.get("gasping_detected"))
        self.note_metric("speech_to_breath_ratio", d.get("speech_to_breath_ratio"))
        self.note_metric("vocal_jitter", d.get("vocal_jitter"))
        self.note_metric("pitch_instability_hz", d.get("pitch_instability_hz"))
        self.note_metric("breath_events", d.get("breath_events"))

    def fresh_metrics(self) -> dict[str, Any]:
        """Latest value per metric, ignoring anything older than ``guard_fresh_s`` (never stale)."""
        now, limit = time.monotonic(), self.settings.guard_fresh_s
        keys = ("asymmetry_pct", "hr_bpm", "hr_confidence", "gasping_detected",
                "speech_to_breath_ratio", "vocal_jitter", "pitch_instability_hz", "au04_distress",
                "au09_pain", "au12_14_palsy_velocity")
        return {k: (self.metrics[k][0] if k in self.metrics and now - self.metrics[k][1] <= limit else None)
                for k in keys}

    # ---------------- events ----------------
    def publish(self, type_: str, payload: dict[str, Any]) -> None:
        env = EventEnvelope(type=type_, session_id=self.id, payload=payload).model_dump(mode="json")
        for q in list(self.subscribers):
            try:
                q.put_nowait(env)
            except asyncio.QueueFull:
                try:  # bounded backpressure: drop oldest, keep newest
                    q.get_nowait()
                    q.put_nowait(env)
                except Exception:
                    pass

    def record_error(self, source: str, message: str, recoverable: bool = True) -> None:
        err = {"id": new_id("err_"), "ts": now_iso(), "source": source, "message": message,
               "recoverable": recoverable}
        self.errors.append(err)
        del self.errors[:-50]
        self.publish("system.error", err)

    # ---------------- clinical data ----------------
    def _tag_segment(self, seg_id: str, text: str, model_language: str | None) -> None:
        self.seg_meta[seg_id] = {
            "model_language": model_language,
            "languages": clinical_terms.detect_languages(text, model_language),
            "symptoms": clinical_terms.normalize(text),
        }

    def speech_summary(self) -> dict:
        langs: list[str] = []
        syms: list[str] = []
        for seg in self.segments:
            m = self.seg_meta.get(seg.id) or {}
            langs += m.get("languages", [])
            syms += m.get("symptoms", [])
        return {"detected_languages": list(dict.fromkeys(langs)),
                "normalized_clinical_symptoms": list(dict.fromkeys(syms))}

    def add_segment(self, text: str, source: str, t_start: float | None = None,
                    t_end: float | None = None, language: str | None = None) -> tuple[TranscriptSegment, list[SymptomMention]]:
        seg = TranscriptSegment(text=text, original_text=text, source=source,  # type: ignore[arg-type]
                                t_start_s=t_start, t_end_s=t_end)
        self.segments.append(seg)
        self._tag_segment(seg.id, text, language)
        new = symptom_extractor.extract(seg.id, text)
        self.mentions.extend(new)
        self._refresh_suggestion()
        return seg, new

    def edit_segment(self, seg_id: str, text: str) -> TranscriptSegment:
        seg = next((s for s in self.segments if s.id == seg_id), None)
        if not seg:
            raise KeyError(seg_id)
        seg.text, seg.edited = text, True
        self._tag_segment(seg_id, text, (self.seg_meta.get(seg_id) or {}).get("model_language"))
        old = [m for m in self.mentions if m.segment_id == seg_id and m.origin == "machine"]
        carried = {(m.concept, m.polarity): m.status for m in old}
        self.mentions = [m for m in self.mentions if m not in old]
        for m in symptom_extractor.extract(seg_id, text):
            m.status = carried.get((m.concept, m.polarity), "pending")  # type: ignore[assignment]
            self.mentions.append(m)
        self._refresh_suggestion()
        return seg

    def _refresh_suggestion(self) -> None:
        self.review.chief_complaint_suggestion = symptom_extractor.suggest_chief_complaint(self.mentions)

    def reevaluate(self, engine: RuleEngine) -> tuple[list[SafetyAlert], list[SafetyAlert]]:
        """Recompute alerts. Returns (all_alerts, newly_active_alerts)."""
        before = {a.id for a in self.alerts}
        current = engine.evaluate(self.mentions, self.observations, self.segments)
        self.alerts = merge_alerts(self.alerts, current)
        new = [a for a in self.alerts if a.id not in before and a.state == "active"]
        return self.alerts, new

    def has_clinical_data(self) -> bool:
        return bool(self.segments or self.observations or self.review.chief_complaint)

    # ---------------- cleanup ----------------
    def purge_media_state(self) -> None:
        """Release buffered audio/vision state (no raw media is ever stored)."""
        if self.audio:
            self.audio.speech_buf.clear()
            self.audio.speech_samples = 0
            self.audio.in_speech = False
            self.audio.clear_ring()
        self.vision.ema = None
        self.vision.rppg.reset()
        self.vision.facs.reset()
        waiting_room.stop(self.monitor)
        self.metrics.clear()
        self.last_vision = None
        self.media_stopped = True


class SessionManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._sessions: dict[str, Session] = {}

    def expired_ids(self) -> list[str]:
        """Sessions idle longer than the TTL with no connected client (their data is then erased)."""
        ttl = self.settings.session_idle_ttl_minutes * 60
        now = time.monotonic()
        return [sid for sid, s in self._sessions.items()
                if not s.subscribers and now - s.last_activity > ttl]

    async def sweep(self) -> int:
        ids = self.expired_ids()
        for sid in ids:
            await self.delete(sid)
        if ids:
            log.info("Expired %d idle session(s)", len(ids))
        return len(ids)

    def create(self, req: SessionCreate) -> Session:
        if len(self._sessions) >= self.settings.max_sessions:
            raise TooManySessions("Maximum concurrent sessions reached; delete a session first")
        s = Session(
            id=new_id("ses_"), token=secrets.token_urlsafe(24), reference=req.reference.strip(),
            synthetic=req.synthetic, patient_reported_notes=req.patient_reported_notes,
            settings=self.settings,
        )
        s.audio = AudioSessionState(sr=self.settings.audio_sample_rate)
        self._sessions[s.id] = s
        return s

    def get(self, session_id: str, token: str | None) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise SessionNotFound(session_id)
        if not token or not hmac.compare_digest(token, s.token):
            raise SessionForbidden(session_id)
        s.last_activity = time.monotonic()
        return s

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    async def delete(self, session_id: str) -> bool:
        s = self._sessions.pop(session_id, None)
        if not s:
            return False
        for t in list(s.tasks):
            t.cancel()
        if s.tasks:
            await asyncio.gather(*s.tasks, return_exceptions=True)
        s.publish("session.status", {"status": "deleted"})
        s.purge_media_state()
        s.segments.clear()
        s.mentions.clear()
        s.observations.clear()
        s.alerts.clear()
        s.subscribers.clear()
        return True

    def count(self) -> int:
        return len(self._sessions)

    async def shutdown(self) -> None:
        for sid in list(self._sessions):
            await self.delete(sid)
