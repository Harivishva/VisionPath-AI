"""Acoustic DSP over a rolling window: MFCC / STFT energy, speech-to-pause ratio, F0 variation,
breath-like burst detection.

These are descriptive acoustic observations. A burst of broadband noise inside a pause is NOT a
validated detector of gasping or respiratory distress, and the F0 measure is a frame-to-frame
pitch variation, not clinical jitter. Output requires clinical interpretation.
"""
from __future__ import annotations

import numpy as np

from app.config import Settings
from app.services.audio_processor import estimate_f0

FRAME = 640  # 40 ms at 16 kHz
HOP = 320
FRAME_S = HOP / 16000.0


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(start, end_exclusive)] of True runs."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    out = mask.copy()
    r = _runs(mask)
    for (_a, b), (c, _d) in zip(r[:-1], r[1:], strict=True):
        if c - b <= max_gap:
            out[b:c] = True
    return out


def analyze(x: np.ndarray, sr: int, s: Settings, noise_floor_dbfs: float | None = None) -> dict:
    """Analyse one rolling window (float32 mono, 16 kHz).

    ``noise_floor_dbfs`` is the session's tracked noise floor; it keeps the floor honest when the
    window contains almost no silence (the window's own 10th percentile would then be speech).
    """
    base = {"state": "not_measured", "reason": None, "window_s": round(len(x) / sr, 1),
            "speech_to_breath_ratio": None, "vocal_jitter": None, "gasping_detected": None,
            "pitch_instability_hz": None,
            "speech_s": None, "pause_s": None, "breath_events": None,
            "mfcc_mean": None, "stft_energy_db": None, "experimental": True}
    if sr != 16000:
        return {**base, "state": "unavailable", "reason": f"Unsupported sample rate {sr}"}
    if len(x) < FRAME * 10:
        return {**base, "reason": "Not enough audio yet"}

    n = (len(x) - FRAME) // HOP + 1
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx]
    win = np.hanning(FRAME)
    power = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2 + 1e-12
    rms = np.sqrt(np.mean(frames**2, axis=1))
    dbfs = 20 * np.log10(rms + 1e-9)
    floor = float(np.percentile(dbfs, 10))
    if noise_floor_dbfs is not None:
        floor = min(floor, float(noise_floor_dbfs))
    thr_voice = max(s.vad_min_dbfs, floor + 6.0)

    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    flat = np.exp(np.mean(np.log(power), axis=1)) / np.mean(power, axis=1)
    hf_ratio = power[:, freqs >= 1500].sum(axis=1) / power.sum(axis=1)

    f0 = np.full(n, np.nan)
    voiced = np.zeros(n, dtype=bool)
    for i in range(n):
        if dbfs[i] < thr_voice:
            continue
        f, per = estimate_f0(frames[i], sr)
        if f is not None and per >= 0.5:
            voiced[i], f0[i] = True, f

    # Speech = voiced activity with short gaps (<0.2 s, e.g. consonants) bridged.
    speech_mask = _close_gaps(voiced, max_gap=10)
    sruns = _runs(speech_mask)
    speech_s = float(speech_mask.sum()) * FRAME_S

    # Pauses = gaps between consecutive speech runs lasting 0.2 - 3 s.
    pauses = [(b, c) for (_a, b), (c, _d) in zip(sruns[:-1], sruns[1:], strict=True)
              if 0.2 <= (c - b) * FRAME_S <= 3.0]
    pause_s = sum((c - b) for b, c in pauses) * FRAME_S

    # Breath-like events: noisy, unvoiced, above the local noise floor, inside a pause.
    noisy = (dbfs >= floor + 6.0) & (~voiced) & (flat >= 0.2) & (hf_ratio >= 0.35)
    events = 0
    for b, c in pauses:
        for ra, rb in _runs(noisy[b:c]):
            if 0.2 <= (rb - ra) * FRAME_S <= 1.5:
                events += 1

    # Frame-to-frame F0 variation over consecutive voiced frames.
    jitter = None
    pairs = [(f0[i - 1], f0[i]) for i in range(1, n) if voiced[i] and voiced[i - 1]]
    if len(pairs) >= 10:
        a = np.array(pairs)
        jitter = float(np.mean(np.abs(a[:, 1] - a[:, 0])) / np.mean(a))

    pitch_sd = None
    f0v = f0[voiced]
    if len(f0v) >= 10:
        pitch_sd = float(np.std(f0v))

    try:
        import librosa

        mf = librosa.feature.mfcc(y=x, sr=sr, n_mfcc=13, n_fft=512, hop_length=256)
        mfcc_mean = [round(float(v), 2) for v in mf.mean(axis=1)]
    except Exception:  # MFCCs are descriptive extras; never block the rest
        mfcc_mean = None
    tot = power.sum()
    band = lambda lo, hi: round(float(10 * np.log10(power[:, (freqs >= lo) & (freqs < hi)].sum() / tot + 1e-12)), 1)  # noqa: E731
    stft = {"low_0_500hz": band(0, 500), "mid_500_2000hz": band(500, 2000), "high_2000hz_plus": band(2000, 8001)}

    valid = speech_s >= s.dsp_min_speech_s and len(sruns) >= 1
    ratio = None
    if valid:
        ratio = round(speech_s / (speech_s + pause_s), 3) if (speech_s + pause_s) > 0 else None
    return {
        **base,
        "state": "pending_review" if valid else "low_quality",
        "reason": None if valid else f"Less than {s.dsp_min_speech_s:.0f} s of voiced speech in the window",
        "speech_to_breath_ratio": ratio,
        "vocal_jitter": round(jitter, 4) if jitter is not None else None,
        "pitch_instability_hz": round(pitch_sd, 1) if (pitch_sd is not None and valid) else None,
        "gasping_detected": bool(events >= s.dsp_gasp_min_events) if valid else None,
        "speech_s": round(speech_s, 2), "pause_s": round(pause_s, 2), "breath_events": events,
        "mfcc_mean": mfcc_mean, "stft_energy_db": stft,
    }
