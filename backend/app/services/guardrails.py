"""Deterministic guardrail matrix: hard red-flag rules evaluated BEFORE any LLM.

These produce a PROTOTYPE, ESI-style priority override. It is not an official Emergency Severity
Index determination: the inputs are experimental webcam/microphone signals and the thresholds are
configurable prototype values. A trained clinician decides acuity.
"""
from __future__ import annotations

from app.config import Settings
from app.schemas.models import Urgency

DISCLAIMER = ("Prototype ESI-style priority from experimental signals and configurable thresholds. "
              "Not an official ESI assessment; a clinician decides acuity.")


def evaluate(m: dict, s: Settings) -> dict:
    """m keys (None = not available/fresh): asymmetry_pct, hr_bpm, hr_confidence,
    gasping_detected, speech_to_breath_ratio."""
    rules: list[dict] = []
    hr, hc = m.get("hr_bpm"), m.get("hr_confidence")
    if hr is not None and (hc or 0) >= s.guard_hr_min_confidence:
        if hr > s.guard_hr_high_bpm:
            rules.append({"id": "ESI_LEVEL_2_TACHYCARDIA", "name": "TACHYCARDIA", "level": 2,
                          "evidence": f"rPPG heart rate {hr:.0f} BPM > {s.guard_hr_high_bpm:.0f} "
                                      f"(signal strength {hc:.2f})"})
        elif hr < s.guard_hr_low_bpm:
            rules.append({"id": "ESI_LEVEL_2_BRADYCARDIA", "name": "BRADYCARDIA", "level": 2,
                          "evidence": f"rPPG heart rate {hr:.0f} BPM < {s.guard_hr_low_bpm:.0f} "
                                      f"(signal strength {hc:.2f})"})
    asym = m.get("asymmetry_pct")
    if asym is not None and asym > s.guard_asymmetry_pct:
        rules.append({"id": "ESI_LEVEL_2_FACIAL_ASYMMETRY", "name": "FACIAL_ASYMMETRY", "level": 2,
                      "evidence": f"Facial geometric asymmetry {asym:.1f}% > {s.guard_asymmetry_pct:.1f}%"})
    ratio = m.get("speech_to_breath_ratio")
    if m.get("gasping_detected") is True and ratio is not None and ratio < s.guard_speech_ratio:
        lvl = 1 if s.guard_gasp_level == 1 else 2
        rules.append({"id": f"ESI_LEVEL_{lvl}_GASPING_LOW_SPEECH_RATIO", "name": "GASPING", "level": lvl,
                      "evidence": f"Breath-like bursts in pauses with speech-to-pause ratio "
                                  f"{ratio:.2f} < {s.guard_speech_ratio:.2f}"})
    if not rules:
        return {"hard_override_triggered": False, "triggered_rule": None, "forced_esi_score": None,
                "triggered_rules": [], "disclaimer": DISCLAIMER}
    level = min(r["level"] for r in rules)
    names = "_AND_".join(r["name"] for r in rules)
    return {"hard_override_triggered": True,
            "triggered_rule": f"ESI_LEVEL_{level}_{names}_OVERRIDE",
            "forced_esi_score": level, "triggered_rules": rules, "disclaimer": DISCLAIMER}


def priority(guard: dict, text_indicator: dict | None) -> dict:
    """Priority badge: a hard override wins; otherwise the transcript/staff rule engine maps to a level."""
    if guard["hard_override_triggered"]:
        return {"level": guard["forced_esi_score"], "source": "guardrail"}
    lvl = (text_indicator or {}).get("level")
    if lvl == Urgency.IMMEDIATE.value:
        return {"level": 2, "source": "text_rule"}
    if lvl == Urgency.PROMPT.value:
        return {"level": 3, "source": "text_rule"}
    return {"level": None, "source": "none"}


def attributions(m: dict, text_indicator: dict | None, s: Settings) -> dict:
    """SHAP-inspired contribution shares: weight x normalised feature signal, as % of the total.

    A transparent weighted attribution of what drove the priority, NOT Shapley values and not
    an explanation of a learned model. All None when no feature contributes.
    """
    try:
        wv, wa, wt = (float(v) for v in s.xai_weights.split(","))
    except ValueError:
        wv, wa, wt = 0.35, 0.35, 0.30
    asym = m.get("asymmetry_pct")
    f_asym = min(asym / (2 * s.guard_asymmetry_pct), 1.0) if asym is not None else 0.0
    hr, hc = m.get("hr_bpm"), m.get("hr_confidence") or 0.0
    f_hr = 0.0
    if hr is not None and hc >= s.guard_hr_min_confidence:
        if hr > s.guard_hr_high_bpm:
            f_hr = min((hr - s.guard_hr_high_bpm) / 40.0 + 0.5, 1.0)
        elif hr < s.guard_hr_low_bpm:
            f_hr = min((s.guard_hr_low_bpm - hr) / 20.0 + 0.5, 1.0)
    ratio = m.get("speech_to_breath_ratio")
    f_aud = 0.0
    if m.get("gasping_detected") is True:
        f_aud = 0.7
        if ratio is not None:
            f_aud += 0.3 * min(max((s.guard_speech_ratio - ratio) / s.guard_speech_ratio, 0.0), 1.0)
    lvl = (text_indicator or {}).get("level")
    f_txt = {Urgency.IMMEDIATE.value: 1.0, Urgency.PROMPT.value: 0.6}.get(lvl, 0.0)
    c = {"asymmetry": wv * f_asym, "heart_rate": wv * f_hr, "gasping": wa * f_aud, "transcript": wt * f_txt}
    total = sum(c.values())
    if total <= 0:
        return {"asymmetry_weight_pct": None, "heart_rate_weight_pct": None, "gasping_weight_pct": None,
                "transcript_weight_pct": None,
                "method": "weighted_signal_share (SHAP-inspired, not Shapley values)"}
    raw = {k: 100 * v / total for k, v in c.items()}
    out = {k: int(v) for k, v in raw.items()}  # largest-remainder rounding so shares sum to 100
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: 100 - sum(out.values())]:
        out[k] += 1
    return {"asymmetry_weight_pct": out["asymmetry"], "heart_rate_weight_pct": out["heart_rate"],
            "gasping_weight_pct": out["gasping"], "transcript_weight_pct": out["transcript"],
            "method": "weighted_signal_share (SHAP-inspired, not Shapley values)"}
