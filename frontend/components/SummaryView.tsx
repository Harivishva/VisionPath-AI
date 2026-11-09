"use client";

import { useEffect, useState } from "react";
import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";
import { STATE_LABEL } from "@/lib/types";
import { Badge, Button, inputCls, Panel, StateChip, UrgencyBadge } from "./ui";

export function SummaryView({ intake }: { intake: Intake }) {
  const { session, mutate, guard, setNotice } = intake;
  const [cc, setCc] = useState("");
  const [notes, setNotes] = useState("");
  const [text, setText] = useState("");
  const [reviewer, setReviewer] = useState("");

  // Each field re-syncs only when ITS server value changes, so unsaved local edits in the other
  // fields are never overwritten by an unrelated snapshot update.
  const rv = session?.review;
  useEffect(() => { if (rv) setCc(rv.chief_complaint); }, [rv?.chief_complaint]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (rv) setNotes(rv.clinician_notes); }, [rv?.clinician_notes]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (rv) setText(rv.summary_text); }, [rv?.summary_text]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (rv) setReviewer(rv.reviewer); }, [rv?.reviewer]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!session) return null;
  const r = session.review;
  const locked = r.finalized;
  const live = session.mentions.filter((m) => m.status !== "rejected");
  const vm = session.modalities.vision;
  const am = session.modalities.audio;
  const llm = session.providers.llm;

  const draft = async (mode: "deterministic" | "llm") => {
    const res = await guard(() => api.draftSummary(session.id, intake.token!, mode));
    if (res) {
      setText(res.text);
      setNotice({ kind: res.notice ? "info" : "success", text: res.notice ?? "Draft generated. Review and edit before finalizing." });
      await mutate((id, t) => api.get(id, t));
    }
  };

  return (
    <div className="mx-auto grid max-w-[1180px] gap-3 p-4 lg:grid-cols-2" data-testid="summary-view">
      <div className="space-y-3">
        <Panel title="Chief complaint" id="cc">
          <div className="space-y-2 p-3">
            <label htmlFor="cc-input" className="sr-only">Chief complaint</label>
            <input id="cc-input" className={inputCls} value={cc} disabled={locked} maxLength={300} onChange={(e) => setCc(e.target.value)} placeholder="Staff-confirmed chief complaint" />
            {r.chief_complaint_suggestion && !r.chief_complaint && !locked && (
              <p className="text-[12px] text-ink-3">
                Machine suggestion (unconfirmed): <b className="font-medium text-ink-2">{r.chief_complaint_suggestion}</b>{" "}
                <Button small variant="quiet" onClick={() => setCc(r.chief_complaint_suggestion)}>Use suggestion</Button>
              </p>
            )}
          </div>
        </Panel>

        <Panel title="Patient / observer-reported" aside={<Badge tone="slate">as stated</Badge>}>
          <ul className="divide-y divide-line-soft" data-testid="summary-reported">
            {live.length === 0 && <li className="p-3 text-[13px] text-ink-3">None recorded.</li>}
            {live.map((m) => (
              <li key={m.id} className="p-3 text-[13px]">
                <b className="font-medium">{m.label}</b> — {m.polarity}{" "}
                <Badge tone={m.status === "confirmed" ? "sage" : "slate"}>{m.status}</Badge>
                <p className="text-[12px] text-ink-3">“{m.sentence}”{m.onset ? ` · onset “${m.onset.text}”${m.onset.certain ? "" : " (uncertain)"}` : ""}</p>
              </li>
            ))}
            {session.observations.map((o) => (
              <li key={o.id} className="p-3 text-[13px]"><Badge tone="slate">staff</Badge> {o.text} {o.concerning && <Badge tone="amber">flagged concerning</Badge>}</li>
            ))}
          </ul>
        </Panel>

        <Panel title="Observed technical signals" aside={<Badge tone="amber">experimental</Badge>}>
          <dl className="grid grid-cols-[1fr_auto] gap-y-1.5 p-3 text-[13px]">
            <dt className="text-ink-2">Camera</dt><dd><StateChip state={vm.state} /></dd>
            <dd className="col-span-2 -mt-1 text-[12px] text-ink-3">
              {vm.frames_processed} frames processed · {vm.accepted_frames} usable for face geometry · {vm.low_quality_frames} low quality
              {vm.reason ? ` · ${vm.reason}` : ""}
            </dd>
            <dt className="text-ink-2">Microphone</dt><dd><StateChip state={am.state} /></dd>
            <dd className="col-span-2 -mt-1 text-[12px] text-ink-3">{am.speech_segments} speech segment(s) · {am.speech_seconds}s{am.reason ? ` · ${am.reason}` : ""}</dd>
            <dt className="text-ink-2">Transcript</dt><dd><StateChip state={session.modalities.transcript.state} /></dd>
          </dl>
          <p className="border-t border-line-soft px-3 py-2 text-[12px] text-ink-3">
            “{STATE_LABEL.not_measured}”, “{STATE_LABEL.not_detected}”, “{STATE_LABEL.unavailable}” and “{STATE_LABEL.low_quality}” are different conditions and none of them is a normal finding.
          </p>
        </Panel>
      </div>

      <div className="space-y-3">
        <Panel title="Machine interpretation · safety rules" aside={<UrgencyBadge level={session.indicator.level} />}>
          <ul className="divide-y divide-line-soft text-[13px]">
            {session.alerts.length === 0 && <li className="p-3 text-ink-3">No rule triggered (not a clearance).</li>}
            {session.alerts.map((a) => (
              <li key={a.id} className={`p-3 ${a.state === "withdrawn" ? "opacity-55" : ""}`}>
                <b className="font-medium">{a.title}</b> <span className="readout text-ink-3">{a.rule_id}</span> <Badge tone="slate">{a.state}</Badge>
                <p className="text-[12px] text-ink-3">{a.evidence.map((e) => e.text).join(" · ")}</p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Items requiring clinician verification">
          <ul className="list-disc space-y-0.5 p-3 pl-7 text-[13px] text-ink-2" data-testid="verification-list">
            {[...session.missing_information, ...live.filter((m) => m.status === "pending").map((m) => `${m.label}: machine-extracted, not yet confirmed`)].map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </Panel>

        <Panel title="Clinician notes & summary" id="notes">
          <div className="space-y-2 p-3">
            <label htmlFor="notes-input" className="label">Clinician notes</label>
            <textarea id="notes-input" rows={2} className={inputCls} value={notes} disabled={locked} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="summary-input" className="label">Intake summary (editable draft)</label>
              <div className="flex gap-1.5">
                <Button small onClick={() => void draft("deterministic")} disabled={locked}>Generate draft</Button>
                <Button small onClick={() => void draft("llm")} disabled={locked || !llm.ready} title={llm.ready ? "" : llm.detail}>Draft with LLM</Button>
              </div>
            </div>
            <textarea id="summary-input" rows={9} className={`${inputCls} font-mono !text-[12px]`} value={text} disabled={locked} onChange={(e) => setText(e.target.value)} />
            <p className="text-[12px] text-ink-3">Origin: {r.summary_origin.replace("_", " ")}{!llm.ready && " · LLM not configured: deterministic drafts only"}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" disabled={locked} onClick={async () => {
                await mutate((id, t) => api.editSummary(id, t, { chief_complaint: cc, clinician_notes: notes, summary_text: text }), "Review edits saved");
              }}>Save edits</Button>
              {!locked ? (
                <>
                  <label htmlFor="reviewer" className="sr-only">Reviewer</label>
                  <input id="reviewer" className={`${inputCls} !w-[150px]`} placeholder="Reviewer initials" value={reviewer} maxLength={80} onChange={(e) => setReviewer(e.target.value)} />
                  <Button onClick={async () => {
                    await mutate((id, t) => api.editSummary(id, t, { chief_complaint: cc, clinician_notes: notes, summary_text: text }));
                    await mutate((id, t) => api.review(id, t, true, reviewer), "Review finalized");
                  }}>Finalize review</Button>
                </>
              ) : (
                <>
                  <Badge tone="sage">Finalized {r.finalized_at ? new Date(r.finalized_at).toLocaleTimeString() : ""}</Badge>
                  <Button small onClick={() => void mutate((id, t) => api.review(id, t, false), "Review reopened")}>Reopen</Button>
                </>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
