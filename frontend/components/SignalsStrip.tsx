"use client";

import type { Intake } from "@/lib/useIntake";
import { Badge, Metric, Panel, StateChip } from "./ui";

const fmt = (n: number | null | undefined, d = 1) => (n === null || n === undefined ? "—" : n.toFixed(d));

export function SignalsStrip({ intake }: { intake: Intake }) {
  const { session, vision, audio, stats, transcribing } = intake;
  if (!session) return null;
  const vm = session.modalities.vision;
  const am = session.modalities.audio;
  const stt = session.providers.transcription;
  const level = audio ? Math.max(0, Math.min(1, (audio.rms_dbfs + 70) / 70)) : 0;
  const tState = session.modalities.transcript.transcription_state;

  return (
    <div className="grid gap-2 md:grid-cols-2">
      <Panel
        title="Vision"
        aside={<StateChip state={vm.state} />}
      >
        <div className="grid grid-cols-4 gap-x-3 gap-y-2 px-3 py-2" data-testid="vision-metrics">
          <Metric label="Face" value={vision ? (vision.face_detected ? "detected" : "not detected") : "—"} />
          <Metric label="Model conf." value="n/a" hint="The face-landmark model does not report a confidence value; none is invented." />
          <Metric label="Proc. rate" value={`${fmt(vm.fps)} /s`} hint="Measured frames processed per second on the server" />
          <Metric label="Latency" value={vm.mean_latency_ms ? `${fmt(vm.mean_latency_ms, 0)} ms` : "—"} hint="Mean server processing time per frame" />
          <Metric label="Brightness" value={fmt(vision?.quality?.brightness, 0)} />
          <Metric label="Sharpness" value={fmt(vision?.quality?.sharpness, 0)} />
          <Metric label="Yaw / pitch" value={vision?.head_pose_deg ? `${fmt(vision.head_pose_deg.yaw, 0)}° / ${fmt(vision.head_pose_deg.pitch, 0)}°` : "—"} />
          <Metric label="Frames" value={`${vm.accepted_frames}/${vm.frames_processed}`} hint="Usable for geometry / processed" />
        </div>
        <details className="border-t border-line-soft px-3 py-1.5 text-[12px]">
          <summary className="cursor-pointer text-ink-2">Experimental geometry <Badge tone="amber">experimental</Badge></summary>
          <div className="mt-2 space-y-1" data-testid="geometry">
            {vision?.smoothed ? (
              <>
                <Metric label="Mouth-corner vertical offset" value={fmt(vision.smoothed.mouth_corner_vertical_offset, 3)} />
                <Metric label="Brow height offset" value={fmt(vision.smoothed.brow_height_offset, 3)} />
                <Metric label="Eye-opening offset" value={fmt(vision.smoothed.eye_opening_offset, 3)} />
              </>
            ) : (
              <p className="text-ink-3">{vision?.reason ?? vm.reason ?? "No quality-gated measurement yet."}</p>
            )}
            <p className="text-ink-3">
              Smoothed, roll-compensated geometric differences in inter-ocular units (image-right minus image-left). Descriptive only:
              not a measure of probability, not a clinical measurement, not diagnostic.
            </p>
            <p className="readout text-ink-3">sent {stats.framesSent} · skipped {stats.framesSkipped} · {stats.sendFps} fps out · dropped {vm.frames_dropped}</p>
          </div>
        </details>
      </Panel>

      <Panel title="Audio & speech" aside={<StateChip state={am.state} />}>
        <div className="space-y-2 px-3 py-2" data-testid="audio-metrics">
          <div className="flex items-center gap-2">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-sunken" role="meter" aria-label="Microphone level" aria-valuemin={-70} aria-valuemax={0} aria-valuenow={audio ? Math.round(audio.rms_dbfs) : -70}>
              <div className={`h-full ${audio?.quality === "low_quality" ? "bg-amber" : "bg-sage"} transition-[width] duration-100`} style={{ width: `${level * 100}%` }} />
            </div>
            <span className="readout w-[64px] text-right">{audio ? `${fmt(audio.rms_dbfs, 0)} dBFS` : "—"}</span>
          </div>
          <div className="grid grid-cols-4 gap-x-3">
            <Metric label="Voice" value={audio ? (audio.voice_activity ? "speech" : "silence") : "—"} />
            <Metric label="F0" value={audio?.f0_hz ? `${fmt(audio.f0_hz, 0)} Hz` : audio ? "withheld" : "—"} hint="Pitch is shown only when periodicity is high enough to be reliable" />
            <Metric label="Segments" value={`${am.speech_segments}`} />
            <Metric label="Speech" value={`${fmt(am.speech_seconds)} s`} />
          </div>
          {audio?.quality === "low_quality" && <p className="text-[12px] text-amber">Insufficient audio quality: {audio.quality_reason}</p>}
          <p className="text-[12px]" data-testid="stt-status">
            <span className="label mr-1.5">Transcription</span>
            {transcribing || tState === "transcribing" ? (
              <Badge tone="slate">transcribing…</Badge>
            ) : stt.ready ? (
              <Badge tone="sage">{stt.provider}{stt.model ? ` · ${stt.model}` : ""}</Badge>
            ) : (
              <Badge tone="amber">unavailable</Badge>
            )}
            <span className="ml-1.5 text-ink-3">{tState === "error" || tState === "unavailable" ? session.modalities.transcript.transcription_detail : stt.detail}</span>
          </p>
        </div>
      </Panel>
    </div>
  );
}
