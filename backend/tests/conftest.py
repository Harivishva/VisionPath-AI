import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.state import AppState


@pytest.fixture(scope="session")
def shared_vision():
    from app.services.vision_processor import VisionProcessor

    v = VisionProcessor(Settings())
    yield v
    v.close()


@pytest.fixture()
def settings():
    return Settings(transcription_provider="none", llm_provider="none")  # deterministic for tests


@pytest.fixture()
def client(settings, shared_vision):
    state = AppState(settings, vision=shared_vision)
    app = create_app(settings, state)
    with TestClient(app) as c:
        app.state.vp = state  # lifespan replaces with provided state; keep explicit
        yield c


def make_session(client, synthetic=False, reference="TEST-001", notes=""):
    r = client.post("/api/sessions", json={"reference": reference, "synthetic": synthetic,
                                          "consent_acknowledged": True, "patient_reported_notes": notes})
    assert r.status_code == 201, r.text
    d = r.json()
    return d["session_id"], {"X-Session-Token": d["token"]}, d


@pytest.fixture()
def new_session(client):
    def _mk(**kw):
        return make_session(client, **kw)
    return _mk


def sine(freq=220.0, secs=0.5, sr=16000, amp=0.3):
    t = np.arange(int(secs * sr)) / sr
    # add harmonics so the signal is voice-like
    x = sum(amp / (k) * np.sin(2 * np.pi * freq * k * t) for k in (1, 2, 3))
    return x.astype(np.float32)


def to_pcm16(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
