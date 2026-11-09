"""Transparent, configurable safety-rule engine.

Rules fire ONLY on explicit evidence (reported symptoms in the transcript or staff-entered
observations). Computer-vision / audio features never trigger or suppress an alert, and no
numeric score is produced. Labels are workflow aids, not ESI levels or triage decisions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.schemas.models import (
    URGENCY_LABEL, URGENCY_RANK, Evidence, RuleConfig, SafetyAlert, StaffObservation,
    SymptomMention, TranscriptSegment, Urgency,
)

NEURO = ["facial_droop", "slurred_speech", "unilateral_weakness", "numbness", "confusion", "vision_change"]
CONCERNING = NEURO + ["chest_pain", "dyspnea", "syncope", "seizure", "bleeding"]

ACTION_IMMEDIATE = "Alert the responsible clinician now for bedside assessment per local protocol."
ACTION_PROMPT = "Flag for prompt clinician review and verify the statement with the patient or observer."


@dataclass
class RuleDef:
    id: str
    title: str
    description: str
    severity: Urgency
    concepts: list[str] = field(default_factory=list)
    enabled: bool = True


DEFAULT_RULES: list[RuleDef] = [
    RuleDef("R-NEURO-SUDDEN", "Sudden neurological symptom reported",
            "An affirmed neurological symptom (face, speech, weakness, numbness, confusion, vision) "
            "was reported together with a statement of sudden onset.", Urgency.IMMEDIATE, NEURO),
    RuleDef("R-NEURO-COMBO", "Multiple neurological symptoms reported",
            "Two or more distinct affirmed neurological symptoms were reported in this session.",
            Urgency.IMMEDIATE, NEURO),
    RuleDef("R-NEURO-REPORTED", "Neurological symptom reported",
            "An affirmed neurological symptom was reported without a statement of sudden onset.",
            Urgency.PROMPT, NEURO),
    RuleDef("R-CHEST-001", "Chest pain or discomfort reported",
            "Chest pain, pressure or tightness was reported.", Urgency.IMMEDIATE, ["chest_pain"]),
    RuleDef("R-BREATH-001", "Difficulty breathing reported",
            "Shortness of breath or difficulty breathing was reported. (Reported speech content only; "
            "no breathing measurement is made.)", Urgency.IMMEDIATE, ["dyspnea"]),
    RuleDef("R-CONSC-001", "Fainting, collapse or seizure reported",
            "Loss of consciousness, collapse or seizure was reported.", Urgency.IMMEDIATE,
            ["syncope", "seizure"]),
    RuleDef("R-BLEED-SEVERE", "Heavy bleeding reported",
            "Bleeding was reported together with a severity qualifier (e.g. heavy, a lot).",
            Urgency.IMMEDIATE, ["bleeding"]),
    RuleDef("R-BLEED-001", "Bleeding reported", "Bleeding was reported.", Urgency.PROMPT, ["bleeding"]),
    RuleDef("R-UNCERTAIN-001", "Possible concerning symptom mentioned with uncertainty",
            "A concerning symptom was mentioned in an uncertain way and needs verification. "
            "It is surfaced, never ignored.", Urgency.PROMPT, CONCERNING),
    RuleDef("R-STAFF-001", "Staff flagged a concerning observation",
            "A staff member recorded an observation marked as concerning. Severity is set by the staff "
            "member.", Urgency.PROMPT),
]


class RuleEngine:
    def __init__(self, rules: list[RuleDef] | None = None):
        self.rules: dict[str, RuleDef] = {r.id: r for r in (rules or [RuleDef(**r.__dict__) for r in DEFAULT_RULES])}

    def config(self) -> list[dict]:
        return [
            {"id": r.id, "title": r.title, "description": r.description, "enabled": r.enabled,
             "severity": r.severity.value, "concepts": r.concepts}
            for r in self.rules.values()
        ]

    def update(self, updates: list[RuleConfig]) -> None:
        unknown = [u.id for u in updates if u.id not in self.rules]
        if unknown:
            raise KeyError(f"Unknown rule id(s): {', '.join(unknown)}")
        for u in updates:
            self.rules[u.id].enabled = u.enabled
            self.rules[u.id].severity = u.severity

    # ------------------------------------------------------------------
    def evaluate(
        self,
        mentions: list[SymptomMention],
        observations: list[StaffObservation],
        segments: list[TranscriptSegment],
    ) -> list[SafetyAlert]:
        seg_ts = {s.id: s.created_at for s in segments}
        live = [m for m in mentions if m.status != "rejected"]
        alerts: list[SafetyAlert] = []
        used_by_neuro_sudden: set[str] = set()

        def ev(m: SymptomMention) -> Evidence:
            return Evidence(source="symptom_mention", ref_id=m.id, text=f'"{m.sentence}"',
                            timestamp=seg_ts.get(m.segment_id, m.created_at), reporter=m.reporter)

        def make(rule: RuleDef, key: str, evidence: list[Evidence], pending: bool, severity=None,
                 explanation: str | None = None) -> SafetyAlert:
            aid = "alt_" + hashlib.sha1(f"{rule.id}:{key}".encode()).hexdigest()[:12]
            return SafetyAlert(
                id=aid, rule_id=rule.id, title=rule.title, severity=severity or rule.severity,
                explanation=explanation or rule.description,
                recommended_action=ACTION_IMMEDIATE if (severity or rule.severity) == Urgency.IMMEDIATE else ACTION_PROMPT,
                evidence=evidence, pending_review=pending,
            )

        def on(rid: str) -> RuleDef | None:
            r = self.rules.get(rid)
            return r if r and r.enabled else None

        aff = [m for m in live if m.polarity == "affirmed"]

        if (r := on("R-NEURO-SUDDEN")):
            for m in aff:
                if m.concept in r.concepts and m.sudden:
                    used_by_neuro_sudden.add(m.id)
                    alerts.append(make(r, m.id, [ev(m)], m.status == "pending",
                                       explanation=f"Reported \"{m.label}\" with sudden onset wording in: {m.sentence}"))
        if (r := on("R-NEURO-COMBO")):
            neuro = [m for m in aff if m.concept in r.concepts]
            concepts = sorted({m.concept for m in neuro})
            if len(concepts) >= 2:
                firsts = [next(m for m in neuro if m.concept == c) for c in concepts]
                alerts.append(make(r, "|".join(concepts), [ev(m) for m in firsts],
                                   any(m.status == "pending" for m in firsts),
                                   explanation="Distinct neurological symptoms reported: "
                                               + ", ".join(m.label for m in firsts)))
        if (r := on("R-NEURO-REPORTED")):
            for m in aff:
                if m.concept in r.concepts and m.id not in used_by_neuro_sudden:
                    alerts.append(make(r, m.id, [ev(m)], m.status == "pending",
                                       explanation=f"Reported \"{m.label}\" in: {m.sentence}"))
        for rid in ("R-CHEST-001", "R-BREATH-001", "R-CONSC-001"):
            if (r := on(rid)):
                for m in aff:
                    if m.concept in r.concepts:
                        alerts.append(make(r, m.id, [ev(m)], m.status == "pending",
                                           explanation=f"Reported \"{m.label}\" in: {m.sentence}"))
        severe_bleed: set[str] = set()
        if (r := on("R-BLEED-SEVERE")):
            for m in aff:
                if m.concept == "bleeding" and m.severity_hint:
                    severe_bleed.add(m.id)
                    alerts.append(make(r, m.id, [ev(m)], m.status == "pending",
                                       explanation=f"Bleeding with severity wording in: {m.sentence}"))
        if (r := on("R-BLEED-001")):
            for m in aff:
                if m.concept == "bleeding" and m.id not in severe_bleed:
                    alerts.append(make(r, m.id, [ev(m)], m.status == "pending",
                                       explanation=f"Bleeding reported in: {m.sentence}"))
        if (r := on("R-UNCERTAIN-001")):
            for m in live:
                if m.polarity == "uncertain" and m.concept in r.concepts:
                    alerts.append(make(r, m.id, [ev(m)], True,
                                       explanation=f"\"{m.label}\" was mentioned with uncertainty in: {m.sentence}"))
        if (r := on("R-STAFF-001")):
            for o in observations:
                if o.concerning:
                    alerts.append(make(
                        r, o.id,
                        [Evidence(source="staff_observation", ref_id=o.id, text=o.text,
                                  timestamp=o.created_at, reporter="staff")],
                        False, severity=o.urgency,
                        explanation="Staff-recorded concerning observation: " + o.text))
        alerts.sort(key=lambda a: -URGENCY_RANK[a.severity])
        return alerts


def merge_alerts(previous: list[SafetyAlert], current: list[SafetyAlert]) -> list[SafetyAlert]:
    """Preserve acknowledgement state; never silently drop an alert that previously fired."""
    prev = {a.id: a for a in previous}
    cur_ids = {a.id for a in current}
    merged: list[SafetyAlert] = []
    for a in current:
        old = prev.get(a.id)
        if old and old.state == "acknowledged":
            a.state, a.acknowledged_at = "acknowledged", old.acknowledged_at
        if old:
            a.created_at = old.created_at
        merged.append(a)
    for old in previous:
        if old.id not in cur_ids and old.state != "withdrawn":
            old.state = "withdrawn"
            old.withdrawn_reason = "Underlying evidence was rejected or edited by a clinician"
            merged.append(old)
        elif old.id not in cur_ids:
            merged.append(old)
    merged.sort(key=lambda a: (a.state == "withdrawn", -URGENCY_RANK[a.severity], a.created_at))
    return merged


def overall_indicator(alerts: list[SafetyAlert], has_data: bool) -> dict:
    """Prototype workflow label. Highest active (non-withdrawn) alert wins. Not an ESI level."""
    active = [a for a in alerts if a.state != "withdrawn"]
    if active:
        top = max(active, key=lambda a: URGENCY_RANK[a.severity])
        level = top.severity
        basis = f"Highest triggered rule: {top.rule_id}"
    elif has_data:
        level = Urgency.ROUTINE
        basis = "No configured rule was triggered by the information entered so far (not a clearance)"
    else:
        level = Urgency.INSUFFICIENT
        basis = "No reported symptoms or staff observations have been entered"
    return {"level": level.value, "label": URGENCY_LABEL[level], "basis": basis,
            "disclaimer": "Prototype workflow aid. Not an Emergency Severity Index (ESI) level or a triage decision."}
