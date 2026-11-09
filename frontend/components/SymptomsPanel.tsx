"use client";

import { useState } from "react";
import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";
import type { Mention } from "@/lib/types";
import { Badge, Button, inputCls, Panel } from "./ui";

const POL_TONE = { affirmed: "sage", negated: "slate", uncertain: "amber" } as const;

export function SymptomsPanel({ intake }: { intake: Intake }) {
  const { session, mutate } = intake;
  if (!session) return null;
  const locked = session.review.finalized;
  const pending = session.mentions.filter((m) => m.status === "pending").length;

  return (
    <Panel
      title="Reported symptoms"
      id="symptoms"
      aside={pending ? <Badge tone="slate">{pending} to review</Badge> : session.mentions.length ? <Badge tone="sage">all reviewed</Badge> : undefined}
    >
      <ul className="scroll-thin max-h-[20vh] min-h-[56px] divide-y divide-line-soft overflow-y-auto" data-testid="symptom-list">
        {session.mentions.length === 0 && (
          <li className="px-3 py-2 text-[13px] text-ink-3">No symptom statements extracted yet. Nothing is inferred beyond what is said.</li>
        )}
        {session.mentions.map((m) => (
          <MentionRow key={m.id} m={m} locked={locked} onChange={(b) => void mutate((id, t) => api.reviewSymptom(id, t, m.id, b))} />
        ))}
      </ul>
    </Panel>
  );
}

function MentionRow({ m, locked, onChange }: { m: Mention; locked: boolean; onChange: (b: { status?: string; polarity?: string; onset_text?: string }) => void }) {
  const [edit, setEdit] = useState(false);
  const [pol, setPol] = useState(m.polarity);
  const [onset, setOnset] = useState(m.onset?.text ?? "");
  const muted = m.status === "rejected";
  return (
    <li className={`px-3 py-2 ${muted ? "opacity-55" : ""}`} data-testid="symptom">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`text-[13.5px] font-medium ${muted ? "line-through" : ""}`}>{m.label}</span>
        <Badge tone={POL_TONE[m.polarity]}>{m.polarity}</Badge>
        <Badge tone="neutral">{m.reporter}</Badge>
        {m.sudden && <Badge tone="amber">sudden wording</Badge>}
        {m.status === "confirmed" && <Badge tone="sage">confirmed</Badge>}
        {m.status === "rejected" && <Badge tone="slate">rejected</Badge>}
      </div>
      <p className="text-[12px] text-ink-3">“{m.sentence}”</p>
      <p className="text-[12px] text-ink-2">
        Onset: {m.onset ? <>“{m.onset.text}”{!m.onset.certain && <span className="ml-1 text-amber">(uncertain — not a definite time)</span>}</> : <span className="text-ink-3">not stated</span>}
      </p>
      {!locked && (
        edit ? (
          <div className="mt-1.5 flex flex-wrap items-end gap-2">
            <div>
              <label className="label block" htmlFor={`pol-${m.id}`}>Polarity</label>
              <select id={`pol-${m.id}`} className={`${inputCls} !w-auto`} value={pol} onChange={(e) => setPol(e.target.value as Mention["polarity"])}>
                <option value="affirmed">affirmed</option><option value="negated">negated</option><option value="uncertain">uncertain</option>
              </select>
            </div>
            <div className="min-w-[140px] flex-1">
              <label className="label block" htmlFor={`onset-${m.id}`}>Onset (as stated)</label>
              <input id={`onset-${m.id}`} className={inputCls} value={onset} onChange={(e) => setOnset(e.target.value)} />
            </div>
            <Button small variant="primary" onClick={() => { onChange({ polarity: pol, onset_text: onset }); setEdit(false); }}>Save</Button>
            <Button small variant="ghost" onClick={() => setEdit(false)}>Cancel</Button>
          </div>
        ) : (
          <div className="mt-1 flex gap-1">
            <Button small variant={m.status === "confirmed" ? "primary" : "secondary"} onClick={() => onChange({ status: "confirmed" })} aria-label={`Confirm ${m.label}`}>Confirm</Button>
            <Button small onClick={() => onChange({ status: m.status === "rejected" ? "pending" : "rejected" })} aria-label={`${m.status === "rejected" ? "Restore" : "Reject"} ${m.label}`}>
              {m.status === "rejected" ? "Restore" : "Reject"}
            </Button>
            <Button small variant="quiet" onClick={() => setEdit(true)} aria-label={`Edit ${m.label}`}>Edit</Button>
          </div>
        )
      )}
    </li>
  );
}
