import json

import httpx
import numpy as np
import pytest

from app.config import Settings
from app.services import clinical_summary as cs
from app.services.privacy_service import redact
from app.services.transcription_service import (
    TranscriptionFailed, TranscriptionService, TranscriptionUnavailable,
)
from tests.conftest import make_session, sine


REAL_ASYNC_CLIENT = httpx.AsyncClient


def patch_client(monkeypatch, module, handler):
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler),
                                                       timeout=kw.get("timeout")))


# ---------- transcription ----------
async def test_no_provider_raises_unavailable():
    svc = TranscriptionService(Settings(transcription_provider="none"))
    assert svc.status()["ready"] is False
    with pytest.raises(TranscriptionUnavailable):
        await svc.transcribe(sine(), 16000)


async def test_openai_without_key_unavailable():
    svc = TranscriptionService(Settings(transcription_provider="openai", transcription_api_key=""))
    st = svc.status()
    assert not st["ready"] and st["sends_audio_externally"] is True
    with pytest.raises(TranscriptionUnavailable):
        await svc.transcribe(sine(), 16000)


async def test_openai_success_and_failure(monkeypatch):
    from app.services import transcription_service as ts

    svc = TranscriptionService(Settings(transcription_provider="openai", transcription_api_key="k" * 12,
                                        transcription_retries=1))
    patch_client(monkeypatch, ts, lambda r: httpx.Response(200, json={"text": " hello there "}))
    assert await svc.transcribe(sine(), 16000) == "hello there"
    calls = []

    def boom(r):
        calls.append(1)
        return httpx.Response(500)

    patch_client(monkeypatch, ts, boom)
    with pytest.raises(TranscriptionFailed):
        await svc.transcribe(sine(), 16000)
    assert len(calls) == 2 and svc.segments_failed == 1  # bounded retry
    patch_client(monkeypatch, ts, lambda r: httpx.Response(401))
    with pytest.raises(TranscriptionUnavailable):
        await svc.transcribe(sine(), 16000)


def test_local_provider_status_is_truthful():
    st = TranscriptionService(Settings(transcription_provider="local")).status()
    assert st["provider"] == "local" and st["sends_audio_externally"] is False


# ---------- LLM ----------
SECTIONS = {
    "chief_complaint": None, "staff_observations": [], "machine_interpretations": [],
    "items_requiring_verification": [],
    "patient_reported": [{"label": "Chest pain / discomfort", "polarity": "affirmed"}],
}


def llm(**kw):
    return cs.LLMService(Settings(llm_provider="openai", llm_api_key="k" * 12, llm_model="m", **kw))


def reply(content):
    return lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_llm_unconfigured():
    svc = cs.LLMService(Settings(llm_provider="none"))
    assert svc.status()["ready"] is False
    with pytest.raises(cs.LLMUnavailable):
        await svc.draft(SECTIONS)


async def test_llm_valid_schema(monkeypatch):
    good = json.dumps({"narrative": "Patient reports chest pain.", "missing_information": ["onset"],
                       "symptoms_mentioned": ["Chest pain / discomfort"]})
    patch_client(monkeypatch, cs, reply(good))
    d = await llm().draft(SECTIONS)
    assert d.narrative.startswith("Patient")


async def test_llm_rejects_hallucinated_symptom_and_bad_schema(monkeypatch):
    bad = json.dumps({"narrative": "x", "symptoms_mentioned": ["Stroke"]})
    patch_client(monkeypatch, cs, reply(bad))
    with pytest.raises(cs.LLMInvalid):
        await llm().draft(SECTIONS)
    patch_client(monkeypatch, cs, reply(json.dumps({"narrative": "x", "diagnosis": "stroke"})))
    with pytest.raises(cs.LLMInvalid):  # extra field forbidden
        await llm().draft(SECTIONS)
    patch_client(monkeypatch, cs, reply("not json"))
    with pytest.raises(cs.LLMInvalid):
        await llm().draft(SECTIONS)


