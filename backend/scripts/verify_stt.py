"""Verify the configured speech-to-text provider with a WAV file (e.g. Windows TTS output).

Usage: python scripts/verify_stt.py path/to/speech.wav
Uses VP_TRANSCRIPTION_PROVIDER (default: local). Prints the transcript and the extracted mentions.
"""
import asyncio
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VP_TRANSCRIPTION_PROVIDER", "local")

from app.config import Settings  # noqa: E402
from app.services import symptom_extractor  # noqa: E402
from app.services.transcription_service import TranscriptionService  # noqa: E402


def load(path: str, target_sr: int = 16000):
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        assert sw == 2, "expected 16-bit PCM"
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != target_sr:
        from scipy.signal import resample_poly
        from math import gcd

        g = gcd(sr, target_sr)
        x = resample_poly(x, target_sr // g, sr // g).astype(np.float32)
    return x, target_sr


async def main(path: str):
    svc = TranscriptionService(Settings())
    print("status:", svc.status())
    x, sr = load(path)
    text = await svc.transcribe(x, sr)
    print("transcript:", text)
    for m in symptom_extractor.extract("seg", text):
        print(f"  {m.label}: {m.polarity} reporter={m.reporter} sudden={m.sudden}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
