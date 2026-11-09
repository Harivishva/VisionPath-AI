import json
import struct
import time

import cv2
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import make_session, sine, to_pcm16


def jpeg_bytes(val=128):
    ok, buf = cv2.imencode(".jpg", np.full((240, 320, 3), val, np.uint8))
    return buf.tobytes()


def vframe(data):
    return b"\x01" + struct.pack(">d", time.time() * 1000) + data


def aframe(pcm, rate=16000):
    return b"\x02" + struct.pack(">I", rate) + pcm


def collect(ws, until, limit=60):
    got = []
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        got.append(ev)
        if until(ev):
            return got
    raise AssertionError(f"event not seen; got {[e['type'] for e in got]}")


def open_ws(client, sid, token):
    return client.websocket_connect(f"/ws/sessions/{sid}?token={token}")


def test_ws_rejects_bad_token(client):
    sid, h, _ = make_session(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/sessions/{sid}?token=wrong") as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/sessions/ses_nope?token=x") as ws:
            ws.receive_text()


def test_ws_initial_snapshot_and_envelope(client):
    sid, h, d = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ev = json.loads(ws.receive_text())
    assert ev["type"] == "session.snapshot" and ev["session_id"] == sid
    assert ev["event_id"].startswith("evt_") and ev["ts"] and ev["payload"]["id"] == sid


