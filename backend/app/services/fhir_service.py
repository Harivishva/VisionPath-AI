"""FHIR R4-compatible bundle generation and local structural validation.

Structural validation uses the `fhir.resources` R4B Pydantic models (FHIR 4.3.0, which share the
R4 structure for every resource type used here) plus value-set and reference-consistency checks
implemented in this module. Passing local validation does NOT establish clinical validity,
regulatory approval, or successful EHR integration.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Any

from fhir.resources.R4B import get_fhir_model_class

from app.services.multimodal_aggregator import vision_modality
from app.services.unified import build_unified
from app.services.session_manager import Session

SRC_SYSTEM = "https://visionpath.example/CodeSystem/data-source"
CAT_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
ESI_SYSTEM = "https://visionpath.example/CodeSystem/esi-style-priority"
LOINC = "http://loinc.org"
DAR_SYSTEM = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
SNOMED = "http://snomed.info/sct"

# SNOMED CT codes used only where the mapping is unambiguous. Everything else is text-only.
SNOMED_CODES = {
    "chest_pain": ("29857009", "Chest pain"),
    "dyspnea": ("267036007", "Dyspnea"),
    "headache": ("25064002", "Headache"),
    "dizziness": ("404640003", "Dizziness"),
    "syncope": ("271594007", "Syncope"),
    "seizure": ("91175000", "Seizure"),
    "palpitations": ("80313002", "Palpitations"),
    "nausea": ("422587007", "Nausea"),
    "fever": ("386661006", "Fever"),
    "abdominal_pain": ("21522001", "Abdominal pain"),
}
PRESENCE = {
    "affirmed": ("52101004", "Present"),
    "negated": ("2667000", "Absent"),
    "uncertain": ("261665006", "Unknown"),
}
DAR_BY_STATE = {
    "not_measured": ("not-performed", "Not performed"),
    "unavailable": ("error", "Error"),
    "low_quality": ("unknown", "Unknown"),
    "not_detected": ("unknown", "Unknown"),
}
VALUE_SETS = {
    "Bundle.type": {"document", "message", "transaction", "transaction-response", "batch",
                    "batch-response", "history", "searchset", "collection"},
    "Observation.status": {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
                           "entered-in-error", "unknown"},
    "Encounter.status": {"planned", "arrived", "triaged", "in-progress", "onleave", "finished",
                         "cancelled", "entered-in-error", "unknown"},
    "Flag.status": {"active", "inactive", "entered-in-error"},
    "DocumentReference.status": {"current", "superseded", "entered-in-error"},
    "DocumentReference.docStatus": {"preliminary", "final", "amended", "entered-in-error"},
}
DISCLAIMER = (
    "Local structural validation only (fhir.resources R4B models + reference/value-set checks). "
    "This does not establish clinical validity, regulatory approval, or EHR integration. "
    "Experimental vision/audio signals are not standardized clinical observations."
)


def _u() -> str:
    return "urn:uuid:" + str(uuid.uuid4())


def _tag(code: str, synthetic: bool = False) -> dict:
    tags = [{"system": SRC_SYSTEM, "code": code}]
    if synthetic:
        tags.append({"system": "http://terminology.hl7.org/CodeSystem/v3-ActReason", "code": "HTEST",
                     "display": "test health data"})
    return {"tag": tags}


def build_bundle(s: Session) -> tuple[dict, list[str]]:
    """Return (bundle_dict, notes). Rejected and unconfirmed machine mentions are excluded."""
    notes: list[str] = []
    syn = s.synthetic
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status_obs = "final" if s.review.finalized else "preliminary"
    entries: list[tuple[str, dict]] = []

    pat_url, enc_url = _u(), _u()
    patient = {
        "resourceType": "Patient", "id": pat_url[9:], "meta": _tag("pseudonymous-session", syn),
        "identifier": [{"system": "https://visionpath.example/session-reference", "value": s.reference},
                       {"system": "https://visionpath.example/session-id", "value": s.id}],
    }  # No demographics are collected by this prototype, so none are exported.
    entries.append((pat_url, patient))
    ref_p, ref_e = {"reference": pat_url}, {"reference": enc_url}
    encounter: dict[str, Any] = {
        "resourceType": "Encounter", "id": enc_url[9:], "meta": _tag("session", syn),
        "status": "finished" if s.status == "stopped" or s.review.finalized else "in-progress",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "EMER",
                  "display": "emergency"},
        "subject": ref_p, "period": {"start": s.created_at},
    }
    if s.review.finalized and s.review.finalized_at:
        encounter["period"]["end"] = s.review.finalized_at
    if s.review.chief_complaint:
        encounter["reasonCode"] = [{"text": s.review.chief_complaint}]
    uni = build_unified(s, s.settings)
    lvl = uni["priority"]["level"]
    if lvl is not None:
        encounter["priority"] = {
            "coding": [{"system": ESI_SYSTEM, "code": str(lvl), "display": f"ESI-style level {lvl} (prototype)"}],
            "text": f"Prototype ESI-style priority level {lvl}. Not an official ESI assessment.",
        }
        encounter["extension"] = [{
            "url": "https://visionpath.example/StructureDefinition/guardrail-status",
            "valueString": ("hard override: " + uni["guardrail_status"]["triggered_rule"])
            if uni["guardrail_status"]["hard_override_triggered"] else "no hard override; level from transcript rules",
        }]
    entries.append((enc_url, encounter))

    def ctx(res: dict) -> dict:
        res.update({"subject": ref_p, "encounter": ref_e})
        return res

    # Patient-reported symptoms (confirmed only)
    excluded = 0
    for m in s.mentions:
        if m.status != "confirmed":
            excluded += m.status == "pending"
            continue
        code: dict[str, Any] = {"text": m.label}
        if m.concept in SNOMED_CODES:
            c, d = SNOMED_CODES[m.concept]
            code["coding"] = [{"system": SNOMED, "code": c, "display": d}]
        pc, pd = PRESENCE[m.polarity]
        note = f'Source sentence: "{m.sentence}"' + (
            f' Onset as stated: "{m.onset.text}"' + ("" if m.onset.certain else " (uncertain)")
            if m.onset else "")
        u = _u()
        entries.append((u, ctx({
            "resourceType": "Observation", "id": u[9:], "meta": _tag(
                "patient-reported" if m.origin == "machine" and m.reporter != "observer" else "staff-entered", syn),
            "status": status_obs,
            "category": [{"coding": [{"system": CAT_SYSTEM, "code": "survey", "display": "Survey"}]}],
            "code": code,
            "effectiveDateTime": m.created_at,
            "valueCodeableConcept": {"coding": [{"system": SNOMED, "code": pc, "display": pd}], "text": pd},
            "note": [{"text": note}],
        })))
    if excluded:
        notes.append(f"{excluded} unconfirmed machine-extracted symptom mention(s) were excluded from export")

    for o in s.observations:
        u = _u()
        entries.append((u, ctx({
            "resourceType": "Observation", "id": u[9:], "meta": _tag("staff-entered", syn),
            "status": status_obs,
            "category": [{"coding": [{"system": CAT_SYSTEM, "code": "exam", "display": "Exam"}]}],
            "code": {"text": "Staff-entered intake observation"},
            "effectiveDateTime": o.created_at, "valueString": o.text,
        })))

    # Experimental technical signals (never standardized observations, never final)
    vm = vision_modality(s, True, None)
    names = {
        "mouth_corner_vertical_offset": "Experimental: mouth-corner vertical offset (image-right minus image-left, inter-ocular units)",
        "brow_height_offset": "Experimental: brow-to-eyelid distance offset (inter-ocular units)",
        "eye_opening_offset": "Experimental: eye-opening offset (inter-ocular units)",
    }
    if vm.get("mean_measurement"):
        for k, v in vm["mean_measurement"].items():
            u = _u()
            entries.append((u, ctx({
                "resourceType": "Observation", "id": u[9:], "meta": _tag("machine-derived-experimental", syn),
                "status": "preliminary",
                "category": [{"coding": [{"system": SRC_SYSTEM, "code": "experimental-technical-signal"}]}],
                "code": {"text": names[k]}, "effectiveDateTime": now,
                "valueQuantity": {"value": v, "unit": "1", "system": "http://unitsofmeasure.org", "code": "1"},
                "note": [{"text": f"Mean over {vm['accepted_frames']} quality-gated frames. Geometric, "
                                  "non-diagnostic, not a clinical measurement."}],
            })))
    else:
        dc, dd = DAR_BY_STATE.get(vm["state"], ("unknown", "Unknown"))
        u = _u()
        entries.append((u, ctx({
            "resourceType": "Observation", "id": u[9:], "meta": _tag("machine-derived-experimental", syn),
            "status": "preliminary",
            "category": [{"coding": [{"system": SRC_SYSTEM, "code": "experimental-technical-signal"}]}],
            "code": {"text": "Experimental facial geometry (camera)"}, "effectiveDateTime": now,
            "dataAbsentReason": {"coding": [{"system": DAR_SYSTEM, "code": dc, "display": dd}],
                                 "text": f"{vm['state']}: {vm.get('reason') or 'no usable measurement'}"},
        })))
    a = s.audio.summary() if s.audio else {"state": "not_measured", "speech_segments": 0}
    u = _u()
    audio_obs: dict[str, Any] = {
        "resourceType": "Observation", "id": u[9:], "meta": _tag("machine-derived-experimental", syn),
        "status": "preliminary",
        "category": [{"coding": [{"system": SRC_SYSTEM, "code": "experimental-technical-signal"}]}],
        "code": {"text": "Experimental audio: speech segments detected"}, "effectiveDateTime": now,
    }
    if a["state"] == "not_measured":
        audio_obs["dataAbsentReason"] = {"coding": [{"system": DAR_SYSTEM, "code": "not-performed",
                                                     "display": "Not performed"}],
                                         "text": "not_measured: no microphone audio processed"}
    else:
        audio_obs["valueInteger"] = int(a["speech_segments"])
    entries.append((u, ctx(audio_obs)))

    # Multimodal experimental signals (latest derived values; never final, never standardized vitals)
    def exp_obs(code: dict, key: str, value: dict, note: str) -> None:
        u2 = _u()
        entries.append((u2, ctx({
            "resourceType": "Observation", "id": u2[9:], "meta": _tag("machine-derived-experimental", syn),
            "status": "preliminary",
            "category": [{"coding": [{"system": SRC_SYSTEM, "code": "experimental-technical-signal"}]}],
            "code": code, "effectiveDateTime": s.last_known[key][1], **value, "note": [{"text": note}],
        })))

    ucum = "http://unitsofmeasure.org"
    if "hr_bpm" in s.last_known:
        conf = (s.last_known.get("hr_confidence") or (None,))[0]
        exp_obs({"coding": [{"system": LOINC, "code": "8867-4", "display": "Heart rate"}],
                 "text": "Experimental rPPG heart rate"}, "hr_bpm",
                {"valueQuantity": {"value": s.last_known["hr_bpm"][0], "unit": "beats/minute",
                                   "system": ucum, "code": "/min"}},
                f"Camera-derived pulse estimate (spectral confidence {conf}). Experimental, not a validated vital sign.")
    if "asymmetry_pct" in s.last_known:
        exp_obs({"text": "Experimental facial asymmetry"}, "asymmetry_pct",
                {"valueQuantity": {"value": s.last_known["asymmetry_pct"][0], "unit": "%",
                                   "system": ucum, "code": "%"}},
                "Larger of mouth-corner and brow offsets as % of inter-ocular distance. Geometric, non-diagnostic.")
    if "breath_events" in s.last_known:
        exp_obs({"text": "Experimental acoustic gasping score (breath-like bursts in pauses)"}, "breath_events",
                {"valueInteger": int(s.last_known["breath_events"][0])},
                "Count of breath-like noise bursts in the latest analysis window. "
                "An acoustic observation, not a respiratory measurement.")

    if s.monitor.alert:
        u = _u()
        entries.append((u, {
            "resourceType": "Flag", "id": u[9:], "meta": _tag("waiting-room-monitor", syn), "status": "active",
            "category": [{"coding": [{"system": SRC_SYSTEM, "code": "re-triage-alert"}]}],
            "code": {"text": ("Re-triage suggested: " + "; ".join(s.monitor.alert_reasons))[:900]},
            "subject": ref_p, "encounter": ref_e, "period": {"start": s.monitor.alert_at or now},
        }))

    # Rule-engine alerts -> Flag (withdrawn alerts excluded)
    for al in s.alerts:
        if al.state == "withdrawn":
            continue
        u = _u()
        entries.append((u, {
            "resourceType": "Flag", "id": u[9:], "meta": _tag("rule-engine", syn),
            "status": "active",
            "category": [{"coding": [{"system": SRC_SYSTEM, "code": "rule-engine-alert"}]}],
            "code": {"coding": [{"system": SRC_SYSTEM, "code": al.rule_id, "display": al.title}],
                     "text": f"{al.title} [{al.severity.value}] {al.explanation}"[:900]},
            "subject": ref_p, "encounter": ref_e, "period": {"start": al.created_at},
        }))

    if s.review.summary_text:
        u = _u()
        entries.append((u, {
            "resourceType": "DocumentReference", "id": u[9:], "meta": _tag("staff-entered", syn),
            "status": "current", "docStatus": "final" if s.review.finalized else "preliminary",
            "type": {"text": "Emergency intake summary (clinician-reviewed draft)"},
            "subject": ref_p, "date": now,
            "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                        "data": base64.b64encode(s.review.summary_text.encode()).decode(),
                                        "title": "Intake summary"}}],
            "context": {"encounter": [ref_e]},
        }))

    bundle = {
        "resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "timestamp": now,
        "meta": _tag("visionpath-prototype-export", syn),
        "entry": [{"fullUrl": url, "resource": res} for url, res in entries],
    }
    if not s.review.finalized:
        notes.append("Review not finalized: observations are marked 'preliminary'")
    return bundle, notes


def _walk_refs(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                out.append(v)
            else:
                _walk_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, out)


def _check_enum(path: str, value: Any, errors: list[dict], resource: str) -> None:
    allowed = VALUE_SETS.get(path)
    if allowed is not None and value is not None and value not in allowed:
        errors.append({"resource": resource, "path": path, "message": f"'{value}' is not in the required value set"})


def _error_items(exc: Exception) -> list[dict]:
    errs = getattr(exc, "errors", None)
    return list(errs()) if callable(errs) else [{"loc": [], "msg": str(exc)}]


def validate_bundle(bundle: dict) -> dict:
    errors: list[dict] = []
    counts: dict[str, int] = {}
    if bundle.get("resourceType") != "Bundle":
        return {"valid": False, "errors": [{"resource": "Bundle", "path": "resourceType",
                                            "message": "Not a Bundle"}], "resource_counts": {},
                "references_checked": 0, "disclaimer": DISCLAIMER}
    try:
        get_fhir_model_class("Bundle").model_validate(bundle)
    except Exception as exc:
        for e in _error_items(exc):
            loc = [str(x) for x in e.get("loc", [])]
            if loc[:1] == ["entry"]:
                continue  # nested entry problems are reported once, per resource, below
            errors.append({"resource": "Bundle", "path": ".".join(loc), "message": e.get("msg", "invalid")})
    _check_enum("Bundle.type", bundle.get("type"), errors, "Bundle")

    urls = set()
    for i, ent in enumerate(bundle.get("entry", []) or []):
        res = ent.get("resource", {})
        rtype = res.get("resourceType", "?")
        counts[rtype] = counts.get(rtype, 0) + 1
        if ent.get("fullUrl"):
            urls.add(ent["fullUrl"])
        if rtype in ("Observation", "Encounter", "Flag"):
            _check_enum(f"{rtype}.status", res.get("status"), errors, f"{rtype}[{i}]")
        if rtype == "DocumentReference":
            _check_enum("DocumentReference.status", res.get("status"), errors, f"{rtype}[{i}]")
            _check_enum("DocumentReference.docStatus", res.get("docStatus"), errors, f"{rtype}[{i}]")
        try:
            get_fhir_model_class(rtype).model_validate(res)
        except Exception as exc:
            for e in _error_items(exc):
                errors.append({"resource": f"{rtype}[{i}]", "path": ".".join(map(str, e.get("loc", []))),
                               "message": e.get("msg", "invalid")})
    n_refs = 0
    for i, ent in enumerate(bundle.get("entry", []) or []):
        refs: list[str] = []
        _walk_refs(ent.get("resource", {}), refs)
        for r in refs:
            n_refs += 1
            if r not in urls:
                errors.append({"resource": f"{ent.get('resource', {}).get('resourceType', '?')}[{i}]",
                               "path": "reference", "message": f"Reference '{r}' does not resolve inside the bundle"})
    return {"valid": not errors, "errors": errors, "resource_counts": counts, "references_checked": n_refs,
            "standard": "FHIR R4 (validated with fhir.resources R4B / 4.3.0 models)", "disclaimer": DISCLAIMER}
