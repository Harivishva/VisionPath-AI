"""Environment-driven configuration. No secrets are hardcoded; see .env.example."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VP_", env_file=".env", extra="ignore")

    env: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Limits (bytes / counts)
    max_frame_bytes: int = 400_000
    max_audio_chunk_bytes: int = 200_000
    max_text_chars: int = 8_000
    max_sessions: int = 20
    session_idle_ttl_minutes: float = 120.0  # idle sessions (and their data) are erased after this long
    session_sweep_interval_s: float = 60.0
    video_queue_size: int = 2
    audio_queue_size: int = 32
    transcription_queue_size: int = 4
    ws_event_queue_size: int = 256

    # Vision
    face_model_path: str = str(BACKEND_DIR / "models" / "face_landmarker.task")
    max_head_yaw_deg: float = 25.0
    max_head_pitch_deg: float = 20.0
    max_head_roll_deg: float = 35.0
    min_sharpness: float = 25.0  # variance of Laplacian on 320px-wide gray image
    min_brightness: float = 50.0
    max_brightness: float = 215.0
    smoothing_alpha: float = 0.3

    # Audio
    audio_sample_rate: int = 16_000
    vad_min_dbfs: float = -48.0
    vad_noise_margin_db: float = 10.0
    silence_end_s: float = 0.9
    max_utterance_s: float = 25.0
    min_utterance_s: float = 0.4

    # STT noise handling (applied to each utterance before the model sees it)
    stt_min_rms: float = 0.01  # utterance RMS below this -> audio_quality "too_quiet" (flagged, then gain-boosted)
    stt_skip_rms: float = 0.002  # essentially silent: no transcription attempt at all
    stt_target_rms: float = 0.05  # quiet speech is boosted (max +18 dB) to this level before STT
    stt_denoise: bool = False  # off by default: gating artefacts can make Whisper hallucinate
    stt_denoise_below_snr_db: float = 25.0  # spectral gating is skipped for already-clean audio
    stt_noisy_snr_db: float = 8.0  # below this the audio is flagged "noisy" (still transcribed)
    stt_initial_prompt: str = ""  # off by default: a priming prompt makes Whisper invent text from noise
    stt_vad_threshold: float = 0.35  # Whisper-internal VAD; 0.6 discarded real laptop-mic speech
    stt_retry_voiced_fraction: float = 0.15  # retry without Whisper VAD when this much of the clip is voiced
    stt_min_voiced_fraction: float = 0.08  # an utterance needs this share of voiced (pitched) frames to be treated as speech

    # rPPG (experimental; not a validated vital-sign measurement)
    rppg_window_s: float = 5.0
    rppg_min_fps: float = 10.0
    rppg_min_confidence: float = 0.35  # below this no heart rate is reported at all

    # Acoustic DSP (rolling window)
    dsp_window_s: float = 15.0
    dsp_interval_s: float = 2.0
    dsp_min_speech_s: float = 2.0
    dsp_gasp_min_events: int = 2

    # Waiting-room re-triage monitor (derived metrics polled at ~1 Hz; trend check every 5 min)
    waiting_sample_interval_s: float = 1.0
    waiting_eval_interval_s: float = 300.0
    waiting_window_s: float = 300.0
    waiting_baseline_samples: int = 10
    waiting_min_samples: int = 5

    # Visual-sign notices (lower than the guardrail thresholds; informational only)
    vis_asymmetry_pct: float = 6.0
    vis_au_score: float = 0.5
    vis_palsy_velocity: float = 0.1

    # Deterministic guardrails (prototype priority overrides; thresholds configurable)
    guard_hr_high_bpm: float = 130.0
    guard_hr_low_bpm: float = 40.0
    guard_hr_min_confidence: float = 0.5  # rPPG must be at least this reliable to drive an override
    guard_asymmetry_pct: float = 10.0
    guard_speech_ratio: float = 0.4
    guard_gasp_level: int = 2  # ESI-style level forced by gasping + low speech ratio (1 or 2)
    guard_fresh_s: float = 5.0  # metrics older than this are ignored
    xai_weights: str = "0.35,0.35,0.30"  # vision, acoustic, transcript (HR shares the vision weight)

    # Transcription: auto | none | openai | local  (auto = local faster-whisper if installed)
    transcription_provider: str = "auto"
    transcription_base_url: str = "https://api.openai.com/v1"
    transcription_api_key: str = ""
    transcription_model: str = "whisper-1"
    local_whisper_model: str = "small.en"
    local_whisper_compute_type: str = "int8"
    # "auto" lets a MULTILINGUAL Whisper model detect the language (needs VP_LOCAL_WHISPER_MODEL=small|medium|...).
    # The default small.en is English-only and always decodes as English.
    stt_language: str = "auto"
    transcription_timeout_s: float = 30.0
    transcription_retries: int = 1

    # Optional LLM (OpenAI-compatible chat completions)
    llm_provider: str = "none"  # none | openai
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 20.0
    llm_retries: int = 1

    # Privacy: raw media is never persisted unless explicitly enabled for tests.
    persist_raw_media: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
