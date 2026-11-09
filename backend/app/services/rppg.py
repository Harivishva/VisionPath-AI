"""Experimental remote photoplethysmography (rPPG) with the POS algorithm.

EXPERIMENTAL. A webcam-derived pulse rate is not a validated vital sign: it is sensitive to
lighting, motion, skin tone, camera auto-exposure and JPEG compression. The estimate is reported
only when enough contiguous, quality-gated frames exist and the spectral peak is dominant.
``confidence`` is the share of in-band spectral power at the peak (0-1); it is NOT a probability.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.signal import butter, filtfilt

BAND_HZ = (0.75, 3.33)  # 45 - 200 BPM
TARGET_FS = 30.0
MAX_GAP_S = 1.0
FOREHEAD = [67, 109, 10, 338, 297, 336, 9, 107]  # includes landmarks 10, 67, 297, 338
CHEEK_CENTERS = {"cheek_left": 50, "cheek_right": 280}  # upper cheeks, below the eyes
EYE_OUTER = (33, 263)


def roi_polygons(pts: np.ndarray) -> dict[str, np.ndarray]:
    """Pixel polygons for the forehead and both upper cheeks from MediaPipe landmarks (px)."""
    iod = float(np.hypot(*(pts[EYE_OUTER[1]] - pts[EYE_OUTER[0]]))) or 1.0
    r = 0.16 * iod
    polys = {"forehead": pts[FOREHEAD]}
    for name, i in CHEEK_CENTERS.items():
        c = pts[i]
        polys[name] = np.array([[c[0] - r, c[1] - r], [c[0] + r, c[1] - r],
                                [c[0] + r, c[1] + r], [c[0] - r, c[1] + r]])
    return polys


def mean_rgb(img_bgr: np.ndarray, polys: dict[str, np.ndarray]) -> np.ndarray | None:
    """Spatial mean RGB over the union of the ROI polygons, or None if the ROI is empty."""
    h, w = img_bgr.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for p in polys.values():
        cv2.fillPoly(mask, [np.round(p).astype(np.int32)], 255)
    if int(mask.sum()) == 0:
        return None
    b, g, r, _ = cv2.mean(img_bgr, mask=mask)
    return np.array([r, g, b], dtype=np.float64)


def pos_pulse(rgb: np.ndarray, fs: float, win_s: float = 1.6) -> np.ndarray:
    """Plane-Orthogonal-to-Skin: S = P . C_n over sliding windows, overlap-added (rgb: N x 3)."""
    n = len(rgb)
    ln = max(int(round(win_s * fs)), 8)
    proj = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])
    h = np.zeros(n)
    for t in range(0, n - ln + 1):
        c = rgb[t:t + ln].T  # 3 x l
        mu = c.mean(axis=1, keepdims=True)
        if np.any(mu <= 1e-6):
            continue
        s = proj @ (c / mu)
        sd1, sd2 = float(np.std(s[0])), float(np.std(s[1]))
        if sd2 < 1e-9:
            continue
        p = s[0] + (sd1 / sd2) * s[1]
        h[t:t + ln] += p - p.mean()
    return h


def estimate_rate(t: np.ndarray, rgb: np.ndarray, min_conf: float) -> dict:
    """Heart-rate estimate from timestamped mean-RGB samples. Returns a dict with hr/confidence."""
    span = float(t[-1] - t[0])
    fs_eff = (len(t) - 1) / span if span > 0 else 0.0
    grid = np.arange(t[0], t[-1], 1.0 / TARGET_FS)
    if len(grid) < int(TARGET_FS * 3):
        return {"hr": None, "confidence": None, "fs": fs_eff, "reason": "Not enough contiguous frames"}
    u = np.stack([np.interp(grid, t, rgb[:, k]) for k in range(3)], axis=1)
    pulse = pos_pulse(u, TARGET_FS)
    b, a = butter(3, [BAND_HZ[0], BAND_HZ[1]], btype="bandpass", fs=TARGET_FS)
    if len(pulse) <= 3 * max(len(a), len(b)):
        return {"hr": None, "confidence": None, "fs": fs_eff, "reason": "Window too short to filter"}
    sig = filtfilt(b, a, pulse) * np.hanning(len(pulse))
    nfft = 8192
    spec = np.abs(np.fft.rfft(sig, nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1.0 / TARGET_FS)
    band = (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])
    if not band.any() or float(spec[band].sum()) <= 1e-12:
        return {"hr": None, "confidence": None, "fs": fs_eff, "reason": "No pulse-band signal"}
    fb, sb = freqs[band], spec[band]
    peak = float(fb[int(np.argmax(sb))])
    near = np.abs(fb - peak) <= 0.12
    harm = (np.abs(fb - 2 * peak) <= 0.12)
    conf = float((sb[near].sum() + sb[harm].sum()) / sb.sum())
    conf = float(min(conf, 1.0))
    if conf < min_conf:
        return {"hr": None, "confidence": round(conf, 2), "fs": fs_eff,
                "reason": "Pulse signal not distinct enough to report a rate"}
    return {"hr": round(peak * 60.0, 1), "confidence": round(conf, 2), "fs": fs_eff, "reason": None}


@dataclass
class RppgState:
    """Rolling buffer of ROI-mean RGB samples (no image data is kept)."""

    window_s: float = 5.0
    min_fps: float = 10.0
    min_conf: float = 0.35
    t: deque = field(default_factory=lambda: deque(maxlen=900))
    rgb: deque = field(default_factory=lambda: deque(maxlen=900))
    last: dict | None = None
    last_est_t: float = 0.0
    last_add_mono: float = 0.0

    def reset(self) -> None:
        self.t.clear()
        self.rgb.clear()
        self.last = None
        self.last_est_t = 0.0

    def add(self, ts: float, rgb: np.ndarray) -> None:
        if self.t and ts <= self.t[-1]:
            return  # out-of-order / duplicate frame
        if self.t and ts - self.t[-1] > MAX_GAP_S:
            self.reset()  # contiguity broken: pulse needs an unbroken window
        self.t.append(ts)
        self.rgb.append(rgb)
        self.last_add_mono = time.monotonic()
        while self.t and self.t[-1] - self.t[0] > self.window_s + 0.5:
            self.t.popleft()
            self.rgb.popleft()

    def update(self) -> dict:
        """Re-estimate at most every 0.5 s of frame time; always returns a state dict."""
        span = (self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0
        if span < self.window_s * 0.98:
            self.last = {"state": "not_measured", "heart_rate_bpm": None, "confidence": None,
                         "window_s": round(span, 1), "effective_fps": None,
                         "reason": f"Collecting {span:.1f}/{self.window_s:.0f} s of steady face frames"}
            return self.last
        if self.t[-1] - self.last_est_t < 0.5 and self.last is not None:
            return self.last
        self.last_est_t = self.t[-1]
        t, rgb = np.array(self.t), np.array(self.rgb)
        fs = (len(t) - 1) / span
        if fs < self.min_fps:
            self.last = {"state": "unavailable", "heart_rate_bpm": None, "confidence": None,
                         "window_s": round(span, 1), "effective_fps": round(fs, 1),
                         "reason": f"Frame rate {fs:.1f} fps is below the {self.min_fps:.0f} fps needed"}
            return self.last
        est = estimate_rate(t, rgb, self.min_conf)
        if est["hr"] is None:
            state = "low_quality"
        else:
            state = "pending_review"  # machine estimate, needs clinician interpretation
        self.last = {"state": state, "heart_rate_bpm": est["hr"], "confidence": est["confidence"],
                     "window_s": round(span, 1), "effective_fps": round(est["fs"], 1),
                     "reason": est["reason"]}
        return self.last

    def current(self, max_age_s: float = 2.0) -> dict | None:
        """Latest estimate, or an explicit 'unavailable' when no valid face frames arrive."""
        if self.last is None:
            return None
        if time.monotonic() - self.last_add_mono > max_age_s:
            return {"state": "unavailable", "heart_rate_bpm": None, "confidence": None,
                    "window_s": 0.0, "effective_fps": None,
                    "reason": "No steady, quality-gated face frames"}
        return self.last
