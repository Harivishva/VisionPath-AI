"use client";

import type { Alert, SessionSnapshot } from "@/lib/types";
import { Badge, Button, UrgencyBadge } from "./ui";

const BORDER: Record<string, string> = {
  immediate_human_review: "border-alert bg-alert-soft/60",
  prompt_clinical_review: "border-amber/60 bg-amber-soft/50",
  routine_review: "border-line bg-paper",
  insufficient_information: "border-line bg-paper",
};

export function AlertsPanel({ session, onAck }: { session: SessionSnapshot; onAck: (id: string) => void }) {
  const ind = session.indicator;
  const active = session.alerts.filter((a) => a.state !== "withdrawn");
  const withdrawn = session.alerts.filter((a) => a.state === "withdrawn");
  const heading =
    ind.level === "immediate_human_review"
      ? "border-alert bg-alert text-paper"
      : ind.level === "prompt_clinical_review"
        ? "border-amber bg-amber-soft text-amber"
        : "border-line bg-sunken text-ink";

  return (
    <section aria-labelledby="alerts-h" className="flex shrink-0 flex-col overflow-hidden rounded-md border border-line bg-paper" data-testid="alerts-panel">
      <div className={`rounded-t-md border-b px-3 py-2 ${heading}`}>
        <div className="flex items-baseline justify-between gap-2">
          <h2 id="alerts-h" className="label !text-inherit opacity-80">Prototype urgency indicator</h2>
          <span className="text-[10.5px] opacity-80">not ESI · workflow aid</span>
        </div>
        <p className="font-serif text-[19px] leading-tight" data-testid="indicator-label">{ind.label}</p>
        <p className="text-[12px] opacity-90">{ind.basis}</p>
      </div>
      <div className="scroll-thin max-h-[26vh] min-h-[56px] space-y-2 overflow-y-auto p-2" aria-live="polite" aria-relevant="additions">
        {active.length === 0 && (
          <p className="px-1 py-1 text-[12.5px] text-ink-3">
            No configured rule has been triggered. This is not a clearance — the absence of an alert never means the patient is well.
          </p>
        )}
        {active.map((a) => (
          <AlertCard key={a.id} a={a} onAck={onAck} />
        ))}
        {withdrawn.length > 0 && (
          <details className="rounded border border-line-soft px-2 py-1 text-[12px] text-ink-3">
            <summary className="cursor-pointer">{withdrawn.length} withdrawn alert(s) — evidence rejected or edited by staff</summary>
            <ul className="mt-1 space-y-1">
              {withdrawn.map((a) => (
                <li key={a.id}>{a.title} <span className="readout">[{a.rule_id}]</span> — {a.withdrawn_reason}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  );
}

function AlertCard({ a, onAck }: { a: Alert; onAck: (id: string) => void }) {
  const acked = a.state === "acknowledged";
  return (
    <article
      role={a.severity === "immediate_human_review" && !acked ? "alert" : undefined}
      className={`alert-in rounded-md border-l-4 border px-2.5 py-2 ${BORDER[a.severity]} ${acked ? "opacity-70" : ""}`}
      data-testid="alert-card"
      aria-label={`${a.title}, ${acked ? "acknowledged" : "active"}`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <UrgencyBadge level={a.severity} />
        {a.pending_review && <Badge tone="slate" title="Based on machine-extracted text not yet confirmed by staff">pending review</Badge>}
        <span className="readout text-ink-3">{a.rule_id}</span>
      </div>
      <h3 className="mt-1 text-[13.5px] font-semibold">{a.title}</h3>
      <p className="text-[12.5px] text-ink-2">{a.explanation}</p>
      <ul className="mt-1 space-y-0.5">
        {a.evidence.map((e) => (
          <li key={e.ref_id} className="border-l-2 border-line pl-2 text-[12px] text-ink-2">
            <span className="label mr-1">{e.source.replace("_", " ")}{e.reporter !== "unspecified" ? ` · ${e.reporter}` : ""}</span>
            {e.text}
            <span className="readout ml-1 text-ink-3">{new Date(e.timestamp).toLocaleTimeString()}</span>
          </li>
        ))}
      </ul>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <p className="text-[12px] text-ink-3">{a.recommended_action}</p>
        {acked ? (
          <Badge tone="sage">Acknowledged {a.acknowledged_at ? new Date(a.acknowledged_at).toLocaleTimeString() : ""}</Badge>
        ) : (
          <Button small onClick={() => onAck(a.id)} aria-label={`Acknowledge alert: ${a.title}`}>Acknowledge</Button>
        )}
      </div>
    </article>
  );
}
