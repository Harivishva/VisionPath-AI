"""Application-wide singletons and the clinical 'commit' step shared by REST and WebSocket paths."""
from __future__ import annotations

import time

from app.config import Settings
from app.services.audio_processor import AudioProcessor
from app.schemas.models import now_iso
from app.services import waiting_room
from app.services.unified import build_unified, evaluate_guardrail
from app.services.clinical_summary import LLMService
from app.services.multimodal_aggregator import snapshot
from app.services.safety_rules import RuleEngine
from app.services.session_manager import Session, SessionManager
from app.services.transcription_service import TranscriptionService
from app.services.vision_processor import VisionProcessor


class AppState:
    def __init__(self, settings: Settings, vision: VisionProcessor | None = None):
        self.settings = settings
        self.sessions = SessionManager(settings)
        self.vision = vision or VisionProcessor(settings)
        self.audio = AudioProcessor(settings)
        self.stt = TranscriptionService(settings)
        self.llm = LLMService(settings)
        self.rules = RuleEngine()
        self.fhir_cache: dict[str, dict] = {}

    def snapshot(self, s: Session) -> dict:
        return snapshot(s, self.rules, self.vision.ready, self.vision.error,
                        self.stt.status(), self.llm.status())

    def refresh_guardrail(self, s: Session, force: bool = False) -> dict:
        """Re-evaluate guardrails (no LLM, no network) and broadcast, at most ~2x per second."""
        now = time.monotonic()
        prev = (s.guardrail or {}).get("triggered_rule")
        g = evaluate_guardrail(s, self.settings)
        if force or g["triggered_rule"] != prev or now - s.last_unified_pub >= 0.5:
            s.last_unified_pub = now
            s.publish("unified.metrics", build_unified(s, self.settings))
        return g

    def waiting_tick(self, s: Session, now: float | None = None) -> dict | None:
        """One ~1 Hz pass of the passive monitor: sample derived metrics, run the 5-minute trend check."""
        if not s.monitor.enabled:
            return None
        now = time.monotonic() if now is None else now
        waiting_room.ingest(s.monitor, now, s.fresh_metrics(), self.settings)
        alert = waiting_room.evaluate(s.monitor, now, self.settings, now_iso())
        if alert:
            s.publish(waiting_room.ALERT_EVENT, alert)
            self.refresh_guardrail(s, force=True)
        return alert

    def control(self, s: Session, action: str) -> None:
        """Session lifecycle. Raises ValueError on an invalid transition."""
        allowed = {"start": ("setup",), "pause": ("live",), "resume": ("paused",),
                   "stop": ("setup", "live", "paused")}
        if s.status not in allowed[action]:
            raise ValueError(f"Cannot {action} a session that is '{s.status}'")
        s.status = {"start": "live", "pause": "paused", "resume": "live", "stop": "stopped"}[action]
        if action == "stop":
            if s.audio is not None:
                utt = self.audio.flush(s.audio)
                if utt is not None and s.utt_queue is not None:
                    try:
                        s.utt_queue.put_nowait(utt)
                    except Exception:
                        s.record_error("transcription", "Final utterance dropped: queue full")
            s.purge_media_state()
        s.publish("session.status", {"status": s.status})
        s.publish("session.snapshot", self.snapshot(s))

    def commit(self, s: Session, new_mentions: list | None = None, segment=None) -> list:
        """Re-run deterministic rules and broadcast. Called after ANY clinical-data change.

        Rules run synchronously on the transcript text, so an alert never waits on a model,
        a camera, or an LLM.
        """
        _, new_alerts = s.reevaluate(self.rules)
        if segment is not None:
            s.publish("transcript.final", {"segment": segment.model_dump(mode="json")})
        for m in new_mentions or []:
            s.publish("symptom.extracted", {"mention": m.model_dump(mode="json")})
        for a in new_alerts:
            s.publish("safety.alert", {"alert": a.model_dump(mode="json")})
        s.publish("session.snapshot", self.snapshot(s))
        self.refresh_guardrail(s, force=True)
        return new_alerts
