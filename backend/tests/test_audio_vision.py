import cv2
import numpy as np
import pytest

from app.config import Settings
from app.schemas.models import DataState
from app.services.audio_processor import (
    AudioProcessor, AudioSessionState, AudioValidationError, estimate_f0, extract_features, pcm16_to_float,
)
from app.services.vision_processor import IDX, VisionProcessor, VisionSessionState
from tests.conftest import sine

SR = 16000


# ---------------- audio ----------------
def test_features_on_generated_tone():
    f = extract_features(sine(220.0, 0.5), SR)
    assert f["f0_hz"] is not None and abs(f["f0_hz"] - 220.0) < 8
    assert f["rms_dbfs"] > -30 and f["clipping_fraction"] == 0
    assert f["spectral_centroid_hz"] > 100


def test_f0_withheld_for_noise_and_silence():
    rng = np.random.default_rng(1)
    assert estimate_f0(rng.normal(0, 0.1, 8000).astype(np.float32), SR)[0] is None
    assert extract_features(np.zeros(8000, np.float32), SR)["f0_hz"] is None


def test_clipping_detected_and_flagged_low_quality():
    p = AudioProcessor(Settings())
    st = AudioSessionState(sr=SR)
    x = np.clip(sine(200, 0.5, amp=2.0), -1, 1).astype(np.float32)
    feat, _ = p.process_chunk(x, st)
    assert feat["clipping_fraction"] > 0.005 and feat["quality"] == DataState.LOW_QUALITY.value
    assert st.clipped_chunks == 1


def test_vad_and_segmentation_one_utterance():
    p = AudioProcessor(Settings())
    st = AudioSessionState(sr=SR)
    utts = []
    for _ in range(4):  # leading silence establishes the noise floor
        _, u = p.process_chunk(np.zeros(4000, np.float32) + 1e-4, st)
        utts += u
    voiced = []
    for _ in range(6):
        feat, u = p.process_chunk(sine(180, 0.25), st)
        voiced.append(feat["voice_activity"])
        utts += u
    for _ in range(6):  # trailing silence closes the utterance
        _, u = p.process_chunk(np.zeros(4000, np.float32) + 1e-4, st)
        utts += u
    assert all(voiced)
    assert len(utts) == 1 and 1.0 < len(utts[0].samples) / SR < 3.5
    assert st.speech_segments == 1


def test_silence_is_not_speech_and_not_a_finding():
    p = AudioProcessor(Settings())
    st = AudioSessionState(sr=SR)
    for _ in range(8):
        feat, u = p.process_chunk(np.zeros(4000, np.float32) + 1e-5, st)
        assert not feat["voice_activity"] and u == []
    assert st.summary()["state"] == DataState.NOT_DETECTED.value  # not "normal"


def test_not_measured_when_nothing_received():
    assert AudioSessionState(sr=SR).summary()["state"] == DataState.NOT_MEASURED.value


def test_invalid_audio_rejected():
    p = AudioProcessor(Settings())
    with pytest.raises(AudioValidationError):
        p.validate_chunk(b"\x00\x01\x02", SR)  # odd length
    with pytest.raises(AudioValidationError):
        p.validate_chunk(b"\x00\x00" * 10, 44100)  # wrong rate
    with pytest.raises(AudioValidationError):
        p.validate_chunk(b"\x00\x00" * 200_000, SR)  # oversize
    with pytest.raises(AudioValidationError):
        pcm16_to_float(b"")


# ---------------- vision ----------------
def jpeg(img):
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_model_ready(shared_vision):
    assert shared_vision.ready, shared_vision.error


def test_blank_frame_reports_no_face_not_normal(shared_vision):
    st = VisionSessionState()
    img = np.full((480, 640, 3), 128, np.uint8)
    obs = shared_vision.process_jpeg(jpeg(img), st, 1.0)
    assert obs.state == DataState.NOT_DETECTED and not obs.face_detected
    assert obs.measurement is None and obs.face_confidence is None
    assert "unavailable" in obs.reason.lower()
    assert st.summary()["state"] == DataState.NOT_DETECTED.value


