"""Real computer-vision pipeline: OpenCV decode + MediaPipe FaceLandmarker.

Outputs descriptive, quality-gated facial geometry in normalised units. These are
experimental technical signals. They are NOT diagnostic and no probability is derived.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.config import Settings
from app.schemas.models import DataState, VisionMeasurement, VisionObservation
from app.services.facs import FacsState
from app.services.rppg import RppgState, mean_rgb, roi_polygons

log = logging.getLogger("visionpath.vision")


def asymmetry_pct(m: VisionMeasurement) -> float:
    """Experimental: 100 x the larger of the mouth-corner and brow offsets (inter-ocular units).

    Eye-opening is excluded because blinks and squints make it too noisy. Not a probability.
    """
    return round(100.0 * max(abs(m.mouth_corner_vertical_offset), abs(m.brow_height_offset)), 1)

# MediaPipe face-mesh landmark indices. "image_left"/"image_right" describe the side of
# the *image*, not the patient's anatomical side (frames are not mirrored server-side).
IDX = {
    "eye_outer_left": 33,
    "eye_outer_right": 263,
    "mouth_left": 61,
    "mouth_right": 291,
    "brow_left": 105,
    "brow_right": 334,
    "lid_up_left": 159,
    "lid_low_left": 145,
    "lid_up_right": 386,
    "lid_low_right": 374,
}
OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377,
        152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
EYE_L = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
EYE_R = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]
LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]


def _wrap_deg(a: float) -> float:
    while a > 90:
        a -= 180
    while a <= -90:
        a += 180
    return a


@dataclass
class VisionSessionState:
    """Per-session smoothing + statistics. Holds no image data."""

    ema: np.ndarray | None = None
    last_accept_t: float = 0.0
    frames_received: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    face_frames: int = 0
    accepted_frames: int = 0
    low_quality_frames: int = 0
    sum_meas: np.ndarray = field(default_factory=lambda: np.zeros(3))
    proc_times: deque = field(default_factory=lambda: deque(maxlen=30))
    latencies: deque = field(default_factory=lambda: deque(maxlen=30))
    last_observation_at: str | None = None
    rppg: RppgState = field(default_factory=RppgState)
    facs: FacsState = field(default_factory=FacsState)

    def fps(self) -> float:
        if len(self.proc_times) < 2:
            return 0.0
        span = self.proc_times[-1] - self.proc_times[0]
        return round((len(self.proc_times) - 1) / span, 2) if span > 0 else 0.0

    def mean_latency_ms(self) -> float:
        return round(sum(self.latencies) / len(self.latencies), 1) if self.latencies else 0.0

    def summary(self) -> dict:
        n = self.accepted_frames
        mean = (self.sum_meas / n).round(4).tolist() if n else None
        if self.frames_processed == 0:
            state = DataState.NOT_MEASURED
        elif n == 0 and self.face_frames == 0:
            state = DataState.NOT_DETECTED
        elif n == 0:
            state = DataState.LOW_QUALITY
        else:
            state = DataState.PENDING_REVIEW  # measurements exist but need clinician interpretation
        return {
            "state": state.value,
            "frames_received": self.frames_received,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "face_frames": self.face_frames,
            "accepted_frames": n,
            "low_quality_frames": self.low_quality_frames,
            "fps": self.fps(),
            "mean_latency_ms": self.mean_latency_ms(),
            "mean_measurement": (
                {
                    "mouth_corner_vertical_offset": mean[0],
                    "brow_height_offset": mean[1],
                    "eye_opening_offset": mean[2],
                }
                if mean
                else None
            ),
        }


class VisionProcessor:
    """Owns one shared FaceLandmarker instance (created once, reused, lock-protected)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._landmarker = None
        self._lock = threading.Lock()
        self.ready = False
        self.error: str | None = None
        self._init_model()

    def _init_model(self) -> None:
        try:
            import os

            if not os.path.exists(self.settings.face_model_path):
                raise FileNotFoundError(
                    "face_landmarker.task not found. Run backend/scripts/download_models.py"
                )
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            opts = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=self.settings.face_model_path),
                running_mode=vision.RunningMode.IMAGE,
                num_faces=1,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=True,
            )
            self._landmarker = vision.FaceLandmarker.create_from_options(opts)
            self._mp = mp
            self.ready = True
        except Exception as exc:  # model/library unavailable -> truthful status
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready = False
            log.warning("Vision model unavailable: %s", self.error)

    def close(self) -> None:
        with self._lock:
            if self._landmarker is not None:
                try:
                    self._landmarker.close()
                finally:
                    self._landmarker = None
                    self.ready = False

    # ------------------------------------------------------------------
    def process_jpeg(
        self, data: bytes, state: VisionSessionState, frame_ts: float | None = None
    ) -> VisionObservation:
        t0 = time.perf_counter()
        state.frames_processed += 1
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return self._finish(
                state, t0, VisionObservation(frame_ts=frame_ts, state=DataState.UNAVAILABLE,
                                             reason="Frame could not be decoded as an image")
            )
        return self.process_bgr(img, state, frame_ts, t0)

    def process_bgr(
        self, img: np.ndarray, state: VisionSessionState, frame_ts: float | None = None,
        t0: float | None = None,
    ) -> VisionObservation:
        if t0 is None:  # called directly (not via process_jpeg)
            state.frames_processed += 1
            t0 = time.perf_counter()
        quality = self._quality(img)
        if not self.ready:
            return self._finish(
                state, t0,
                VisionObservation(frame_ts=frame_ts, state=DataState.UNAVAILABLE,
                                  reason=f"Vision model not ready: {self.error}", quality=quality),
            )

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        with self._lock:
            result = self._landmarker.detect(mp_img)

        if not result.face_landmarks:
            return self._finish(
                state, t0,
                VisionObservation(frame_ts=frame_ts, state=DataState.NOT_DETECTED, face_detected=False,
                                  reason="No face detected; visual analysis unavailable for this frame",
                                  quality=quality),
            )

        state.face_frames += 1
        lm = result.face_landmarks[0]
        h, w = img.shape[:2]
        pts = np.array([[p.x * w, p.y * h] for p in lm], dtype=np.float64)
        inside = float(np.mean([(0.0 <= p.x <= 1.0) and (0.0 <= p.y <= 1.0) for p in lm]))
        quality["face_visibility"] = round(inside, 3)
        bbox = (pts.min(axis=0), pts.max(axis=0))
        quality["face_area_ratio"] = round(
            float((bbox[1][0] - bbox[0][0]) * (bbox[1][1] - bbox[0][1]) / (w * h)), 3
        )

        pose = None
        if result.facial_transformation_matrixes:
            m = np.array(result.facial_transformation_matrixes[0])[:3, :3]
            sy = math.hypot(m[0, 0], m[1, 0])
            yaw = math.degrees(math.atan2(-m[2, 0], sy))
            pitch = _wrap_deg(math.degrees(math.atan2(m[2, 1], m[2, 2])))
            roll = math.degrees(math.atan2(m[1, 0], m[0, 0]))
            pose = {"yaw": round(yaw, 1), "pitch": round(pitch, 1), "roll": round(_wrap_deg(roll), 1)}

        overlay = {
            "oval": self._xy(lm, OVAL),
            "eye_left": self._xy(lm, EYE_L),
            "eye_right": self._xy(lm, EYE_R),
            "lips": self._xy(lm, LIPS),
            # Asymmetry vectors: line between left/right mouth corners and between left/right brows.
            "asym_mouth": self._xy(lm, [IDX["mouth_left"], IDX["mouth_right"]]),
            "asym_brow": self._xy(lm, [IDX["brow_left"], IDX["brow_right"]]),
        }

        reason = self._gate(quality, pose)
        if reason:
            state.low_quality_frames += 1
            return self._finish(
                state, t0,
                VisionObservation(frame_ts=frame_ts, state=DataState.LOW_QUALITY, face_detected=True,
                                  reason=reason, quality=quality, head_pose_deg=pose, overlay=overlay),
            )

        meas = self._geometry(pts)
        vec = np.array([meas.mouth_corner_vertical_offset, meas.brow_height_offset,
                        meas.eye_opening_offset])
        polys = roi_polygons(pts)
        overlay.update({f"roi_{k}": [[round(x / w, 4), round(y / h, 4)] for x, y in p]
                        for k, p in polys.items()})
        rgb = mean_rgb(img, polys)
        if rgb is not None:
            state.rppg.window_s = self.settings.rppg_window_s
            state.rppg.min_fps = self.settings.rppg_min_fps
            state.rppg.min_conf = self.settings.rppg_min_confidence
            ts = frame_ts / 1000.0 if frame_ts is not None and math.isfinite(frame_ts) else time.monotonic()
            state.rppg.add(ts, rgb)
            state.rppg.update()
        ts_f = frame_ts / 1000.0 if frame_ts is not None and math.isfinite(frame_ts) else time.monotonic()
        pts3 = np.array([[p.x * w, p.y * h, p.z * w] for p in lm], dtype=np.float64)
        state.facs.add(ts_f, pts3)
        now = time.monotonic()
        a = self.settings.smoothing_alpha
        if state.ema is None or now - state.last_accept_t > 3.0:
            state.ema = vec
        else:
            state.ema = a * vec + (1 - a) * state.ema
        state.last_accept_t = now
        state.accepted_frames += 1
        state.sum_meas += vec
        smoothed = VisionMeasurement(
            mouth_corner_vertical_offset=round(float(state.ema[0]), 4),
            brow_height_offset=round(float(state.ema[1]), 4),
            eye_opening_offset=round(float(state.ema[2]), 4),
        )
        return self._finish(
            state, t0,
            VisionObservation(frame_ts=frame_ts, state=DataState.PENDING_REVIEW, face_detected=True,
                              quality=quality, head_pose_deg=pose, measurement=meas, smoothed=smoothed,
                              asymmetry_pct=asymmetry_pct(smoothed), overlay=overlay),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _xy(lm, idx: list[int]) -> list[list[float]]:
        return [[round(lm[i].x, 4), round(lm[i].y, 4)] for i in idx]

    def _quality(self, img: np.ndarray) -> dict[str, float]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        scale = 320.0 / w if w > 320 else 1.0
        small = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale != 1.0 else gray
        return {
            "brightness": round(float(gray.mean()), 1),
            "contrast": round(float(gray.std()), 1),
            "sharpness": round(float(cv2.Laplacian(small, cv2.CV_64F).var()), 1),
            "width": float(w),
            "height": float(h),
        }

    def _gate(self, q: dict[str, float], pose: dict[str, float] | None) -> str | None:
        s = self.settings
        if q["brightness"] < s.min_brightness:
            return "Image too dark for reliable landmark geometry"
        if q["brightness"] > s.max_brightness:
            return "Image overexposed for reliable landmark geometry"
        if q["sharpness"] < s.min_sharpness:
            return "Image too blurry for reliable landmark geometry"
        if q.get("face_visibility", 1.0) < 0.98:
            return "Face partly outside the frame"
        if q.get("face_area_ratio", 1.0) < 0.04:
            return "Face too small in frame; move closer"
        if pose is None:
            return "Head pose unavailable; geometry not computed"
        if abs(pose["yaw"]) > s.max_head_yaw_deg or abs(pose["pitch"]) > s.max_head_pitch_deg:
            return "Head rotated too far from camera for symmetric geometry"
        if abs(pose["roll"]) > s.max_head_roll_deg:
            return "Head tilted too far sideways for reliable landmark geometry"
        return None

    @staticmethod
    def _geometry(p: np.ndarray) -> VisionMeasurement:
        """Roll-compensated, inter-ocular-normalised left/right geometric differences.

        Positive values mean the image-right feature is *higher / more open* than image-left.
        """
        l_eye, r_eye = p[IDX["eye_outer_left"]], p[IDX["eye_outer_right"]]
        d = r_eye - l_eye
        iod = float(np.hypot(*d)) or 1.0
        ang = math.atan2(d[1], d[0])
        c, s = math.cos(-ang), math.sin(-ang)
        rot = np.array([[c, -s], [s, c]])
        q = (p - l_eye) @ rot.T  # y grows downward

        def y(name: str) -> float:
            return float(q[IDX[name]][1])

        mouth = (y("mouth_left") - y("mouth_right")) / iod  # >0: right corner higher
        brow_l = y("lid_up_left") - y("brow_left")
        brow_r = y("lid_up_right") - y("brow_right")
        brow = (brow_r - brow_l) / iod  # >0: right brow farther above its eye
        open_l = y("lid_low_left") - y("lid_up_left")
        open_r = y("lid_low_right") - y("lid_up_right")
        eye = (open_r - open_l) / iod
        return VisionMeasurement(
            mouth_corner_vertical_offset=round(mouth, 4),
            brow_height_offset=round(brow, 4),
            eye_opening_offset=round(eye, 4),
        )

    def _finish(self, state: VisionSessionState, t0: float, obs: VisionObservation) -> VisionObservation:
        lat = (time.perf_counter() - t0) * 1000.0
        obs.latency_ms = round(lat, 1)
        obs.rppg = state.rppg.current()
        obs.facs = state.facs.current()
        state.latencies.append(lat)
        state.proc_times.append(time.monotonic())
        state.last_observation_at = obs.processed_at
        return obs
