"use client";

import { useRef } from "react";
import type { Intake } from "@/lib/useIntake";
import { Badge, Button, Dot } from "./ui";

export type Tab = "intake" | "summary" | "fhir" | "diagnostics";
export const TABS: { id: Tab; label: string }[] = [
  { id: "intake", label: "Intake" },
  { id: "summary", label: "Summary" },
  { id: "fhir", label: "FHIR export" },
  { id: "diagnostics", label: "Diagnostics" },
];

const STATUS_TONE = { setup: "idle", live: "sage", paused: "amber", stopped: "idle" } as const;
const STATUS_LABEL = { setup: "Ready", live: "Live", paused: "Paused", stopped: "Stopped" } as const;

export function Header({ intake, tab, onTab }: { intake: Intake; tab: Tab; onTab: (t: Tab) => void }) {
  const { session } = intake;
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKey = (e: React.KeyboardEvent, i: number) => {
    let n = i;
    if (e.key === "ArrowRight") n = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") n = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = TABS.length - 1;
    else return;
    e.preventDefault();
    onTab(TABS[n].id);
    refs.current[TABS[n].id]?.focus();
  };

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 overflow-x-auto border-b border-line bg-paper px-4 whitespace-nowrap">
      <div className="flex shrink-0 items-baseline gap-2">
        <span className="font-serif text-[19px] font-medium tracking-tight">VisionPath <span className="text-sage">AI</span></span>
        <span className="hidden text-[11px] text-ink-3 xl:inline">decision-support prototype · not a diagnostic device</span>
      </div>

      <nav role="tablist" aria-label="Workspace sections" className="ml-2 flex h-full shrink-0 items-end gap-1">
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => { refs.current[t.id] = el; }}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="main-panel"
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => onTab(t.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={`-mb-px h-full border-b-2 px-3 text-[13px] ${tab === t.id ? "border-sage font-semibold text-ink" : "border-transparent text-ink-3 hover:text-ink"}`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="ml-auto flex shrink-0 items-center gap-3">
        {session && (
          <>
            <span className="readout text-ink-2" data-testid="session-ref">{session.reference}</span>
            {session.synthetic && <Badge tone="amber">SYNTHETIC DEMO</Badge>}
            <span className="inline-flex items-center gap-1.5 text-[12px] font-medium" data-testid="session-status" role="status">
              <Dot tone={STATUS_TONE[session.status]} /> {STATUS_LABEL[session.status]}
            </span>
            <Button small variant="ghost" onClick={() => void intake.reset()} title="Deletes session data on the server and releases devices">
              End &amp; delete session
            </Button>
          </>
        )}
      </div>
    </header>
  );
}