def test_noise_frame_no_face(shared_vision):
    st = VisionSessionState()
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    assert shared_vision.process_jpeg(jpeg(img), st).state == DataState.NOT_DETECTED


def test_undecodable_frame_unavailable(shared_vision):
    obs = shared_vision.process_jpeg(b"not a jpeg", VisionSessionState())
    assert obs.state == DataState.UNAVAILABLE


def test_model_missing_is_unavailable_not_fabricated():
    v = VisionProcessor(Settings(face_model_path="C:/definitely/missing.task"))
    assert not v.ready and v.error
    obs = v.process_bgr(np.full((240, 320, 3), 100, np.uint8), VisionSessionState())
    assert obs.state == DataState.UNAVAILABLE and obs.measurement is None


def test_quality_metrics_dark_frame(shared_vision):
    q = shared_vision._quality(np.full((240, 320, 3), 5, np.uint8))
    assert q["brightness"] < 10


def _face_points(roll_deg=0.0, mouth_dy=(0.0, 0.0)):
    """Synthetic 478-landmark layout, only the indices used for geometry are meaningful."""
    p = np.zeros((478, 2))
    p[IDX["eye_outer_left"]] = [200, 200]
    p[IDX["eye_outer_right"]] = [400, 200]
    p[IDX["mouth_left"]] = [240, 350 + mouth_dy[0]]
    p[IDX["mouth_right"]] = [360, 350 + mouth_dy[1]]
    p[IDX["brow_left"]] = [230, 170]
    p[IDX["brow_right"]] = [370, 170]
    p[IDX["lid_up_left"]] = [230, 195]
    p[IDX["lid_low_left"]] = [230, 215]
    p[IDX["lid_up_right"]] = [370, 195]
    p[IDX["lid_low_right"]] = [370, 215]
    if roll_deg:
        a = np.deg2rad(roll_deg)
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        c = np.array([300.0, 250.0])
        p = (p - c) @ R.T + c
    return p


def test_geometry_symmetric_is_zero():
    m = VisionProcessor._geometry(_face_points())
    assert abs(m.mouth_corner_vertical_offset) < 1e-6 and abs(m.brow_height_offset) < 1e-6
    assert abs(m.eye_opening_offset) < 1e-6


def test_geometry_detects_offset_and_is_roll_invariant():
    base = VisionProcessor._geometry(_face_points(mouth_dy=(10.0, 0.0)))  # left corner lower in image
    assert base.mouth_corner_vertical_offset == pytest.approx(-(10 / 200), abs=1e-3) or \
        base.mouth_corner_vertical_offset == pytest.approx(10 / 200, abs=1e-3)
    rolled = VisionProcessor._geometry(_face_points(roll_deg=12, mouth_dy=(10.0, 0.0)))
    assert rolled.mouth_corner_vertical_offset == pytest.approx(base.mouth_corner_vertical_offset, abs=2e-3)


def test_gate_rejects_rotated_and_dark(shared_vision):
    s = shared_vision.settings
    good = {"brightness": 120.0, "sharpness": 100.0, "face_visibility": 1.0, "face_area_ratio": 0.2}
    assert shared_vision._gate(good, {"yaw": 0, "pitch": 0, "roll": 0}) is None
    assert "rotated" in shared_vision._gate(good, {"yaw": s.max_head_yaw_deg + 5, "pitch": 0, "roll": 0})
    assert "tilted" in shared_vision._gate(good, {"yaw": 0, "pitch": 0, "roll": s.max_head_roll_deg + 20})
    assert "dark" in shared_vision._gate({**good, "brightness": 10}, {"yaw": 0, "pitch": 0, "roll": 0})
    assert "blurry" in shared_vision._gate({**good, "sharpness": 1}, {"yaw": 0, "pitch": 0, "roll": 0})
    assert "outside" in shared_vision._gate({**good, "face_visibility": 0.5}, {"yaw": 0, "pitch": 0, "roll": 0})
