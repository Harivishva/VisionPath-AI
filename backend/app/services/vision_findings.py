"""Plain-language visual signs derived from FRESH camera metrics (experimental, for staff review).

Each finding names the measurement it comes from. These are geometric / signal observations, not
symptoms the patient reported and not diagnoses; absent data produces no finding (never "normal").
"""
from __future__ import annotations

from typing import Any

from app.config import Settings


def derive(m: dict[str, Any], facs: dict | None, s: Settings) -> list[dict]:
    out: list[dict] = []

    def add(signal: str, label: str, value: float, unit: str, evidence: str, level: str = "notice") -> None:
        out.append({"signal": signal, "label": label, "value": value, "unit": unit, "evidence": evidence,
                    "level": level, "experimental": True})

    a = m.get("asymmetry_pct")
    if a is not None and a >= s.vis_asymmetry_pct:
        marked = a >= s.guard_asymmetry_pct
        add("asymmetry", "Marked facial asymmetry" if marked else "Possible facial asymmetry", a, "%",
            f"Mouth/brow offset {a:.1f}% of inter-ocular distance (notice ≥ {s.vis_asymmetry_pct:g}%, "
            f"guardrail > {s.guard_asymmetry_pct:g}%). Check the patient is facing the camera.",
            "alert" if marked else "notice")
    facs = facs or {}
    if facs.get("state") == "pending_review":
        v = m.get("au04_distress")
        if v is not None and v >= s.vis_au_score:
            add("au04", "Brow lowering / furrowing vs. baseline", v, "0-1",
                f"AU04 proxy {v:.2f}: brow-to-eye distance is shorter than at the start of the session.")
        v = m.get("au09_pain")
        if v is not None and v >= s.vis_au_score:
            add("au09", "Nose / mid-face tightening vs. baseline (possible grimace)", v, "0-1",
                f"AU09 proxy {v:.2f}: nostril-side distance changed from the session baseline.")
        v = m.get("au12_14_palsy_velocity")
        if v is not None and v >= s.vis_palsy_velocity and facs.get("motion_class") == "active":
            add("au12_14", "One-sided lip-corner motion", v, "IOD/s",
                f"Lip corners moved asymmetrically ({v:.3f} inter-ocular units/s) while the mouth was active.")
    hr, conf = m.get("hr_bpm"), m.get("hr_confidence") or 0.0
    if hr is not None and conf >= s.guard_hr_min_confidence:
        if hr > 100:
            add("hr", "Elevated camera pulse estimate", hr, "BPM",
                f"rPPG estimate {hr:.0f} BPM (spectral strength {conf:.2f}). Experimental, not a validated vital sign.",
                "alert" if hr > s.guard_hr_high_bpm else "notice")
        elif hr < 50:
            add("hr", "Low camera pulse estimate", hr, "BPM",
                f"rPPG estimate {hr:.0f} BPM (spectral strength {conf:.2f}). Experimental, not a validated vital sign.",
                "alert" if hr < s.guard_hr_low_bpm else "notice")
    return out
