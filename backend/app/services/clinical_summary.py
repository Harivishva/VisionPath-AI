"""Clinical summary: deterministic structured sections + optional validated LLM narrative draft."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.services.multimodal_aggregator import missing_information
from app.services.session_manager import Session

log = logging.getLogger("visionpath.summary")


def build_sections(s: Session) -> dict:
    """Separates observed facts, patient-reported statements, machine interpretations, clinician notes."""
    live = [m for m in s.mentions if m.status != "rejected"]
    reported = [
        {"id": m.id, "label": m.label, "polarity": m.polarity, "reporter": m.reporter,
         "quote": m.sentence, "onset": m.onset.model_dump() if m.onset else None,
         "status": m.status, "sudden_wording": m.sudden}
        for m in live
    ]
    vis = s.vision.summary()
    observed = {
        "camera_frames_processed": vis["frames_processed"],
        "frames_with_usable_face_geometry": vis["accepted_frames"],
        "frames_low_quality": vis["low_quality_frames"],
        "face_detected_frames": vis["face_frames"],
        "mean_geometry_experimental": vis["mean_measurement"],
        "audio": {k: v for k, v in (s.audio.summary() if s.audio else {}).items() if k != "last_feature"},
        "note": "Experimental technical signals; not clinical measurements and not diagnostic.",
    }
    machine = [
        {"rule_id": a.rule_id, "title": a.title, "severity": a.severity.value, "state": a.state,
         "explanation": a.explanation, "evidence": [e.text for e in a.evidence],
         "pending_review": a.pending_review}
        for a in s.alerts
    ]
    return {
        "chief_complaint": s.review.chief_complaint or None,
        "chief_complaint_suggestion": s.review.chief_complaint_suggestion or None,
        "patient_reported": reported,
        "staff_observations": [o.model_dump(mode="json") for o in s.observations],
        "observed_signals": observed,
        "machine_interpretations": machine,
        "clinician_notes": s.review.clinician_notes or None,
        "items_requiring_verification": missing_information(s)
        + [f"{m.label}: machine-extracted, not yet confirmed" for m in live if m.status == "pending"],
    }


def deterministic_text(s: Session) -> str:
    sec = build_sections(s)
    L = [f"INTAKE SUMMARY DRAFT — session {s.reference}" + (" (SYNTHETIC DEMO DATA)" if s.synthetic else ""),
         "Machine-assembled from entered data. Requires clinician review. Not a diagnosis.", ""]
    L.append(f"Chief complaint: {sec['chief_complaint'] or 'not confirmed'}")
    if sec["chief_complaint_suggestion"] and not sec["chief_complaint"]:
        L.append(f"  (machine suggestion, unconfirmed: {sec['chief_complaint_suggestion']})")
    L += ["", "Reported (patient/observer, from transcript):"]
    if not sec["patient_reported"]:
        L.append("  - none recorded")
    for r in sec["patient_reported"]:
        onset = ""
        if r["onset"]:
            onset = f"; onset: \"{r['onset']['text']}\"" + ("" if r["onset"]["certain"] else " (uncertain)")
        L.append(f"  - {r['label']}: {r['polarity']} ({r['reporter']}){onset} [{r['status']}]")
    if sec["staff_observations"]:
        L += ["", "Staff observations:"] + [
            f"  - {o['text']}" + (" [flagged concerning]" if o["concerning"] else "")
            for o in sec["staff_observations"]]
    L += ["", "Safety rules triggered (prototype; not ESI):"]
    active = [m for m in sec["machine_interpretations"] if m["state"] != "withdrawn"]
    L += [f"  - {m['title']} [{m['rule_id']}] — {m['severity'].replace('_', ' ')}" for m in active] or ["  - none"]
    o = sec["observed_signals"]
    L += ["", "Technical signals (experimental, not clinical measurements):",
          f"  - Camera frames processed: {o['camera_frames_processed']}; usable face-geometry frames: "
          f"{o['frames_with_usable_face_geometry']}",
          f"  - Speech segments detected: {o['audio'].get('speech_segments', 0)}"]
    L += ["", "Items requiring clinician verification:"] + [f"  - {x}" for x in sec["items_requiring_verification"]]
    if sec["clinician_notes"]:
        L += ["", f"Clinician notes: {sec['clinician_notes']}"]
    return "\n".join(L)


# ------------------------------- LLM (optional) -------------------------------
class LLMDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str = Field(min_length=1, max_length=2000)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    symptoms_mentioned: list[str] = Field(default_factory=list, max_length=30)


class LLMUnavailable(RuntimeError):
    pass


class LLMInvalid(RuntimeError):
    pass


SYSTEM_PROMPT = (
    "You assist emergency intake staff by drafting a short factual note from structured input. "
    "Rules: use ONLY the provided data. Do not diagnose. Do not invent measurements, vital signs, "
    "ages, history or symptoms. Do not assign triage levels or ESI scores. Do not give treatment "
    "advice. Preserve uncertainty and negation exactly. Never downplay or omit triggered safety rules. "
    "Reply with a JSON object: {\"narrative\": string, \"missing_information\": [string], "
    "\"symptoms_mentioned\": [string]} where symptoms_mentioned only contains labels from the input."
)


class LLMService:
    def __init__(self, settings: Settings):
        self.s = settings
        self.last_error: str | None = None

    def status(self) -> dict:
        p = self.s.llm_provider.lower()
        if p == "none":
            return {"provider": "none", "ready": False, "detail": "No LLM configured; deterministic summary is used",
                    "sends_data_externally": False, "last_error": self.last_error}
        ready = bool(self.s.llm_api_key and self.s.llm_model)
        return {"provider": p, "ready": ready, "model": self.s.llm_model or None,
                "detail": "Configured; confirmed intake text is sent to the configured API endpoint"
                if ready else "VP_LLM_API_KEY and VP_LLM_MODEL must both be set",
                "sends_data_externally": True, "last_error": self.last_error}

    async def draft(self, sections: dict) -> LLMDraft:
        st = self.status()
        if not st["ready"]:
            raise LLMUnavailable(st["detail"])
        allowed = {r["label"] for r in sections["patient_reported"]}
        payload = {k: sections[k] for k in ("chief_complaint", "patient_reported", "staff_observations",
                                            "machine_interpretations", "items_requiring_verification")}
        url = self.s.llm_base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.s.llm_model, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps(payload)}],
        }
        last: Exception | None = None
        for attempt in range(self.s.llm_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.s.llm_timeout_s) as c:
                    r = await c.post(url, json=body, headers={"Authorization": f"Bearer {self.s.llm_api_key}"})
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                draft = LLMDraft.model_validate(json.loads(content))
                extra = [x for x in draft.symptoms_mentioned if x not in allowed]
                if extra:
                    raise LLMInvalid(f"Draft mentions symptoms not in input: {extra}")
                self.last_error = None
                return draft
            except LLMInvalid as exc:
                self.last_error = "LLMInvalid"
                raise exc
            except (ValidationError, json.JSONDecodeError, KeyError) as exc:
                self.last_error = "invalid_response"
                raise LLMInvalid(type(exc).__name__) from exc
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                await asyncio.sleep(0.3 * (attempt + 1))
        self.last_error = type(last).__name__ if last else "unknown"
        raise LLMUnavailable(self.last_error)
