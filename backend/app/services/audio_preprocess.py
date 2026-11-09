"""Pre-STT audio conditioning: RMS / SNR measurement, quality flag, spectral-gating noise reduction.

Everything here is descriptive signal processing. Nothing is invented: when the audio is too
quiet we say so (``audio_quality: "too_quiet"``) instead of guessing a transcript.
"""
from __future__ import annotations

import logging

import numpy as np

from app.config import Settings

log = logging.getLogger("visionpath.audio")

FRAME_MS = 20


def _frame_power(x: np.ndarray, sr: int) -> np.ndarray:
    n = max(int(sr * FRAME_MS / 1000), 1)
    k = len(x) // n
    if k == 0:
        return np.array([float(np.mean(x**2)) if len(x) else 0.0])
    return np.mean(x[: k * n].reshape(k, n) ** 2, axis=1)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2))) if len(x) else 0.0


def estimate_snr_db(x: np.ndarray, sr: int) -> float | None:
    """Rough SNR: power of the loudest 30 % of frames over the power of the quietest 10 %.

    Needs some quiet frames to estimate the noise floor; returns None when it cannot.
    """
    p = _frame_power(x, sr)
    if len(p) < 10:
        return None
    noise = float(np.percentile(p, 10))
    sig = float(np.mean(p[p >= np.percentile(p, 70)]))
    if noise <= 1e-12:
        return 60.0 if sig > 1e-9 else None
    return float(np.clip(10 * np.log10(max(sig, 1e-12) / noise), 0.0, 60.0))


def _noise_clip(x: np.ndarray, sr: int) -> np.ndarray | None:
    """Concatenate the quietest frames (>= 0.2 s) as the noise profile for stationary gating."""
    n = max(int(sr * FRAME_MS / 1000), 1)
    k = len(x) // n
    if k < 20:
        return None
    frames = x[: k * n].reshape(k, n)
    pw = np.mean(frames**2, axis=1)
    quiet = np.argsort(pw)[: max(int(k * 0.2), 10)]
    return frames[np.sort(quiet)].reshape(-1)


def denoise(x: np.ndarray, sr: int) -> np.ndarray:
    import noisereduce as nr  # lazy: optional dependency

    clip = _noise_clip(x, sr)
    if clip is not None:
        # Narrow frequency smoothing: the 500 Hz default smears harmonic speech into the mask and
        # attenuated ~50 % of its energy in testing.
        y = nr.reduce_noise(y=x, sr=sr, y_noise=clip, stationary=True, prop_decrease=0.85,
                            freq_mask_smooth_hz=100)
    else:
        y = nr.reduce_noise(y=x, sr=sr, stationary=False, prop_decrease=0.75, freq_mask_smooth_hz=100)
    return np.asarray(y, dtype=np.float32)


def voiced_fraction(x: np.ndarray, sr: int) -> float:
    """Share of 40 ms frames (that are clearly above the quietest frames) with a reliable pitch."""
    from app.services.audio_processor import estimate_f0

    n, hop = int(sr * 0.04), int(sr * 0.02)
    if len(x) < n * 3:
        return 0.0
    frames = [x[i:i + n] for i in range(0, len(x) - n, hop)]
    db = np.array([20 * np.log10(np.sqrt(np.mean(f**2)) + 1e-9) for f in frames])
    gate = min(np.percentile(db, 20) + 6.0, float(db.max()) - 3.0)  # also works when there is no silence
    cand = [f for f, d in zip(frames, db, strict=True) if d > gate]
    if not cand:
        return 0.0
    hits = sum(1 for f in cand if estimate_f0(f, sr)[0] is not None)
    return hits / len(frames)


def prepare(x: np.ndarray, sr: int, s: Settings) -> tuple[np.ndarray, dict]:
    """Return (audio for STT, status). ``status['audio_quality']`` is good | noisy | too_quiet."""
    level = rms(x)
    snr = estimate_snr_db(x, sr)
    clipping = float(np.mean(np.abs(x) >= 0.999)) if len(x) else 0.0
    status: dict = {
        "rms": round(level, 5),
        "snr_db": round(snr, 1) if snr is not None else None,
        "noise_filter_applied": False,
        "noise_filter_error": None,
        "audio_quality": "good",
        "clipping_fraction": round(clipping, 5),
        "voiced_fraction": None,
    }
    status["gain_db"] = 0.0
    status["skip_stt"] = False
    out = x
    vf = voiced_fraction(x, sr)
    status["voiced_fraction"] = round(vf, 3)
    if vf < s.stt_min_voiced_fraction:
        # Broadband noise / non-speech: transcribing it only invites hallucinated sentences.
        status["audio_quality"] = "no_speech" if level >= s.stt_min_rms else "too_quiet"
        status["skip_stt"] = True
        return x, status
    if level < s.stt_min_rms:
        status["audio_quality"] = "too_quiet"  # always reported ...
        if level < s.stt_skip_rms:
            status["skip_stt"] = True  # ... and only a near-silent signal is not transcribed at all
            return x, status
        gain = min(s.stt_target_rms / level, 8.0)  # ... quiet speech gets a boost (max +18 dB)
        out = np.clip(x * gain, -1.0, 1.0).astype(np.float32)
        status["gain_db"] = round(20 * float(np.log10(gain)), 1)
    if s.stt_denoise and (snr is None or snr < s.stt_denoise_below_snr_db):
        out_before = out
        try:
            cleaned = denoise(out, sr)
            if rms(cleaned) < 0.6 * rms(out):
                # Over-suppression (e.g. continuous speech mistaken for the noise profile): keep the original.
                status["noise_filter_error"] = "over_suppressed"
                out = out_before
            else:
                out = cleaned
                status["noise_filter_applied"] = True
        except Exception as exc:  # optional dependency / numerical failure: keep raw audio, say so
            status["noise_filter_error"] = type(exc).__name__
            log.warning("Noise reduction unavailable: %s", type(exc).__name__)
            out = out_before
    if snr is not None and snr < s.stt_noisy_snr_db and status["audio_quality"] == "good":
        status["audio_quality"] = "noisy"
    return out, status
