"""Speech-to-text providers. Never fabricates a transcript: failures raise typed errors."""
from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import numpy as np

from app.config import Settings
from app.services import audio_preprocess
from app.services.audio_processor import to_wav_bytes

log = logging.getLogger("visionpath.stt")


NO_SPEECH_MAX = 0.5
AVG_LOGPROB_MIN = -0.8
COMPRESSION_MAX = 2.4


def keep_segment(seg) -> bool:
    """Drop likely-hallucinated segments (high no-speech probability or very low decoder confidence)."""
    no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
    logprob = getattr(seg, "avg_logprob", 0.0) or 0.0
    comp = getattr(seg, "compression_ratio", 0.0) or 0.0
    return not (no_speech > NO_SPEECH_MAX or logprob < AVG_LOGPROB_MIN or comp > COMPRESSION_MAX)


class TranscriptionUnavailable(RuntimeError):
    """Provider not configured / model not installed."""


class TranscriptionFailed(RuntimeError):
    """Provider configured but the request failed."""


class TranscriptionService:
    def __init__(self, settings: Settings):
        self.s = settings
        self._whisper = None
        self._whisper_lock = threading.Lock()
        self._whisper_error: str | None = None
        self.last_error: str | None = None
        self.segments_ok = 0
        self.segments_failed = 0

    # --- status -------------------------------------------------------
    def effective_provider(self) -> str:
        """`auto` resolves to the local model when faster-whisper is installed, else `none`."""
        p = self.s.transcription_provider.lower()
        if p == "auto":
            try:
                import faster_whisper  # noqa: F401
                return "local"
            except Exception:
                return "none"
        return p

    def status(self) -> dict:
        p = self.effective_provider()
        if p == "openai":
            if not self.s.transcription_api_key:
                return self._st(p, False, "VP_TRANSCRIPTION_API_KEY is not set", external=True)
            return self._st(p, True, "Configured (audio is sent to the configured API endpoint)", external=True)
        if p == "local":
            try:
                import faster_whisper  # noqa: F401
            except Exception:
                return self._st(p, False, "faster-whisper is not installed (pip install faster-whisper)")
            if self._whisper_error:
                return self._st(p, False, f"Model failed to load: {self._whisper_error}")
            loaded = self._whisper is not None
            return self._st(p, True, "Model loaded" if loaded else "Installed; model loads on first utterance")
        return self._st("none", False, "No transcription provider configured; use manual transcript entry")

    def _st(self, provider: str, ready: bool, detail: str, external: bool = False) -> dict:
        return {
            "provider": provider,
            "ready": ready,
            "detail": detail,
            "sends_audio_externally": external,
            "model": self.s.transcription_model if provider == "openai" else (
                self.s.local_whisper_model if provider == "local" else None),
            "segments_ok": self.segments_ok,
            "segments_failed": self.segments_failed,
            "last_error": self.last_error,
        }

    @property
    def source_tag(self) -> str:
        return "stt-openai" if self.effective_provider() == "openai" else "stt-local"

    # --- transcription ------------------------------------------------
    def prepare(self, samples: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
        """RMS/SNR check + optional spectral-gating noise reduction. Blocking: run in a thread."""
        return audio_preprocess.prepare(samples, sr, self.s)

    async def transcribe(self, samples: np.ndarray, sr: int) -> str:
        return (await self.transcribe_detailed(samples, sr))[0]

    async def transcribe_detailed(self, samples: np.ndarray, sr: int,
                                  retry_no_vad: bool = False) -> tuple[str, str | None]:
        """(text, language the model reported for the utterance or None)."""
        st = self.status()
        if not st["ready"]:
            raise TranscriptionUnavailable(st["detail"])
        provider = st["provider"]
        try:
            if provider == "openai":
                text, lang = await self._openai(to_wav_bytes(samples, sr)), None
            else:
                text, lang = await asyncio.to_thread(self._local_detail, samples, sr, retry_no_vad)
        except TranscriptionUnavailable:
            raise
        except Exception as exc:
            self.segments_failed += 1
            self.last_error = f"{type(exc).__name__}"
            raise TranscriptionFailed(self.last_error) from exc
        self.segments_ok += 1
        self.last_error = None
        return text.strip(), lang

    async def _openai(self, wav: bytes) -> str:
        url = self.s.transcription_base_url.rstrip("/") + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.s.transcription_api_key}"}
        last: Exception | None = None
        for attempt in range(self.s.transcription_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.s.transcription_timeout_s) as c:
                    r = await c.post(
                        url, headers=headers,
                        data={"model": self.s.transcription_model, "response_format": "json"},
                        files={"file": ("utterance.wav", wav, "audio/wav")},
                    )
                if r.status_code in (401, 403):
                    raise TranscriptionUnavailable(f"Provider rejected credentials (HTTP {r.status_code})")
                r.raise_for_status()
                return str(r.json().get("text", ""))
            except TranscriptionUnavailable:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                await asyncio.sleep(0.3 * (attempt + 1))
        raise TranscriptionFailed(type(last).__name__ if last else "unknown")

    def _local(self, samples: np.ndarray, sr: int) -> str:
        return self._local_detail(samples, sr)[0]

    def _local_detail(self, samples: np.ndarray, sr: int, retry_no_vad: bool = False) -> tuple[str, str | None]:
        with self._whisper_lock:
            if self._whisper is None:
                try:
                    from faster_whisper import WhisperModel

                    self._whisper = WhisperModel(
                        self.s.local_whisper_model, device="cpu",
                        compute_type=self.s.local_whisper_compute_type,
                    )
                except Exception as exc:
                    self._whisper_error = f"{type(exc).__name__}: {exc}"
                    raise TranscriptionUnavailable(self._whisper_error) from exc
            model = self._whisper
            # Whisper can hallucinate fluent text ("Thank you.") on near-silence or noise. Guard with
            # the built-in VAD plus per-segment confidence filters so non-speech yields NO transcript.
            # "auto" detects the language with a multilingual model; an English-only model (*.en) is
            # always decoded as English by faster-whisper regardless of this value.
            lang_cfg = (self.s.stt_language or "auto").lower()
            audio = samples.astype(np.float32)
            prompt = (self.s.stt_initial_prompt or "").lower().strip(" .")

            def decode(use_vad: bool):
                segs, info = model.transcribe(
                    audio, language=None if lang_cfg == "auto" else lang_cfg,
                    beam_size=5, vad_filter=use_vad,
                    vad_parameters={"min_silence_duration_ms": 700, "threshold": self.s.stt_vad_threshold,
                                    "min_speech_duration_ms": 150, "speech_pad_ms": 400},
                    condition_on_previous_text=False,
                    temperature=0.0,  # no sampling fallback: avoids hallucination loops during pauses
                    initial_prompt=self.s.stt_initial_prompt or None,
                )
                # The decoder can echo its own priming prompt on near-silence: that is not speech.
                texts = [s.text.strip() for s in segs if keep_segment(s)]
                return " ".join(t for t in texts if not prompt or t.lower().strip(" .") != prompt), info

            text, info = decode(True)
            if not text and retry_no_vad:
                # Whisper's VAD discarded audio that our pitch check says contains a voice (laptop mics,
                # quiet or distant speech). Decode it once without the VAD; the per-segment confidence
                # filters in keep_segment still drop anything the decoder is unsure about.
                text, info = decode(False)
                log.info("STT retry without VAD -> %d chars", len(text))
            return text, (getattr(info, "language", None) if text else None)
