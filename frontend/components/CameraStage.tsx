"use client";

import { useEffect, useRef, useState } from "react";
import type { Intake } from "@/lib/useIntake";
import type { DeviceState } from "@/lib/media";
import { Badge, Button, Dot, StateChip } from "./ui";

const DEVICE_COPY: Record<string, string> = {
  idle: "Camera is off. Press Start capture to request access.",
  requesting: "Waiting for camera permission…",
  paused: "Capture paused — camera released.",
  stopped: "Session stopped — camera released.",
};

function stageMessage(cam: DeviceState): string {
  return cam.message ?? DEVICE_COPY[cam.status] ?? "Camera not active.";
}

export function CameraStage({ intake }: { intake: Intake }) {
  const { session, camera, mic, vision, videoRef, ws, busy } = intake;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [aspect, setAspect] = useState(4 / 3);
  const status = session?.status ?? "setup";
  const live = camera.status === "live";

  // Draw the real landmark overlay returned by the backend for the latest accepted/tracked frame.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    if (!live || !vision?.overlay || !vision.face_detected) return;
    const ok = vision.state === "pending_review";
    const W = c.width, H = c.height;
    const over = (intake.unified?.guardrail_status.triggered_rules ?? []).some((r) => r.name === "FACIAL_ASYMMETRY");
    const path = (pts: number[][], close: boolean) => {
      ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
      if (close) ctx.closePath();
    };
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = ok ? "rgba(221,233,227,0.95)" : "rgba(245,230,192,0.95)";
    for (const [name, pts] of Object.entries(vision.overlay)) {
      if (!pts.length || name.startsWith("roi_") || name.startsWith("asym_")) continue;
      path(pts, true);
      ctx.stroke();
    }
    // rPPG regions of interest (forehead + upper cheeks): dashed, labelled, only while a pulse window is being used.
    ctx.save();
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(160,215,196,0.95)";
    ctx.fillStyle = "rgba(160,215,196,0.14)";
    for (const [name, pts] of Object.entries(vision.overlay)) {
      if (!name.startsWith("roi_") || !pts.length) continue;
      path(pts, true);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
    const fh = vision.overlay.roi_forehead;
    if (fh?.length) {
      const top = Math.min(...fh.map((p) => p[1])), left = Math.min(...fh.map((p) => p[0]));
      ctx.font = "11px ui-monospace, Consolas, monospace";
      ctx.fillStyle = "rgba(251,250,246,0.95)";
      ctx.fillText("rPPG zone", left * W, Math.max(12, top * H - 4));
    }
    // Asymmetry vectors: mouth-corner and brow lines with a level reference from the image-left point.
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = over ? "rgba(255,142,128,1)" : "rgba(251,250,246,0.9)";
    ctx.fillStyle = ctx.strokeStyle;
    for (const key of ["asym_mouth", "asym_brow"]) {
      const p = vision.overlay[key];
      if (!p || p.length < 2) continue;
      ctx.setLineDash([]);
      path(p, false);
      ctx.stroke();
      ctx.setLineDash([2, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(p[0][0] * W, p[0][1] * H);
      ctx.lineTo(p[1][0] * W, p[0][1] * H);
      ctx.stroke();
      ctx.lineWidth = 2;
      for (const [x, y] of p) {
        ctx.beginPath();
        ctx.arc(x * W, y * H, 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
    if (vision.asymmetry_pct != null && vision.overlay.asym_mouth) {
      const [x, y] = vision.overlay.asym_mouth[1];
      ctx.font = "11px ui-monospace, Consolas, monospace";
      ctx.fillStyle = "rgba(251,250,246,0.95)";
      ctx.fillText(`asym ${vision.asymmetry_pct.toFixed(1)}%`, Math.min(x * W + 8, W - 84), y * H + 16);
    }
  }, [vision, live, intake.unified]);

  const faceChip = !live ? null : !vision ? (
    <Badge tone="neutral">Awaiting first analysed frame</Badge>
  ) : vision.state === "unavailable" ? (
    <Badge tone="amber">Vision unavailable</Badge>
  ) : vision.face_detected ? (
    <Badge tone={vision.state === "low_quality" ? "amber" : "sage"}>Face tracked</Badge>
  ) : (
    <Badge tone="slate">No face detected</Badge>
  );

  return (
    <div className="flex shrink-0 flex-col gap-2">
      {/* The stage takes the real camera aspect ratio so the video is never stretched and the
          normalised landmark overlay maps 1:1 onto the frame. Height is capped for laptop screens. */}
      <div
        className="relative mx-auto shrink-0 overflow-hidden rounded-md bg-ink"
        style={{ aspectRatio: String(aspect), width: `min(100%, calc(min(50vh, 480px) * ${aspect}))` }}
        data-testid="camera-stage"
      >
        <video
          ref={videoRef}
          playsInline
          muted
          aria-label="Live camera preview"
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            if (v.videoWidth && v.videoHeight) setAspect(v.videoWidth / v.videoHeight);
          }}
          className={`absolute inset-0 h-full w-full object-fill ${live ? "" : "invisible"}`}
        />
        <canvas ref={canvasRef} width={640} height={480} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />
        <div className="crop-marks pointer-events-none absolute inset-3" aria-hidden />
        {!live && (
          <div className="absolute inset-0 flex items-center justify-center p-8 text-center">
            <p role="status" className="max-w-[34ch] text-[13px] text-paper/85" data-testid="camera-message">{stageMessage(camera)}</p>
          </div>
        )}
        <div className="absolute left-3 top-3 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1.5 rounded-sm bg-ink/70 px-1.5 py-1 text-[11px] font-medium text-paper">
            <Dot tone={live ? "sage" : "idle"} />
            {live ? "Camera live" : `Camera ${camera.status}`}
          </span>
          {faceChip}
          {session?.synthetic && <Badge tone="amber">SYNTHETIC DEMO</Badge>}
        </div>
        {intake.unified?.priority.level != null && (
          <div className="absolute right-3 top-3 flex flex-col items-end gap-1" data-testid="stage-priority">
            <span className={`rounded-md border px-2.5 py-1 text-[13px] font-semibold ${{ 1: "border-alert bg-alert text-paper", 2: "border-esi2 bg-esi2 text-paper", 3: "border-esi3/40 bg-esi3-soft text-esi3" }[intake.unified.priority.level] ?? ""}`}>
              Level {intake.unified.priority.level}
            </span>
            {intake.unified.guardrail_status.hard_override_triggered && (
              <span className="rounded-sm bg-alert px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-paper">GUARDRAIL: ACTIVE OVERRIDE</span>
            )}
          </div>
        )}
        {live && vision?.state === "low_quality" && (
          <div className="absolute bottom-3 left-3 right-3 rounded-sm bg-amber-soft/95 px-2.5 py-1.5 text-[12px] text-amber" role="status">
            Insufficient signal: {vision.reason}. Visual measurements are paused for this frame.
          </div>
        )}
        {live && vision && !vision.face_detected && vision.state === "not_detected" && (
          <div className="absolute bottom-3 left-3 right-3 rounded-sm bg-paper/90 px-2.5 py-1.5 text-[12px] text-ink-2" role="status">
            Visual analysis unavailable — no face in frame. This is not a normal finding.
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Capture controls">
        {status === "setup" && (
          <Button variant="primary" onClick={() => void intake.start()} disabled={busy}>
            {busy ? "Starting…" : "Start capture"}
          </Button>
        )}
        {status === "live" && (
          <>
            <Button onClick={() => void intake.pause()}>Pause</Button>
            <Button variant="danger" onClick={() => void intake.stop()}>Stop session</Button>
          </>
        )}
        {status === "paused" && (
          <>
            <Button variant="primary" onClick={() => void intake.resume()} disabled={busy}>Resume</Button>
            <Button variant="danger" onClick={() => void intake.stop()}>Stop session</Button>
          </>
        )}
        {status === "stopped" && <Badge tone="slate">Session stopped · devices released</Badge>}
        <div className="ml-auto flex items-center gap-2 text-[12px] text-ink-3">
          <span>Mic: <b className="font-medium text-ink-2" data-testid="mic-status">{mic.status}</b></span>
          <span aria-hidden>·</span>
          <span>Link: <b className="font-medium text-ink-2" data-testid="ws-status">{ws}</b></span>
          {vision && live && <StateChip state={vision.state} />}
        </div>
      </div>
      {(mic.message && mic.status !== "live" && mic.status !== "idle") && (
        <p className="text-[12px] text-amber" role="status" data-testid="mic-message">{mic.message}</p>
      )}
    </div>
  );
}
