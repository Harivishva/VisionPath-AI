"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, wsUrl, type CreateSessionInput } from "./api";
import {
  AudioCapture, DeviceError, FrameSampler, openCamera, openMic, stopStream, watchEnded,
  type DeviceState,
} from "./media";
import type { AudioFeature, AudioQuality, DspMetrics, EventEnvelope, SessionSnapshot, UnifiedMetrics, VisionObservation } from "./types";

export type WsState = "idle" | "connecting" | "open" | "reconnecting" | "closed";
export interface ClientStats {
  framesSent: number;
  framesSkipped: number;
  sendFps: number;
  audioChunks: number;
}

// rPPG needs a steady >=10 fps; 15 fps at 480 px stays well within the measured ~15 ms/frame server budget.
const SAMPLER = { fps: 15, width: 480, quality: 0.85 };
const MAX_RECONNECTS = 6;

export function useIntake() {
  const [session, setSession] = useState<SessionSnapshot | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [ws, setWs] = useState<WsState>("idle");
  const [camera, setCamera] = useState<DeviceState>({ status: "idle" });
  const [mic, setMic] = useState<DeviceState>({ status: "idle" });
  const [vision, setVision] = useState<VisionObservation | null>(null);
  const [audio, setAudio] = useState<AudioFeature | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [unified, setUnified] = useState<UnifiedMetrics | null>(null);
  const [dsp, setDsp] = useState<DspMetrics | null>(null);
  const [audioQuality, setAudioQuality] = useState<AudioQuality | null>(null);
  const [notice, setNotice] = useState<{ kind: "error" | "info" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<ClientStats>({ framesSent: 0, framesSkipped: 0, sendFps: 0, audioChunks: 0 });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sockRef = useRef<WebSocket | null>(null);
  const camStream = useRef<MediaStream | null>(null);
  const micStream = useRef<MediaStream | null>(null);
  const sampler = useRef<FrameSampler | null>(null);
  const audioCap = useRef<AudioCapture | null>(null);
  const sessionRef = useRef<{ id: string; token: string } | null>(null);
  const intentionalClose = useRef(false);
  const reconnects = useRef(0);
  const statusRef = useRef<SessionSnapshot["status"]>("setup");
  const audioChunks = useRef(0);

  statusRef.current = session?.status ?? "setup";

  const canSend = useCallback(
    () => sockRef.current?.readyState === WebSocket.OPEN && sockRef.current.bufferedAmount < 600_000 && statusRef.current === "live",
    [],
  );

  // ----------------------------------------------------------------- socket
  const connect = useCallback((id: string, tok: string) => {
    intentionalClose.current = false;
    setWs(reconnects.current ? "reconnecting" : "connecting");
    const sock = new WebSocket(wsUrl(id, tok));
    sock.binaryType = "arraybuffer";
    sockRef.current = sock;
    sock.onopen = () => {
      reconnects.current = 0;
      setWs("open");
    };
    sock.onmessage = (m) => {
      let ev: EventEnvelope;
      try {
        ev = JSON.parse(m.data as string);
      } catch {
        return;
      }
      switch (ev.type) {
        case "session.snapshot": {
          const snap = ev.payload as SessionSnapshot;
          setSession(snap);
          if (snap.unified) setUnified(snap.unified);
          break;
        }
        case "unified.metrics":
          setUnified(ev.payload as UnifiedMetrics);
          break;
        case "audio.dsp":
          setDsp(ev.payload as DspMetrics);
          break;
        case "audio.quality":
          setAudioQuality(ev.payload as AudioQuality);
          break;
        case "vision.observation":
          setVision(ev.payload as VisionObservation);
          break;
        case "audio.feature":
          setAudio(ev.payload as AudioFeature);
          break;
        // Periodic modality summaries keep the live counters (frames, fps, latency, segments) current
        // between full snapshots.
        case "video.status":
          setSession((p) => (p ? { ...p, modalities: { ...p.modalities, vision: ev.payload as SessionSnapshot["modalities"]["vision"] } } : p));
          break;
        case "audio.status":
          setSession((p) => (p ? { ...p, modalities: { ...p.modalities, audio: ev.payload as SessionSnapshot["modalities"]["audio"] } } : p));
          break;
        case "transcript.partial":
          setTranscribing((ev.payload as { state: string }).state === "transcribing");
          break;
        case "transcript.final":
          setTranscribing(false);
          break;
        default:
          break;
      }
    };
    sock.onclose = () => {
      if (sockRef.current !== sock) return;
      if (intentionalClose.current || !sessionRef.current) {
        setWs("closed");
        return;
      }
      if (reconnects.current >= MAX_RECONNECTS) {
        setWs("closed");
        setNotice({ kind: "error", text: "Connection to the backend was lost. Media is not being processed." });
        return;
      }
      reconnects.current += 1;
      setWs("reconnecting");
      const delay = Math.min(8000, 500 * 2 ** reconnects.current);
      setTimeout(() => {
        if (sessionRef.current && !intentionalClose.current) connect(sessionRef.current.id, sessionRef.current.token);
      }, delay);
    };
    sock.onerror = () => {
      /* onclose handles reconnect */
    };
  }, []);

  // ----------------------------------------------------------------- media
  const releaseMedia = useCallback(async () => {
    sampler.current?.stop();
    sampler.current = null;
    await audioCap.current?.stop();
    audioCap.current = null;
    stopStream(camStream.current);
    stopStream(micStream.current);
    camStream.current = micStream.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const openDevices = useCallback(async () => {
    setCamera({ status: "requesting" });
    setMic({ status: "requesting" });
    // Sequential, not concurrent: overlapping getUserMedia requests can race and produce
    // overlapping permission prompts. Each device is independent: one failing never blocks the other.
    const c = await openCamera().then((value) => ({ status: "fulfilled" as const, value }), (reason) => ({ status: "rejected" as const, reason }));
    const m = await openMic().then((value) => ({ status: "fulfilled" as const, value }), (reason) => ({ status: "rejected" as const, reason }));
    if (c.status === "fulfilled") {
      camStream.current = c.value;
      watchEnded(c.value, () => {
        // Camera vanished (unplugged / taken by another app): stop sending frames instead of streaming black.
        sampler.current?.stop();
        sampler.current = null;
        setCamera({ status: "disconnected", message: "Camera disconnected. Reconnect it and use Pause then Resume." });
      });
      const v = videoRef.current;
      if (v) {
        v.srcObject = c.value;
        v.muted = true;
        void v.play().catch(() => undefined);
      }
      setCamera({ status: "live" });
    } else {
      const e = c.reason as DeviceError;
      setCamera({ status: e.kind ?? "error", message: e.message });
    }
    if (m.status === "fulfilled") {
      micStream.current = m.value;
      watchEnded(m.value, () => {
        void audioCap.current?.stop();
        audioCap.current = null;
        setMic({ status: "disconnected", message: "Microphone disconnected. Reconnect it and use Pause then Resume." });
      });
      setMic({ status: "live" });
    } else {
      const e = m.reason as DeviceError;
      setMic({ status: e.kind ?? "error", message: e.message });
    }
    return { camera: c.status === "fulfilled", mic: m.status === "fulfilled" };
  }, []);

  const startPipelines = useCallback(async () => {
    const send = (buf: ArrayBuffer) => {
      if (!canSend()) return false;
      sockRef.current!.send(buf);
      return true;
    };
    if (camStream.current && videoRef.current) {
      sampler.current = new FrameSampler(videoRef.current, send, canSend, SAMPLER, (s) =>
        setStats((p) => ({ ...p, framesSent: s.sent, framesSkipped: s.skipped, sendFps: s.fps })),
      );
      sampler.current.start();
    }
    if (micStream.current) {
      audioCap.current = new AudioCapture();
      try {
        await audioCap.current.start(
          micStream.current,
          (b) => {
            if (canSend()) {
              sockRef.current!.send(b);
              audioChunks.current += 1;
              if (audioChunks.current % 4 === 0) setStats((p) => ({ ...p, audioChunks: audioChunks.current }));
            }
          },
          canSend,
        );
      } catch (e) {
        const err = e instanceof DeviceError ? e : new DeviceError("error", "Audio pipeline could not start.");
        setMic({ status: err.kind, message: err.message });
        stopStream(micStream.current);
        micStream.current = null;
      }
    }
  }, [canSend]);

  // ----------------------------------------------------------------- API actions
  const guard = useCallback(async <T,>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> => {
    try {
      const r = await fn();
      if (okText) setNotice({ kind: "success", text: okText });
      return r;
    } catch (e) {
      setNotice({ kind: "error", text: e instanceof ApiError ? e.message : "Unexpected error" });
      return undefined;
    }
  }, []);

  const create = useCallback(
    async (input: CreateSessionInput) => {
      setBusy(true);
      setNotice(null);
      try {
        const r = await guard(() => api.createSession(input));
        if (!r) return false;
        sessionRef.current = { id: r.session_id, token: r.token };
        setToken(r.token);
        setSession(r.session);
        reconnects.current = 0;
        connect(r.session_id, r.token);
        return true;
      } finally {
        setBusy(false);
      }
    },
    [connect, guard],
  );

  const sid = () => sessionRef.current!;

  const start = useCallback(async () => {
    if (!sessionRef.current) return;
    setBusy(true);
    setNotice(null);
    try {
      const got = await openDevices();
      const snap = await guard(() => api.control(sid().id, sid().token, "start"));
      if (snap) setSession(snap);
      statusRef.current = "live";
      await startPipelines();
      if (!got.camera && !got.mic) {
        setNotice({ kind: "info", text: "No camera or microphone available. Manual intake is still fully usable." });
      }
    } finally {
      setBusy(false);
    }
  }, [guard, openDevices, startPipelines]);

  const pause = useCallback(async () => {
    if (!sessionRef.current) return;
    const snap = await guard(() => api.control(sid().id, sid().token, "pause"));
    if (snap) setSession(snap);
    statusRef.current = "paused";
    await releaseMedia();
    setCamera({ status: "paused" });
    setMic({ status: "paused" });
  }, [guard, releaseMedia]);

  const resume = useCallback(async () => {
    if (!sessionRef.current) return;
    setBusy(true);
    try {
      await openDevices();
      const snap = await guard(() => api.control(sid().id, sid().token, "resume"));
      if (snap) setSession(snap);
      statusRef.current = "live";
      await startPipelines();
    } finally {
      setBusy(false);
    }
  }, [guard, openDevices, startPipelines]);

  const stop = useCallback(async () => {
    if (!sessionRef.current) return;
    statusRef.current = "stopped";
    await releaseMedia();
    setCamera({ status: "stopped" });
    setMic({ status: "stopped" });
    const snap = await guard(() => api.control(sid().id, sid().token, "stop"), "Session stopped. Camera and microphone released.");
    if (snap) setSession(snap);
    setVision(null);
  }, [guard, releaseMedia]);

  /** Run an API mutation that returns a fresh snapshot. */
  const mutate = useCallback(
    async (fn: (id: string, t: string) => Promise<SessionSnapshot>, okText?: string) => {
      if (!sessionRef.current) return undefined;
      const snap = await guard(() => fn(sessionRef.current!.id, sessionRef.current!.token), okText);
      if (snap) setSession(snap);
      return snap;
    },
    [guard],
  );

  const reset = useCallback(async () => {
    const cur = sessionRef.current;
    intentionalClose.current = true;
    await releaseMedia();
    sockRef.current?.close();
    sockRef.current = null;
    sessionRef.current = null;
    if (cur) await api.remove(cur.id, cur.token).catch(() => undefined);
    setSession(null);
    setToken(null);
    setVision(null);
    setAudio(null);
    setUnified(null);
    setDsp(null);
    setAudioQuality(null);
    setWs("idle");
    setCamera({ status: "idle" });
    setMic({ status: "idle" });
    setStats({ framesSent: 0, framesSkipped: 0, sendFps: 0, audioChunks: 0 });
    audioChunks.current = 0;
    setNotice({ kind: "success", text: "Session deleted from the server and all media released." });
  }, [releaseMedia]);

  // Release devices whenever the page is hidden/closed or the component unmounts.
  useEffect(() => {
    const onHide = () => {
      stopStream(camStream.current);
      stopStream(micStream.current);
    };
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      intentionalClose.current = true;
      sampler.current?.stop();
      void audioCap.current?.stop();
      stopStream(camStream.current);
      stopStream(micStream.current);
      sockRef.current?.close();
    };
  }, []);

  return {
    session, token, ws, camera, mic, vision, audio, unified, dsp, audioQuality, transcribing, notice, busy, stats, videoRef,
    setNotice, create, start, pause, resume, stop, mutate, reset, guard,
    sessionId: session?.id ?? null,
  };
}

export type Intake = ReturnType<typeof useIntake>;
