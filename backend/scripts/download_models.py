"""Download the models VisionPath needs.

  python scripts/download_models.py            # MediaPipe face landmarker (~3.7 MB, required for vision)
  python scripts/download_models.py --whisper  # also pre-fetch the local Whisper model (small.en ~480 MB)
"""
import argparse
import hashlib
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", action="store_true", help="also download the local faster-whisper model")
    ap.add_argument("--whisper-model", default=os.environ.get("VP_LOCAL_WHISPER_MODEL", "small.en"))
    args = ap.parse_args()

    dest = os.path.join(ROOT, "models", "face_landmarker.task")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        print(f"face_landmarker.task already present ({os.path.getsize(dest):,} bytes)")
    else:
        print(f"Downloading {FACE_URL}")
        urllib.request.urlretrieve(FACE_URL, dest)
        print(f"Saved {dest} ({os.path.getsize(dest):,} bytes, sha256 {hashlib.sha256(open(dest, 'rb').read()).hexdigest()[:16]}...)")

    if args.whisper:
        from faster_whisper import WhisperModel

        print(f"Fetching faster-whisper model '{args.whisper_model}' (cached under ~/.cache/huggingface)")
        WhisperModel(args.whisper_model, device="cpu", compute_type="int8")
        print("Whisper model ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
