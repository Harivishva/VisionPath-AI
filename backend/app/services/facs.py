"""Experimental facial action-unit PROXIES with frame-over-frame velocity (rolling 3 s buffer).

These are geometric proxies computed from MediaPipe landmarks, NOT FACS coding by a certified
coder and NOT a pain, distress or stroke measurement. Scores are relative to a per-session
baseline (the first accepted frames), so a face that starts out distressed or asymmetric is
treated as the baseline. Velocity separates a static structural difference from active motion.

Landmarks (MediaPipe 478-point mesh, image sides):
  AU04 proxy  brow lowering   dist(brow 70, inner canthus 133) and dist(brow 300, inner canthus 362)
  AU09 proxy  nose wrinkling  dist(nostril sides 198, 420)
  AU12/14     lip corners 61 / 291, compared as mirror images in a roll-compensated face frame
All distances are 3D and divided by the 3D inter-ocular distance.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

BROW_L, BROW_R, CANTHUS_L, CANTHUS_R = 70, 300, 133, 362
NOSE_L, NOSE_R = 198, 420
LIP_L, LIP_R = 61, 291
EYE_OUTER_L, EYE_OUTER_R = 33, 263

BUFFER_FRAMES = 90  # 3 s at 30 FPS
WINDOW_S = 3.0
SCORE_WINDOW_S = 1.0  # scores/velocities average the most recent second
MAX_GAP_S = 1.0  # a longer gap breaks continuity (the buffer restarts, the baseline stays)
MIN_DT_S = 0.005
CALIB_FRAMES = 20
BROW_FULL_SCALE = 0.12  # a 12 % shorter brow-to-canthus distance scores 1.0
NOSE_FULL_SCALE = 0.08  # an 8 % change of the nostril-side distance scores 1.0
ACTIVE_LIP_SPEED = 0.05  # inter-ocular units / s: above this the lips are treated as moving


def frame_features(p: np.ndarray) -> dict:
    """Per-frame features from 3D landmark points (x, y in px; z in px-equivalent units)."""
    iod = float(np.linalg.norm(p[EYE_OUTER_R] - p[EYE_OUTER_L])) or 1.0
    brow = 0.5 * (np.linalg.norm(p[BROW_L] - p[CANTHUS_L]) + np.linalg.norm(p[BROW_R] - p[CANTHUS_R])) / iod
    nose = float(np.linalg.norm(p[NOSE_L] - p[NOSE_R])) / iod
    d = p[EYE_OUTER_R, :2] - p[EYE_OUTER_L, :2]
    ang = np.arctan2(d[1], d[0])
    c, s = np.cos(-ang), np.sin(-ang)
    rot = np.array([[c, -s], [s, c]])

    def face_frame(i: int) -> np.ndarray:
        xy = rot @ (p[i, :2] - p[EYE_OUTER_L, :2])
        return np.array([xy[0], xy[1], p[i, 2] - p[EYE_OUTER_L, 2]]) / iod

    return {"brow": float(brow), "nose": nose, "lip_l": face_frame(LIP_L), "lip_r": face_frame(LIP_R)}


@dataclass
class FacsState:
    buf: deque = field(default_factory=lambda: deque(maxlen=BUFFER_FRAMES))  # (t, features)
    calib: list = field(default_factory=list)
    base_brow: float | None = None
    base_nose: float | None = None
    n_frames: int = 0

    def reset(self) -> None:
        self.buf.clear()
        self.calib.clear()
        self.base_brow = self.base_nose = None
        self.n_frames = 0

    def add(self, t: float, pts3: np.ndarray) -> None:
        if self.buf and t - self.buf[-1][0] > MAX_GAP_S:
            self.buf.clear()
        if self.buf and t - self.buf[-1][0] < MIN_DT_S:
            return  # duplicate / out-of-order timestamp: velocity would be meaningless
        f = frame_features(pts3)
        self.buf.append((t, f))
        self.n_frames += 1
        if self.base_brow is None:
            self.calib.append((f["brow"], f["nose"]))
            if len(self.calib) >= CALIB_FRAMES:
                a = np.array(self.calib)
                self.base_brow, self.base_nose = float(np.median(a[:, 0])), float(np.median(a[:, 1]))

    def _recent(self) -> list:
        if not self.buf:
            return []
        t_end = self.buf[-1][0]
        return [e for e in self.buf if t_end - e[0] <= SCORE_WINDOW_S]

    def current(self) -> dict:
        base = {"au04_distress": None, "au09_pain": None, "au12_14_palsy_velocity": None,
                "au04_velocity": None, "au09_velocity": None, "lip_motion": None, "motion_class": None,
                "buffer_frames": len(self.buf), "window_s": WINDOW_S, "experimental": True}
        if not self.buf:
            return {**base, "state": "not_measured", "reason": "No quality-gated face frames yet"}
        if self.base_brow is None:
            return {**base, "state": "calibrating",
                    "reason": f"Capturing the per-session baseline ({len(self.calib)}/{CALIB_FRAMES} frames)"}
        rec = self._recent()
        brow = float(np.mean([f["brow"] for _, f in rec]))
        nose = float(np.mean([f["nose"] for _, f in rec]))
        out = {
            **base, "state": "pending_review", "reason": None,
            "au04_distress": round(float(np.clip((self.base_brow - brow) / self.base_brow / BROW_FULL_SCALE, 0, 1)), 3),
            "au09_pain": round(float(np.clip(abs(nose - self.base_nose) / self.base_nose / NOSE_FULL_SCALE, 0, 1)), 3),
        }
        asym, speed, dbrow, dnose = [], [], [], []
        for (t0, a), (t1, b) in zip(rec[:-1], rec[1:], strict=True):
            dt = t1 - t0
            if dt < MIN_DT_S:
                continue
            vl, vr = (b["lip_l"] - a["lip_l"]) / dt, (b["lip_r"] - a["lip_r"]) / dt  # v = delta d / delta t
            # Mirror symmetry: x velocities should be opposite, y and z equal. The residual is the asymmetry.
            asym.append(float(np.linalg.norm([vl[0] + vr[0], vl[1] - vr[1], vl[2] - vr[2]])))
            speed.append(0.5 * (float(np.linalg.norm(vl)) + float(np.linalg.norm(vr))))
            dbrow.append((b["brow"] - a["brow"]) / dt)
            dnose.append((b["nose"] - a["nose"]) / dt)
        if asym:
            out["au12_14_palsy_velocity"] = round(float(np.mean(asym)), 4)
            out["lip_motion"] = round(float(np.mean(speed)), 4)
            out["motion_class"] = "active" if out["lip_motion"] >= ACTIVE_LIP_SPEED else "static"
            out["au04_velocity"] = round(float(np.mean(dbrow)), 4)
            out["au09_velocity"] = round(float(np.mean(dnose)), 4)
        return out
