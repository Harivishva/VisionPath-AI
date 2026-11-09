"""Live session WebSocket.

Client -> server
  binary  0x01 + float64(BE client_ts_ms) + JPEG bytes         (video frame)
  binary  0x02 + uint32(BE sample_rate) + PCM16 LE mono bytes  (audio chunk)
  text    {"type":"session.control","action":"start|pause|resume|stop"} | {"type":"ping"}
Server -> client: JSON EventEnvelope objects (see schemas.models.EventEnvelope).

Video, audio and transcription run in independent bounded queues/tasks so a failure in one
never terminates another.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas.models import new_id
from app.services import audio_dsp
from app.services.audio_processor import AudioValidationError
from app.services.multimodal_aggregator import audio_modality, vision_modality
from app.services.session_manager import Session, SessionForbidden, SessionNotFound
from app.services.transcription_service import TranscriptionFailed, TranscriptionUnavailable
from app.state import AppState

router = APIRouter()
log = logging.getLogger("visionpath.ws")

KIND_VIDEO, KIND_AUDIO = 0x01, 0x02


async def _put_bounded(q: asyncio.Queue, item, on_drop) -> None:
    """Bounded backpressure: drop the OLDEST item so the newest data is processed."""
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            on_drop()
        except asyncio.QueueEmpty:
            pass
        q.put_nowait(item)


@router.websocket("/ws/sessions/{session_id}")
async def session_ws(ws: WebSocket, session_id: str):
    st: AppState = ws.app.state.vp
    token = ws.query_params.get("token")
    try:
        s = st.sessions.get(session_id, token)
    except (SessionNotFound, SessionForbidden):
        await ws.close(code=4403)
        return
    await ws.accept()

    cfg = st.settings
    out_q: asyncio.Queue = asyncio.Queue(maxsize=cfg.ws_event_queue_size)
    s.subscribers.add(out_q)
    video_q: asyncio.Queue = asyncio.Queue(maxsize=cfg.video_queue_size)
    audio_q: asyncio.Queue = asyncio.Queue(maxsize=cfg.audio_queue_size)
    utt_q: asyncio.Queue = asyncio.Queue(maxsize=cfg.transcription_queue_size)
    s.utt_queue = utt_q
    owner = object()
    s.media_owner = owner
    tasks: list[asyncio.Task] = []

    async def sender():
        while True:
            ev = await out_q.get()
            await ws.send_text(json.dumps(ev))

    async def video_worker():
        last_status = 0.0
        while True:
            data, ts = await video_q.get()
            try:
                obs = await asyncio.to_thread(st.vision.process_jpeg, data, s.vision, ts)
                if s.status == "live":
                    s.last_vision = obs
                    s.publish("vision.observation", obs.model_dump(mode="json"))
                    s.note_vision(obs)
                    st.refresh_guardrail(s)
            except Exception as exc:  # isolate video failures
                s.record_error("vision", f"Frame processing failed: {type(exc).__name__}")
            now = time.monotonic()
            if now - last_status > 1.0:
                last_status = now
                s.publish("video.status", vision_modality(s, st.vision.ready, st.vision.error))

    async def audio_worker():
        last_status = 0.0
        while True:
            x, = await audio_q.get()
            try:
                feature, utterances = await asyncio.to_thread(st.audio.process_chunk, x, s.audio)
                s.publish("audio.feature", feature)
                window = st.audio.dsp_due(s.audio)
                if window is not None:
                    dsp = await asyncio.to_thread(audio_dsp.analyze, window, s.audio.sr, cfg,
                                            s.audio.noise_floor_dbfs)
                    s.audio.last_dsp = dsp
                    s.note_dsp(dsp)
                    s.publish("audio.dsp", dsp)
                    st.refresh_guardrail(s, force=True)
                for u in utterances:
                    await _put_bounded(utt_q, u, lambda: s.record_error(
                        "transcription", "Transcription backlog: oldest utterance dropped"))
            except Exception as exc:
                s.record_error("audio", f"Audio processing failed: {type(exc).__name__}")
            now = time.monotonic()
            if now - last_status > 1.0:
                last_status = now
                s.publish("audio.status", audio_modality(s))

    async def transcription_worker():
        while True:
            u = await utt_q.get()
            seg_pending = new_id("seg_")
            s.transcription_state = "transcribing"
            s.publish("transcript.partial", {"id": seg_pending, "state": "transcribing",
                                             "t_start_s": u.t_start_s, "t_end_s": u.t_end_s})
            try:
                cleaned, astat = await asyncio.to_thread(st.stt.prepare, u.samples, u.sr)
                s.last_audio_status = astat
                s.publish("audio.quality", astat)
                if astat["skip_stt"]:
                    # Essentially silent: explicit metadata instead of a guessed transcript.
                    s.transcription_state = "idle"
                    s.publish("transcript.partial", {"id": seg_pending, "state": astat["audio_quality"],
                                                     "audio_quality": astat["audio_quality"], "rms": astat["rms"]})
                    st.refresh_guardrail(s, force=True)
                    continue
                text, lang = await st.stt.transcribe_detailed(
                    cleaned, u.sr, retry_no_vad=(astat.get("voiced_fraction") or 0.0) >= cfg.stt_retry_voiced_fraction)
                log.info("utterance %.1fs quality=%s rms=%s snr=%s voiced=%s gain_db=%s -> %d chars",
                         len(u.samples) / u.sr, astat["audio_quality"], astat["rms"], astat["snr_db"],
                         astat["voiced_fraction"], astat["gain_db"], len(text))
                s.transcription_state, s.transcription_detail = "idle", None
                if text:
                    seg, new = s.add_segment(text, st.stt.source_tag, u.t_start_s, u.t_end_s, language=lang)
                    st.commit(s, new, seg)
                else:
                    s.publish("transcript.partial", {"id": seg_pending, "state": "empty"})
                    s.publish("session.snapshot", st.snapshot(s))
            except TranscriptionUnavailable as exc:
                s.transcription_state, s.transcription_detail = "unavailable", str(exc)
                s.record_error("transcription", f"Transcription unavailable: {exc}")
                s.publish("session.snapshot", st.snapshot(s))
            except TranscriptionFailed as exc:
                s.transcription_state, s.transcription_detail = "error", str(exc)
                s.record_error("transcription", f"Transcription failed: {exc}")
                s.publish("session.snapshot", st.snapshot(s))

    for fn in (sender, video_worker, audio_worker, transcription_worker):
        t = asyncio.create_task(fn(), name=f"{session_id}:{fn.__name__}")
        tasks.append(t)
        s.tasks.add(t)

    s.publish("session.snapshot", st.snapshot(s))

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if (b := msg.get("bytes")) is not None:
                await _handle_binary(st, s, b, video_q, audio_q)
            elif (txt := msg.get("text")) is not None:
                try:
                    data = json.loads(txt)
                except json.JSONDecodeError:
                    s.record_error("ws", "Invalid JSON control message")
                    continue
                if data.get("type") == "ping":
                    s.publish("pong", {})
                elif data.get("type") == "session.control" and data.get("action") in (
                        "start", "pause", "resume", "stop"):
                    try:
                        st.control(s, data["action"])
                    except ValueError as exc:
                        s.record_error("session", str(exc))
                else:
                    s.record_error("ws", "Unknown control message")
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for t in tasks:
            s.tasks.discard(t)
        s.subscribers.discard(out_q)
        if s.media_owner is owner:
            s.media_owner, s.utt_queue = None, None
            if s.status == "live":
                s.status = "paused"
                s.record_error("ws", "Connection lost: media capture paused", recoverable=True)
            s.purge_media_state()


async def _handle_binary(st: AppState, s: Session, b: bytes, video_q: asyncio.Queue,
                         audio_q: asyncio.Queue) -> None:
    cfg = st.settings
    if len(b) < 2:
        return
    kind = b[0]
    if s.status != "live":
        return  # capture is paused/stopped: ignore silently (client should not send)
    if kind == KIND_VIDEO:
        if len(b) < 10 or len(b) > cfg.max_frame_bytes + 9:
            s.record_error("vision", "Rejected frame: invalid or oversized payload")
            return
        ts = struct.unpack(">d", b[1:9])[0]
        s.vision.frames_received += 1
        await _put_bounded(video_q, (b[9:], ts), lambda: setattr(s.vision, "frames_dropped", s.vision.frames_dropped + 1))
    elif kind == KIND_AUDIO:
        if len(b) < 7:
            return
        rate = struct.unpack(">I", b[1:5])[0]
        try:
            x = st.audio.validate_chunk(b[5:], rate)
        except AudioValidationError as exc:
            s.record_error("audio", f"Rejected audio chunk: {exc}")
            return
        await _put_bounded(audio_q, (x,), lambda: setattr(s.audio, "chunks_dropped", s.audio.chunks_dropped + 1))
    else:
        s.record_error("ws", "Unknown binary message kind")