async def test_llm_timeout_is_bounded_and_graceful(monkeypatch):
    calls = []

    def slow(r):
        calls.append(1)
        raise httpx.ReadTimeout("t")

    patch_client(monkeypatch, cs, slow)
    with pytest.raises(cs.LLMUnavailable):
        await llm(llm_retries=1).draft(SECTIONS)
    assert len(calls) == 2


def test_llm_failure_never_blocks_alerts_or_fallback_summary(client):
    sid, h, _ = make_session(client)
    snap = client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have chest pain"}, headers=h).json()
    assert snap["alerts"] and snap["providers"]["llm"]["ready"] is False
    assert client.post(f"/api/sessions/{sid}/summary/draft?mode=llm", headers=h).json()["origin"] == "deterministic"


def test_llm_draft_cannot_remove_alerts(client, monkeypatch):
    st = client.app.state.vp
    st.llm.s.llm_provider, st.llm.s.llm_api_key, st.llm.s.llm_model = "openai", "k" * 12, "m"
    good = json.dumps({"narrative": "Everything seems fine.", "symptoms_mentioned": []})
    patch_client(monkeypatch, cs, reply(good))
    sid, h, _ = make_session(client)
    client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have chest pain"}, headers=h)
    d = client.post(f"/api/sessions/{sid}/summary/draft?mode=llm", headers=h).json()
    assert d["origin"] == "llm_draft" and "Chest pain" in d["text"] and "R-CHEST-001" in d["text"]
    snap = client.get(f"/api/sessions/{sid}", headers=h).json()
    assert snap["indicator"]["level"] == "immediate_human_review"


# ---------- privacy ----------
def test_redaction():
    assert "sk-" not in redact("key sk-abcdefghijklmnopqrstuvwxyz1234")
    assert "abcdefgh1234" not in redact("Authorization: Bearer abcdefgh1234567")
    assert "REDACTED" in redact("token=abcdef123456789")


def test_logs_do_not_contain_transcript(client, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    sid, h, _ = make_session(client)
    client.post(f"/api/sessions/{sid}/transcript", json={"text": "UNIQUE-PHI-MARKER chest pain"}, headers=h)
    assert "UNIQUE-PHI-MARKER" not in caplog.text


def test_media_never_persisted_and_buffers_released(client):
    st = client.app.state.vp
    sid, h, _ = make_session(client)
    s = st.sessions._sessions[sid]
    assert s.raw_media_persisted is False and st.settings.persist_raw_media is False
    s.audio.speech_buf.append(np.zeros(100, np.float32))
    s.audio.in_speech = True
    client.post(f"/api/sessions/{sid}/control", json={"action": "start"}, headers=h)
    client.post(f"/api/sessions/{sid}/control", json={"action": "stop"}, headers=h)
    assert s.audio.speech_buf == [] and not s.audio.in_speech and s.vision.ema is None


def test_hallucination_guard_drops_low_confidence_segments():
    from types import SimpleNamespace as NS
    from app.services.transcription_service import keep_segment

    assert keep_segment(NS(no_speech_prob=0.05, avg_logprob=-0.3))
    assert not keep_segment(NS(no_speech_prob=0.9, avg_logprob=-0.3))   # probably silence/noise
    assert not keep_segment(NS(no_speech_prob=0.1, avg_logprob=-1.6))   # decoder unsure


def test_local_whisper_returns_nothing_for_silence_and_noise():
    """Real model on non-speech audio must not invent text (skipped if the model is unavailable)."""
    import asyncio
    svc = TranscriptionService(Settings(transcription_provider="local"))
    if not svc.status()["ready"]:
        pytest.skip("faster-whisper not installed")
    rng = np.random.default_rng(3)
    for x in (np.zeros(16000 * 2, np.float32), (rng.normal(0, 0.003, 16000 * 2)).astype(np.float32)):
        try:
            text = asyncio.run(svc.transcribe(x, 16000))
        except TranscriptionUnavailable:
            pytest.skip("model not downloaded")
        assert text == ""
