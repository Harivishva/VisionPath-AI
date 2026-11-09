"use client";

import { useState } from "react";
import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";
import type { Segment, Urgency } from "@/lib/types";
import { Badge, Button, inputCls, Panel } from "./ui";

const SOURCE: Record<Segment["source"], { label: string; tone: "sage" | "slate" | "amber" }> = {
  "stt-local": { label: "Speech-to-text · local", tone: "sage" },
  "stt-openai": { label: "Speech-to-text · API", tone: "sage" },
  manual: { label: "Manual entry", tone: "slate" },
  synthetic: { label: "SYNTHETIC DEMO", tone: "amber" },
};

export function TranscriptPanel({ intake }: { intake: Intake }) {
  const { session, mutate, transcribing } = intake;
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [obs, setObs] = useState("");
  const [obsConcerning, setObsConcerning] = useState(false);
  const [obsUrgency, setObsUrgency] = useState<Urgency>("prompt_clinical_review");
  if (!session) return null;
  const stt = session.providers.transcription;
  const tState = session.modalities.transcript.transcription_state;
  const locked = session.review.finalized;

  return (
    <Panel
      title="Transcript"
      grow
      id="transcript"
      aside={
        session.synthetic && !locked ? (
          <Button small variant="quiet" onClick={() => void mutate((id, t) => api.loadSynthetic(id, t), "Synthetic demo statement added (clearly labelled)")}>
            Load synthetic statement
          </Button>
        ) : undefined
      }
    >
      <div className="scroll-thin min-h-[80px] flex-1 space-y-2 overflow-y-auto px-3 py-2" data-testid="transcript-list" aria-live="polite">
        {!stt.ready && (
          <p className="rounded border border-amber/30 bg-amber-soft/60 px-2 py-1.5 text-[12px] text-amber" data-testid="stt-unavailable">
            Speech recognition unavailable: {stt.detail}. Spoken words are not transcribed — type or paste the statement below.
          </p>
        )}
        {(transcribing || tState === "transcribing") && <p className="text-[12px] text-ink-3" role="status">Transcribing the latest utterance…</p>}
        {tState === "error" && <p className="text-[12px] text-alert" role="alert">Transcription failed: {session.modalities.transcript.transcription_detail}</p>}
        {session.segments.length === 0 && <p className="text-[13px] text-ink-3">No transcript yet.</p>}
        {session.segments.map((s) => (
          <article key={s.id} className="group border-l-2 border-line pl-2.5" data-testid="segment">
            <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
              <Badge tone={SOURCE[s.source].tone}>{SOURCE[s.source].label}</Badge>
              <span className="readout text-ink-3">{new Date(s.created_at).toLocaleTimeString()}{s.t_start_s !== null ? ` · ${s.t_start_s.toFixed(1)}–${s.t_end_s?.toFixed(1)}s` : ""}</span>
              {s.edited && <Badge tone="slate">corrected</Badge>}
            </div>
            {editing === s.id ? (
              <div className="space-y-1.5">
                <label className="sr-only" htmlFor={`edit-${s.id}`}>Correct transcript text</label>
                <textarea id={`edit-${s.id}`} rows={3} className={inputCls} value={editText} onChange={(e) => setEditText(e.target.value)} />
                <div className="flex gap-1.5">
                  <Button small variant="primary" disabled={!editText.trim()} onClick={async () => {
                    await mutate((id, t) => api.editTranscript(id, t, s.id, editText.trim()), "Transcript corrected; symptoms re-extracted");
                    setEditing(null);
                  }}>Save correction</Button>
                  <Button small variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                </div>
              </div>
            ) : (
              <>
                <p className="text-[13.5px]">{s.text}</p>
                {s.edited && <p className="text-[11.5px] text-ink-3">Original: <span className="italic">{s.original_text}</span></p>}
                {!locked && (
                  <Button small variant="quiet" className="mt-0.5" onClick={() => { setEditing(s.id); setEditText(s.text); }} aria-label={`Correct transcript segment: ${s.text.slice(0, 40)}`}>
                    Correct
                  </Button>
                )}
              </>
            )}
          </article>
        ))}
      </div>

      {!locked && (
        <div className="space-y-2 border-t border-line-soft p-3">
          <label htmlFor="manual-transcript" className="label">Add statement manually</label>
          <textarea id="manual-transcript" rows={2} className={inputCls} value={draft} maxLength={4000} onChange={(e) => setDraft(e.target.value)} placeholder="Type or paste what the patient or observer said…" />
          <div className="flex items-center justify-between">
            <Button small variant="primary" disabled={!draft.trim()} onClick={async () => {
              const r = await mutate((id, t) => api.addTranscript(id, t, draft.trim()));
              if (r) setDraft("");
            }}>Add to transcript</Button>
          </div>
          <details className="text-[12px]">
            <summary className="cursor-pointer text-ink-2">Record a staff observation</summary>
            <div className="mt-2 space-y-1.5">
              <label htmlFor="obs-text" className="sr-only">Staff observation</label>
              <textarea id="obs-text" rows={2} className={inputCls} value={obs} maxLength={1000} onChange={(e) => setObs(e.target.value)} placeholder="e.g. Patient appears pale and diaphoretic" />
              <label className="flex items-center gap-2"><input type="checkbox" className="accent-[var(--color-sage)]" checked={obsConcerning} onChange={(e) => setObsConcerning(e.target.checked)} /> Mark as concerning (raises an alert at the urgency you choose)</label>
              <div className="flex items-center gap-2">
                <label htmlFor="obs-urgency" className="label">Urgency</label>
                <select id="obs-urgency" className={`${inputCls} !w-auto`} value={obsUrgency} onChange={(e) => setObsUrgency(e.target.value as Urgency)}>
                  <option value="immediate_human_review">Immediate human review</option>
                  <option value="prompt_clinical_review">Prompt clinical review</option>
                  <option value="routine_review">Routine review</option>
                </select>
                <Button small disabled={!obs.trim()} onClick={async () => {
                  const r = await mutate((id, t) => api.addObservation(id, t, { text: obs.trim(), concerning: obsConcerning, urgency: obsUrgency }), "Staff observation recorded");
                  if (r) { setObs(""); setObsConcerning(false); }
                }}>Add observation</Button>
              </div>
            </div>
          </details>
        </div>
      )}
    </Panel>
  );
}
