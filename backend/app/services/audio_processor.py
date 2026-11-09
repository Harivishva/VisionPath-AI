"""Audio signal processing: feature extraction, voice-activity detection, utterance segmentation.

Features are descriptive acoustic observations only. Nothing here establishes respiratory
distress, dysarthria, or any other condition.
"""
from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.schemas.models import DataState

log = logging.getLogger("visionpath.audio")


class AudioValidationError(ValueError):
    pass


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) == 0 or len(data) % 2 != 0:
        raise AudioValidationError("PCM16 payload must be a non-empty, even number of bytes")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def to_wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def estimate_f0(x: np.ndarray, sr: int, fmin: float = 70.0, fmax: float = 400.0) -> tuple[float | None, float]:
    """Autocorrelation F0 estimate. Returns (f0_hz | None, periodicity 0..1).

    The estimate is withheld (None) unless periodicity is high enough to be meaningful.
    """
    x = x - x.mean()
    if len(x) < int(sr / fmin) * 2 or float(np.max(np.abs(x))) < 1e-4:
        return None, 0.0
    n = len(x)
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    lo, hi = int(sr / fmax), min(int(sr / fmin), n - 1)
    if hi <= lo:
        return None, 0.0
    seg = ac[lo:hi]
    k = int(np.argmax(seg))
    periodicity = float(seg[k])
    if periodicity < 0.45:
        return None, max(0.0, periodicity)
    return float(sr / (lo + k)), periodicity


def spectral_features(x: np.ndarray, sr: int) -> dict[str, float]:
    import librosa  # imported lazily: heavy import

    n_fft = 512 if len(x) >= 512 else 256
    cen = librosa.feature.spectral_centroid(y=x, sr=sr, n_fft=n_fft, hop_length=n_fft // 2)
    roll = librosa.feature.spectral_rolloff(y=x, sr=sr, n_fft=n_fft, hop_length=n_fft // 2)
    flat = librosa.feature.spectral_flatness(y=x, n_fft=n_fft, hop_length=n_fft // 2)
    return {
        "spectral_centroid_hz": round(float(np.mean(cen)), 1),
        "spectral_rolloff_hz": round(float(np.mean(roll)), 1),
        "spectral_flatness": round(float(np.mean(flat)), 4),
    }


def extract_features(x: np.ndarray, sr: int) -> dict[str, float | None]:
    rms = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    dbfs = 20 * np.log10(rms) if rms > 1e-9 else -120.0
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x).astype(np.int8))))) if len(x) > 1 else 0.0
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    clipping = float(np.mean(np.abs(x) >= 0.999)) if len(x) else 0.0
    f0, per = estimate_f0(x, sr)
    feats: dict[str, float | None] = {
        "rms": round(rms, 5),
        "rms_dbfs": round(float(dbfs), 1),
        "peak": round(peak, 4),
        "zero_crossing_rate": round(zcr, 4),
        "clipping_fraction": round(clipping, 5),
        "f0_hz": round(f0, 1) if f0 else None,
        "f0_periodicity": round(per, 2),
    }
    try:
        feats.update(spectral_features(x, sr))
    except Exception as exc:  # spectral features are optional
        log.warning("Spectral features unavailable: %s", type(exc).__name__)
    return feats


@dataclass
class AudioSessionState:
    sr: int
    noise_floor_dbfs: float = -70.0
    in_speech: bool = False
    speech_buf: list[np.ndarray] = field(default_factory=list)
    speech_samples: int = 0
    silence_samples: int = 0
    session_samples: int = 0  # total processed (for timestamps)
    utt_start_samples: int = 0
    chunks_received: int = 0
    chunks_dropped: int = 0
    speech_segments: int = 0
    speech_seconds: float = 0.0
    clipped_chunks: int = 0
    low_volume_chunks: int = 0
    last_feature: dict | None = None
    prev_dbfs: float | None = None
    ring: list[np.ndarray] = field(default_factory=list)  # rolling window for the DSP engine (memory only)
    ring_samples: int = 0
    dsp_last_samples: int = 0
    last_dsp: dict | None = None

    def push_ring(self, x: np.ndarray, max_samples: int) -> None:
        self.ring.append(x)
        self.ring_samples += len(x)
        while self.ring and self.ring_samples - len(self.ring[0]) >= max_samples:
            self.ring_samples -= len(self.ring.pop(0))

    def clear_ring(self) -> None:
        self.ring, self.ring_samples, self.dsp_last_samples = [], 0, 0

    def summary(self) -> dict:
        if self.chunks_received == 0:
            state = DataState.NOT_MEASURED
        elif self.speech_segments == 0:
            state = DataState.NOT_DETECTED
        else:
            state = DataState.PENDING_REVIEW
        return {
            "state": state.value,
            "chunks_received": self.chunks_received,
            "chunks_dropped": self.chunks_dropped,
            "speech_segments": self.speech_segments,
            "speech_seconds": round(self.speech_seconds, 1),
            "clipped_chunks": self.clipped_chunks,
            "low_volume_chunks": self.low_volume_chunks,
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1),
            "last_feature": self.last_feature,
        }