def test_ws_video_frame_gets_honest_no_face_observation(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        ws.send_bytes(vframe(jpeg_bytes()))
        evs = collect(ws, lambda e: e["type"] == "vision.observation")
    obs = evs[-1]["payload"]
    assert obs["state"] == "not_detected" and obs["measurement"] is None and obs["face_confidence"] is None
    assert obs["latency_ms"] > 0


def test_ws_frames_ignored_unless_live(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_bytes(vframe(jpeg_bytes()))  # status is 'setup'
        ws.send_text(json.dumps({"type": "ping"}))
        evs = collect(ws, lambda e: e["type"] == "pong")
    assert all(e["type"] != "vision.observation" for e in evs)


def test_ws_invalid_media_reported_without_crash(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        ws.send_bytes(aframe(b"\x00\x01\x02"))  # odd length
        ws.send_bytes(aframe(to_pcm16(sine()), rate=44100))  # wrong rate
        ws.send_bytes(b"\x09" + b"junk")  # unknown kind
        ws.send_bytes(vframe(b"x" * 500_000))  # oversize
        ws.send_bytes(vframe(b"not-a-jpeg"))  # undecodable -> unavailable obs
        evs = collect(ws, lambda e: e["type"] == "vision.observation")
        errs = [e for e in evs if e["type"] == "system.error"]
        assert len(errs) >= 4
        # pipeline still alive afterwards
        ws.send_bytes(vframe(jpeg_bytes()))
        collect(ws, lambda e: e["type"] == "vision.observation")
    assert evs[-1]["payload"]["state"] == "unavailable"


def test_ws_audio_features_and_transcription_unavailable_is_honest(client):
    """Speech with no STT provider: features flow, transcription is reported unavailable (never faked)."""
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        for _ in range(4):
            ws.send_bytes(aframe(to_pcm16(np.zeros(4000, np.float32) + 1e-4)))
        for _ in range(6):
            ws.send_bytes(aframe(to_pcm16(sine(190, 0.25))))
        for _ in range(6):
            ws.send_bytes(aframe(to_pcm16(np.zeros(4000, np.float32) + 1e-4)))
        evs = collect(ws, lambda e: e["type"] == "system.error" and e["payload"]["source"] == "transcription", 200)
    feats = [e for e in evs if e["type"] == "audio.feature"]
    assert feats and any(f["payload"]["voice_activity"] for f in feats)
    assert "f0_hz" in feats[0]["payload"] and "rms_dbfs" in feats[0]["payload"]
    assert any(e["type"] == "transcript.partial" for e in evs)
    snap = client.get(f"/api/sessions/{sid}", headers=h).json()
    assert snap["segments"] == []  # nothing fabricated
    assert snap["modalities"]["transcript"]["transcription_state"] == "unavailable"


def test_ws_transcription_success_path_with_stubbed_provider(client):
    """Isolates the transcription provider only; the audio pipeline and rule engine are real."""
    st = client.app.state.vp

    async def fake(samples, sr, **_kw):
        return "I have severe chest pain", "en"

    st.stt.transcribe_detailed = fake
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        for _ in range(4):
            ws.send_bytes(aframe(to_pcm16(np.zeros(4000, np.float32) + 1e-4)))
        for _ in range(6):
            ws.send_bytes(aframe(to_pcm16(sine(190, 0.25))))
        for _ in range(6):
            ws.send_bytes(aframe(to_pcm16(np.zeros(4000, np.float32) + 1e-4)))
        evs = collect(ws, lambda e: e["type"] == "safety.alert", 200)
    types = [e["type"] for e in evs]
    assert "transcript.final" in types and "symptom.extracted" in types
    assert evs[-1]["payload"]["alert"]["rule_id"] == "R-CHEST-001"


def test_ws_events_isolated_between_sessions(client):
    a, ha, _ = make_session(client, reference="A")
    b, hb, _ = make_session(client, reference="B")
    with open_ws(client, a, ha["X-Session-Token"]) as wa, open_ws(client, b, hb["X-Session-Token"]) as wb:
        wa.receive_text(), wb.receive_text()
        client.post(f"/api/sessions/{a}/transcript", json={"text": "I have chest pain"}, headers=ha)
        evs = collect(wa, lambda e: e["type"] == "safety.alert")
        assert all(e["session_id"] == a for e in evs)
        wb.send_text(json.dumps({"type": "ping"}))
        evs_b = collect(wb, lambda e: e["type"] == "pong")
        assert all(e["session_id"] == b and e["type"] != "safety.alert" for e in evs_b)


def test_ws_disconnect_pauses_live_session_and_releases_state(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        collect(ws, lambda e: e["type"] == "session.status")
    time.sleep(0.2)
    s = client.app.state.vp.sessions._sessions[sid]
    assert s.status == "paused" and s.media_stopped and s.media_owner is None and not s.subscribers


def test_ws_pause_stops_processing(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        ws.send_text(json.dumps({"type": "session.control", "action": "pause"}))
        ws.send_bytes(vframe(jpeg_bytes()))
        ws.send_text(json.dumps({"type": "ping"}))
        evs = collect(ws, lambda e: e["type"] == "pong")
    assert all(e["type"] != "vision.observation" for e in evs)
    s = client.app.state.vp.sessions._sessions[sid]
    assert s.vision.frames_received == 0


def test_delete_session_cancels_tasks_and_closes(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.receive_text()
        assert client.delete(f"/api/sessions/{sid}", headers=h).status_code == 200
    assert client.app.state.vp.sessions.count() == 0


def test_ws_periodic_modality_status_carries_live_counters(client):
    sid, h, _ = make_session(client)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        ws.send_bytes(vframe(jpeg_bytes()))
        ws.send_bytes(aframe(to_pcm16(sine(190, 0.25))))
        seen = {}
        for _ in range(200):  # order of the two status events is not guaranteed
            ev = json.loads(ws.receive_text())
            if ev["type"] in ("video.status", "audio.status"):
                seen[ev["type"]] = ev["payload"]
            if len(seen) == 2:
                break
    vs, au = seen["video.status"], seen["audio.status"]
    assert vs["frames_processed"] >= 1 and vs["state"] in ("not_detected", "low_quality")
    assert "mean_latency_ms" in vs and "fps" in vs
    assert au["chunks_received"] >= 1


def test_ws_quiet_speech_is_flagged_too_quiet_but_still_attempted(client):
    """Quiet speech (below RMS 0.01) is reported as too_quiet, boosted and still sent to STT."""
    sid, h, _ = make_session(client)
    quiet = sine(freq=150, secs=0.25, amp=0.009)  # RMS ~0.007: above the VAD floor, below 0.01
    silence = np.zeros(4000, dtype=np.float32)
    with open_ws(client, sid, h["X-Session-Token"]) as ws:
        ws.send_text(json.dumps({"type": "session.control", "action": "start"}))
        for _ in range(3):  # establish the noise floor first
            ws.send_bytes(aframe(to_pcm16(silence)))
        for _ in range(6):
            ws.send_bytes(aframe(to_pcm16(quiet)))
        for _ in range(8):
            ws.send_bytes(aframe(to_pcm16(silence)))
        evs = collect(ws, lambda e: e["type"] == "audio.quality", limit=200)
    q = evs[-1]["payload"]
    assert q["audio_quality"] == "too_quiet" and q["skip_stt"] is False and q["gain_db"] > 0
