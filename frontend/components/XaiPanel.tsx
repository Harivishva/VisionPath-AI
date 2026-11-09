"use client";

import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";
import type { UnifiedMetrics } from "@/lib/types";
import { Badge, Button, Metric, Panel } from "./ui";

const ESI_STYLE: Record<number, string> = {
  1: "bg-alert text-paper border-alert",
  2: "bg-esi2 text-paper border-esi2",
  3: "bg-esi3-soft text-esi3 border-esi3/40",
};

const BARS: { key: keyof UnifiedMetrics["xai_attributions"]; label: string; bar: string }[] = [
  { key: "asymmetry_weight_pct", label: "Visual asymmetry", bar: "bg-sage" },
  { key: "heart_rate_weight_pct", label: "rPPG heart rate", bar: "bg-sage-strong" },
  { key: "gasping_weight_pct", label: "Acoustic gasping", bar: "bg-esi2" },
  { key: "transcript_weight_pct", label: "Transcribed symptoms", bar: "bg-ink-2" },
];

const num = (n: number | null | undefined, d = 1, unit = "") => (n === null || n === undefined ? "—" : `${n.toFixed(d)}${unit}`);

export function PriorityBadge({ u }: { u: UnifiedMetrics | null }) {
  const lvl = u?.priority.level ?? null;
  if (!u || lvl === null) {
    return (
      <div className="flex items-center gap-2" data-testid="priority-badge">
        <span className="inline-flex h-9 min-w-[88px] items-center justify-center rounded-md border border-line bg-sunken px-3 text-[13px] font-medium text-ink-2">No level</span>
        <span className="text-[12px] text-ink-3">No priority-driving signal yet. Not a clearance.</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2" data-testid="priority-badge">
      <span
        className={`inline-flex h-9 min-w-[88px] items-center justify-center rounded-md border px-3 text-[15px] font-semibold ${ESI_STYLE[lvl] ?? ESI_STYLE[3]}`}
        aria-label={`Prototype priority level ${lvl}`}
      >
        Level {lvl}
      </span>
      <span className="text-[12px] text-ink-3">
        {u.priority.source === "guardrail" ? "Forced by a deterministic guardrail" : "From transcript / staff rules"}
      </span>
    </div>
  );
}

const m_facs = (u: UnifiedMetrics | null) => u?.extracted_metrics.vision.facs_action_units ?? null;

export function XaiPanel({ intake }: { intake: Intake }) {
  const { unified: u, dsp, audioQuality, vision, session, token, guard } = intake;
  const wr = u?.waiting_room_monitoring;
  const fa = m_facs(u);
  const g = u?.guardrail_status;
  const m = u?.extracted_metrics;
  const attrib = u?.xai_attributions;
  const anyAttrib = !!attrib && BARS.some((b) => (attrib[b.key] as number | null) !== null);
  const rppg = vision?.rppg ?? null;

  return (
    <Panel title="Priority & explainability" aside={<Badge tone="amber">prototype</Badge>} id="xai">
      <div className="space-y-3 px-3 py-2.5">
        <PriorityBadge u={u} />

        <div data-testid="visual-findings">
          <div className="label mb-1">Visual signs (experimental, for staff review)</div>
          {u?.visual_findings?.length ? (
            <ul className="space-y-1">
              {u.visual_findings.map((f) => (
                <li key={f.signal} className={`rounded-md border px-2.5 py-1.5 text-[12px] ${f.level === "alert" ? "border-alert/50 bg-alert-soft text-alert" : "border-amber/40 bg-sunken text-ink"}`}>
                  <b className="font-semibold">{f.label}</b>
                  <span className="readout ml-1.5">{f.value}{f.unit === "%" || f.unit === "BPM" ? f.unit : ""}</span>
                  <div className="text-ink-3">{f.evidence}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12px] text-ink-3">No visual sign is currently above its notice level. This is not a clearance: it can also mean the face is not usable (see the camera status).</p>
          )}
        </div>

        <div
          role="status"
          data-testid="guardrail-status"
          className={`rounded-md border px-2.5 py-1.5 text-[12px] ${g?.hard_override_triggered ? "border-alert/50 bg-alert-soft text-alert" : "border-line bg-sunken text-ink-2"}`}
        >
          <b className="font-semibold">Guardrail Status: {g?.hard_override_triggered ? "ACTIVE OVERRIDE" : "monitoring"}</b>
          {g?.hard_override_triggered ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink">
              {g.triggered_rules.map((r) => (
                <li key={r.id}>
                  <span className="readout">{r.id}</span> — {r.evidence}
                </li>
              ))}
            </ul>
          ) : (
            <span className="ml-1 text-ink-3">no red-flag threshold is currently met by fresh data</span>
          )}
        </div>

        <div data-testid="attributions">
          <div className="label mb-1">Signal contribution (SHAP-inspired)</div>
          {anyAttrib ? (
            <ul className="space-y-1" role="list">
              {BARS.map((b) => {
                const v = attrib![b.key] as number | null;
                return (
                  <li key={b.key} className="grid grid-cols-[132px_minmax(0,1fr)_36px] items-center gap-2 text-[12px]">
                    <span className="truncate text-ink-2">{b.label}</span>
                    <div className="h-2 overflow-hidden rounded-full bg-sunken" role="meter" aria-label={`${b.label} contribution`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v ?? 0}>
                      <div className={`h-full ${b.bar} transition-[width] duration-200`} style={{ width: `${v ?? 0}%` }} />
                    </div>
                    <span className="readout text-right">{v === null ? "—" : `${v}%`}</span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[12px] text-ink-3">No signal is contributing to a priority yet.</p>
          )}
          <p className="mt-1 text-[11px] text-ink-3">
            Weighted share of each signal's strength. Not Shapley values and not an explanation of a learned model.
          </p>
        </div>

        <div className="grid grid-cols-3 gap-x-3 gap-y-2" data-testid="xai-metrics">
          <Metric label="rPPG HR" value={m?.vision.rppg_heart_rate_bpm != null ? `${m.vision.rppg_heart_rate_bpm.toFixed(0)} BPM` : "—"} hint={rppg?.reason ?? "Experimental pulse estimate from facial colour; not a validated vital sign"} />
          <Metric label="HR strength" value={num(m?.vision.rppg_confidence, 2)} hint="Share of pulse-band power at the peak (0–1). Not a probability." />
          <Metric label="Asymmetry" value={num(m?.vision.facial_asymmetry_pct, 1, "%")} hint="Experimental geometry, % of inter-ocular distance" />
          <Metric label="Speech ratio" value={num(m?.audio_dsp.speech_to_breath_ratio, 2)} hint="Voiced speech time ÷ (speech + pause time) over the last ~15 s" />
          <Metric label="Gasp bursts" value={m?.audio_dsp.gasping_detected == null ? "—" : m.audio_dsp.gasping_detected ? "detected" : "none"} hint="Breath-like noise bursts inside pauses. An acoustic observation, not a respiratory measurement." />
          <Metric label="AU04 brow" value={num(fa?.au04_distress, 2)} hint="Brow-lowering proxy vs. this session's baseline (0–1). Experimental geometry, not FACS coding." />
          <Metric label="AU09 nose" value={num(fa?.au09_pain, 2)} hint="Nasal-width change proxy vs. baseline (0–1). Not a pain measurement." />
          <Metric label="Lip velocity asym." value={num(fa?.au12_14_palsy_velocity, 3)} hint="Mirror-asymmetry of lip-corner velocity (inter-ocular units/s). Static asymmetry gives ~0." />
          <Metric label="F0 variation" value={num(m?.audio_dsp.vocal_jitter, 3)} hint="Frame-to-frame pitch variation (not clinical jitter)" />
        </div>

        <div className="space-y-0.5 text-[12px] text-ink-3" data-testid="xai-status-lines">
          <p>
            <span className="label mr-1.5">rPPG</span>
            {rppg ? (rppg.heart_rate_bpm != null ? `estimating over ${rppg.window_s.toFixed(1)} s at ${rppg.effective_fps ?? "?"} fps` : rppg.reason) : "not measured yet"}
          </p>
          <p>
            <span className="label mr-1.5">Audio</span>
            {audioQuality ? (
              <>
                {audioQuality.audio_quality === "no_speech"
                  ? "Sound detected but no clear voice (background noise). Nothing was transcribed."
                  : audioQuality.audio_quality === "too_quiet"
                  ? "Quiet audio: speak closer or louder. Nothing is transcribed unless a voice is clearly present."
                  : audioQuality.audio_quality === "noisy"
                    ? "Noisy environment."
                    : "Good quality."}{" "}
                SNR {num(audioQuality.snr_db, 1, " dB")} · noise filter {audioQuality.noise_filter_applied ? "applied" : "not applied"}
              </>
            ) : (
              "no utterance analysed yet"
            )}
            {dsp?.reason ? ` · ${dsp.reason}` : ""}
          </p>
        </div>
        <div className="space-y-0.5 text-[12px] text-ink-3" data-testid="speech-normalized">
          <p>
            <span className="label mr-1.5">Languages</span>
            {u?.extracted_metrics.speech.detected_languages.length ? u.extracted_metrics.speech.detected_languages.join(", ") : "none detected yet"}
          </p>
          <p>
            <span className="label mr-1.5">Normalized symptoms</span>
            {u?.extracted_metrics.speech.normalized_clinical_symptoms.length ? u.extracted_metrics.speech.normalized_clinical_symptoms.join("; ") : "none"}
            <span className="ml-1">(lexicon suggestions for staff review)</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2" data-testid="waiting-room">
          <Button
            onClick={() => session && void guard(() => api.waitingRoom(session.id, token!, wr?.is_monitored ? "stop" : "start"))}
            disabled={!session}
          >
            {wr?.is_monitored ? "Stop waiting-room monitor" : "Start waiting-room monitor"}
          </Button>
          <span className="text-[12px] text-ink-3">
            {wr?.is_monitored
              ? `${wr.phase === "calibrating" ? "Capturing baseline" : "Monitoring"}${wr.baseline_hr_delta ? ` · HR ${wr.baseline_hr_delta} vs baseline` : ""} · trend check every 5 min`
              : "Off. Compares heart rate and the AU09 proxy with the registration baseline."}
          </span>
        </div>
        {g && <p className="text-[11px] text-ink-3">{g.disclaimer}</p>}
      </div>
    </Panel>
  );
}