@dataclass
class Utterance:
    samples: np.ndarray
    sr: int
    t_start_s: float
    t_end_s: float


class AudioProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_chunk(self, data: bytes, sample_rate: int) -> np.ndarray:
        s = self.settings
        if len(data) > s.max_audio_chunk_bytes:
            raise AudioValidationError("Audio chunk exceeds maximum size")
        if sample_rate != s.audio_sample_rate:
            raise AudioValidationError(
                f"Unsupported sample rate {sample_rate}; expected {s.audio_sample_rate}"
            )
        return pcm16_to_float(data)

    def process_chunk(
        self, x: np.ndarray, st: AudioSessionState
    ) -> tuple[dict, list[Utterance]]:
        """Returns (feature dict for the UI, completed utterances)."""
        s = self.settings
        sr = st.sr
        st.chunks_received += 1
        feats = extract_features(x, sr)
        dbfs = float(feats["rms_dbfs"])  # type: ignore[arg-type]

        # Adaptive noise floor: fast to fall, slow to rise.
        if dbfs < st.noise_floor_dbfs or st.chunks_received == 1:
            st.noise_floor_dbfs = dbfs
        else:
            st.noise_floor_dbfs += 0.02 * (dbfs - st.noise_floor_dbfs)
        threshold = max(s.vad_min_dbfs, st.noise_floor_dbfs + s.vad_noise_margin_db)
        speech = dbfs > threshold

        clipped = float(feats["clipping_fraction"]) > 0.005  # type: ignore[arg-type]
        if clipped:
            st.clipped_chunks += 1
        if dbfs < -55.0:
            st.low_volume_chunks += 1
        energy_delta = None if st.prev_dbfs is None else round(dbfs - st.prev_dbfs, 1)
        st.prev_dbfs = dbfs

        if clipped:
            quality = DataState.LOW_QUALITY
            reason = "Signal clipping detected; reduce input gain"
        elif dbfs < -55.0:
            quality = DataState.LOW_QUALITY
            reason = "Input level very low"
        else:
            quality = DataState.AVAILABLE
            reason = None

        feature = {
            **feats,
            "short_time_energy_delta_db": energy_delta,
            "voice_activity": bool(speech),
            "vad_threshold_dbfs": round(threshold, 1),
            "quality": quality.value,
            "quality_reason": reason,
            "duration_s": round(len(x) / sr, 3),
        }
        st.last_feature = feature

        utterances = self._segment(x, speech, st)
        st.push_ring(x, int(s.dsp_window_s * sr))
        st.session_samples += len(x)
        return feature, utterances

    def _segment(self, x: np.ndarray, speech: bool, st: AudioSessionState) -> list[Utterance]:
        s, sr = self.settings, st.sr
        out: list[Utterance] = []
        if speech:
            if not st.in_speech:
                st.in_speech = True
                st.utt_start_samples = st.session_samples
            st.speech_buf.append(x)
            st.speech_samples += len(x)
            st.silence_samples = 0
        elif st.in_speech:
            st.speech_buf.append(x)  # keep trailing context
            st.silence_samples += len(x)
            if st.silence_samples >= s.silence_end_s * sr:
                u = self._close(st, st.session_samples + len(x))
                if u:
                    out.append(u)
        if st.in_speech and st.speech_samples >= s.max_utterance_s * sr:
            u = self._close(st, st.session_samples + len(x))
            if u:
                out.append(u)
        return out

    def dsp_due(self, st: AudioSessionState) -> np.ndarray | None:
        """Rolling window for the DSP engine, at most once per ``dsp_interval_s`` of audio."""
        if st.session_samples - st.dsp_last_samples < self.settings.dsp_interval_s * st.sr or not st.ring:
            return None
        st.dsp_last_samples = st.session_samples
        return np.concatenate(st.ring)

    def flush(self, st: AudioSessionState) -> Utterance | None:
        return self._close(st, st.session_samples) if st.in_speech else None

    def _close(self, st: AudioSessionState, end_samples: int) -> Utterance | None:
        s = self.settings
        samples = np.concatenate(st.speech_buf) if st.speech_buf else np.zeros(0, np.float32)
        speech_len = st.speech_samples
        start = st.utt_start_samples
        st.speech_buf, st.speech_samples, st.silence_samples, st.in_speech = [], 0, 0, False
        if speech_len < s.min_utterance_s * st.sr:
            return None
        st.speech_segments += 1
        st.speech_seconds += speech_len / st.sr
        return Utterance(samples, st.sr, start / st.sr, end_samples / st.sr)
