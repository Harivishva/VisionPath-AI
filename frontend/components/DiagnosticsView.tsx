"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, api, ApiError } from "@/lib/api";
import type { Diagnostics } from "@/lib/types";
import type { Intake } from "@/lib/useIntake";
import { Badge, Button, Panel } from "./ui";

export function DiagnosticsView({ intake }: { intake: Intake }) {
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [devices, setDevices] = useState<{ cams: number; mics: number } | null>(null);

  const refresh = useCallback(async () => {
    setErr(null);
    try {
      setDiag(await api.diagnostics());
    } catch (e) {
      setDiag(null);
      setErr(e instanceof ApiError ? e.message : "Backend unreachable");
    }
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      setDevices({ cams: list.filter((d) => d.kind === "videoinput").length, mics: list.filter((d) => d.kind === "audioinput").length });
    } catch {
      setDevices(null);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const s = intake.session;
  const row = (k: string, v: React.ReactNode, sub?: string) => (
    <div className="flex items-start justify-between gap-4 px-3 py-2">
      <div><div className="text-[13px] text-ink-2">{k}</div>{sub && <div className="text-[12px] text-ink-3">{sub}</div>}</div>
      <div className="text-right text-[13px]">{v}</div>
    </div>
  );
  const ok = (b: boolean, yes = "Ready", no = "Unavailable") => (b ? <Badge tone="sage">{yes}</Badge> : <Badge tone="amber">{no}</Badge>);

  return (
    <div className="mx-auto grid max-w-[1000px] gap-3 p-4 md:grid-cols-2" data-testid="diagnostics-view">
      <Panel title="Backend" aside={<Button small variant="ghost" onClick={() => void refresh()}>Refresh</Button>}>
        <div className="divide-y divide-line-soft">
          {row("API connectivity", err ? <Badge tone="alert">{err}</Badge> : diag ? <Badge tone="sage">Connected</Badge> : <Badge>Checking…</Badge>, API_URL)}
          {diag && (
            <>
              {row("Software version", <span className="readout">{diag.version}</span>, `Python ${diag.python} · ${diag.environment}`)}
              {row("Vision model (MediaPipe FaceLandmarker)", ok(diag.vision.ready), diag.vision.error ?? undefined)}
              {row("Speech recognition", ok(diag.transcription.ready, diag.transcription.provider), diag.transcription.detail)}
              {row("LLM provider", ok(diag.llm.ready, "Configured", "Not configured"), diag.llm.detail)}
              {row("Raw media persistence", diag.raw_media_persistence ? <Badge tone="alert">ENABLED</Badge> : <Badge tone="sage">Off</Badge>, "Frames and audio are processed in memory only")}
              {row("Active sessions", <span className="readout">{diag.active_sessions}</span>)}
            </>
          )}
        </div>
      </Panel>
      <Panel title="This browser & session">
        <div className="divide-y divide-line-soft">
          {row("Webcam availability", devices ? (devices.cams ? <Badge tone="sage">{devices.cams} detected</Badge> : <Badge tone="amber">None found</Badge>) : <Badge>Unknown</Badge>, "Device labels stay hidden until permission is granted")}
          {row("Microphone availability", devices ? (devices.mics ? <Badge tone="sage">{devices.mics} detected</Badge> : <Badge tone="amber">None found</Badge>) : <Badge>Unknown</Badge>)}
          {row("Camera state", <span className="readout">{intake.camera.status}</span>, intake.camera.message)}
          {row("Microphone state", <span className="readout">{intake.mic.status}</span>, intake.mic.message)}
          {row("WebSocket", <span className="readout">{intake.ws}</span>)}
          {s && row("Frames sent / dropped (server)", <span className="readout">{intake.stats.framesSent} / {s.modalities.vision.frames_dropped}</span>)}
          {s && row("Session media released", s.privacy.media_released ? <Badge tone="sage">Yes</Badge> : <Badge>No (capture active or not started)</Badge>)}
        </div>
      </Panel>
      <Panel title="Processing errors" className="md:col-span-2">
        <ul className="divide-y divide-line-soft text-[13px]" data-testid="error-list">
          {!s || s.errors.length === 0 ? <li className="px-3 py-2 text-ink-3">No processing errors recorded in this session.</li> : s.errors.map((e) => (
            <li key={e.id} className="px-3 py-2"><Badge tone="amber">{e.source}</Badge> {e.message} <span className="readout text-ink-3">{new Date(e.ts).toLocaleTimeString()}</span></li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
