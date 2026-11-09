"""Passive waiting-room re-triage monitor (experimental, workflow aid).

A lightweight 1 Hz poller reads the session's latest FRESH derived metrics (rPPG heart rate and the
AU04 / AU09 proxies). It does not touch video, so it adds almost no compute. Note the limit: a pulse
cannot be recovered from a camera sampled at 1 frame/s (it needs >= ~10 fps), so the video source
must keep its normal rate; only this monitor runs at 1 Hz.

Baseline = median of the first N samples. Every ``eval_interval_s`` the moving average over the last
``window_s`` is compared with the baseline; an escalation alert latches until staff acknowledge it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from app.config import Settings

HR_RISE_BPM = 25.0
PAIN_RISE_FRAC = 0.35
PAIN_BASE_FLOOR = 0.2  # a near-zero baseline would turn noise into a huge percentage
ALERT_EVENT = "Acuity_Escalation_Alert"
DISCLAIMER = ("Experimental trend monitor. A change from the registration baseline is a prompt for a nurse "
              "to look at the patient, not a measurement of deterioration and not an official triage decision.")


@dataclass
class MonitorState:
    enabled: bool = False
    registered_at: float | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=900))  # (t, hr, au04, au09)
    hr_base_pool: list = field(default_factory=list)
    au_base_pool: list = field(default_factory=list)
    base_hr: float | None = None
    base_au04: float | None = None
    base_au09: float | None = None
    last_eval_t: float | None = None
    last_sample_t: float | None = None
    last_eval: dict | None = None
    alert: bool = False
    alert_reasons: list = field(default_factory=list)
    alert_at: str | None = None


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 3) if vals else None


def start(m: MonitorState, now: float) -> None:
    m.__init__()  # type: ignore[misc]  # fresh baseline on every (re-)registration
    m.enabled, m.registered_at = True, now


def stop(m: MonitorState) -> None:
    m.enabled = False


def ingest(m: MonitorState, now: float, metrics: dict[str, Any], s: Settings) -> bool:
    """Record one sample (called ~1 Hz). Returns True if it was stored."""
    if not m.enabled:
        return False
    if m.last_sample_t is not None and now - m.last_sample_t < s.waiting_sample_interval_s * 0.9:
        return False
    hr = metrics.get("hr_bpm")
    if hr is not None and (metrics.get("hr_confidence") or 0.0) < s.guard_hr_min_confidence:
        hr = None  # an unreliable pulse is not a vital baseline
    au04, au09 = metrics.get("au04_distress"), metrics.get("au09_pain")
    if hr is None and au09 is None and au04 is None:
        return False
    m.last_sample_t = now
    m.samples.append((now, hr, au04, au09))
    if m.base_hr is None and hr is not None:
        m.hr_base_pool.append(hr)
        if len(m.hr_base_pool) >= s.waiting_baseline_samples:
            m.base_hr = float(median(m.hr_base_pool))
    if m.base_au09 is None and au09 is not None:
        m.au_base_pool.append((au04, au09))
        if len(m.au_base_pool) >= s.waiting_baseline_samples:
            m.base_au04 = float(median(a for a, _ in m.au_base_pool if a is not None) if any(a is not None for a, _ in m.au_base_pool) else 0.0)
            m.base_au09 = float(median(b for _, b in m.au_base_pool))
    return True


def current_average(m: MonitorState, now: float, s: Settings) -> dict:
    rec = [x for x in m.samples if now - x[0] <= s.waiting_window_s]
    return {"n": len(rec), "hr": _avg([x[1] for x in rec if x[1] is not None]),
            "au04": _avg([x[2] for x in rec if x[2] is not None]),
            "au09": _avg([x[3] for x in rec if x[3] is not None])}


def evaluate(m: MonitorState, now: float, s: Settings, iso_now: str) -> dict | None:
    """Run the trend check if one is due. Returns the alert payload when a NEW alert is raised."""
    if not m.enabled:
        return None
    ref = m.last_eval_t if m.last_eval_t is not None else m.registered_at
    if ref is None or now - ref < s.waiting_eval_interval_s:
        return None
    m.last_eval_t = now
    avg = current_average(m, now, s)
    reasons: list[str] = []
    status = "ok"
    if avg["n"] < s.waiting_min_samples:
        status = "insufficient_data"
    elif m.base_hr is None and m.base_au09 is None:
        status = "no_baseline"
    else:
        if m.base_hr is not None and avg["hr"] is not None and avg["hr"] - m.base_hr > HR_RISE_BPM:
            reasons.append(f"Heart rate rose {avg['hr'] - m.base_hr:.0f} BPM above baseline ({m.base_hr:.0f} -> {avg['hr']:.0f})")
        if m.base_au09 is not None and avg["au09"] is not None:
            rise = (avg["au09"] - m.base_au09) / max(m.base_au09, PAIN_BASE_FLOOR)
            if rise > PAIN_RISE_FRAC:
                reasons.append(f"AU09 pain-proxy score rose {rise * 100:.0f}% above baseline ({m.base_au09:.2f} -> {avg['au09']:.2f})")
    m.last_eval = {"at": iso_now, "status": status, "averages": avg, "reasons": reasons}
    if reasons and not m.alert:
        m.alert, m.alert_reasons, m.alert_at = True, reasons, iso_now
        return {"re_triage_alert": True, "reasons": reasons, "baseline_hr": m.base_hr, "baseline_au09": m.base_au09,
                "averages": avg, "evaluated_at": iso_now, "disclaimer": DISCLAIMER}
    return None


def acknowledge(m: MonitorState) -> None:
    m.alert, m.alert_reasons = False, []


def state(m: MonitorState, now: float, s: Settings) -> dict:
    avg = current_average(m, now, s)
    delta = None
    if m.base_hr is not None and avg["hr"] is not None:
        d = avg["hr"] - m.base_hr
        delta = f"{d:+.0f} BPM"
    if not m.enabled:
        phase = "off"
    elif m.base_hr is None and m.base_au09 is None:
        phase = "calibrating"
    else:
        phase = "monitoring"
    return {
        "is_monitored": m.enabled,
        "phase": phase,
        "acuity_escalation_alert": m.alert,
        "re_triage_alert": m.alert,
        "reasons": list(m.alert_reasons),
        "alert_at": m.alert_at,
        "baseline_hr_delta": delta,
        "baseline": {"hr_bpm": m.base_hr, "au04_distress": m.base_au04, "au09_pain": m.base_au09},
        "window_average": avg,
        "samples": len(m.samples),
        "last_evaluation": m.last_eval,
        "eval_interval_s": s.waiting_eval_interval_s,
        "disclaimer": DISCLAIMER,
    }
