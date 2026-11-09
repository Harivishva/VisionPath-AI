"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { STATE_LABEL, URGENCY_LABEL, type DataState, type Urgency } from "@/lib/types";

type Tone = "neutral" | "sage" | "amber" | "alert" | "slate";

const TONE: Record<Tone, string> = {
  neutral: "bg-sunken text-ink-2 border-line",
  sage: "bg-sage-soft text-sage-strong border-sage/30",
  amber: "bg-amber-soft text-amber border-amber/30",
  alert: "bg-alert-soft text-alert border-alert/30",
  slate: "bg-slate-soft text-ink-2 border-line",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-medium leading-none ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: "sage" | "amber" | "alert" | "idle" }) {
  const c = { sage: "bg-sage", amber: "bg-amber", alert: "bg-alert", idle: "bg-ink-3/50" }[tone];
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 rounded-full ${c} ${tone === "sage" ? "pulse-dot" : ""}`} />;
}

const STATE_TONE: Record<DataState, Tone> = {
  not_measured: "neutral",
  not_detected: "slate",
  unavailable: "amber",
  low_quality: "amber",
  pending_review: "slate",
  available: "sage",
};

/** Missing-data states are visually and textually distinct; never interchangeable. */
export function StateChip({ state }: { state: DataState }) {
  return <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>;
}

export function UrgencyBadge({ level }: { level: Urgency }) {
  const tone: Tone = { immediate_human_review: "alert", prompt_clinical_review: "amber", routine_review: "sage", insufficient_information: "neutral" }[level] as Tone;
  return <Badge tone={tone}>{URGENCY_LABEL[level]}</Badge>;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "quiet"; small?: boolean };

export function Button({ variant = "secondary", small, className = "", ...rest }: BtnProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 select-none";
  const size = small ? "px-2.5 py-1 text-[12px]" : "px-3.5 py-1.5 text-[13px]";
  const v = {
    primary: "bg-sage text-paper border-sage-strong hover:bg-sage-strong",
    secondary: "bg-paper text-ink border-line hover:bg-sunken",
    ghost: "border-transparent text-ink-2 hover:bg-sunken",
    quiet: "border-transparent text-sage-strong hover:underline px-1",
    danger: "bg-paper text-alert border-alert/40 hover:bg-alert-soft",
  }[variant];
  return <button type="button" className={`${base} ${size} ${v} ${className}`} {...rest} />;
}

export function Panel({
  title, aside, children, className = "", id, grow,
}: {
  title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; id?: string; grow?: boolean;
}) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-h` : undefined}
      className={`flex min-h-0 flex-col overflow-hidden rounded-md border border-line bg-paper ${grow ? "min-h-[250px] flex-1" : "shrink-0"} ${className}`}
    >
      {(title || aside) && (
        <header className="flex items-center justify-between gap-2 border-b border-line-soft px-3 py-2">
          <h2 id={id ? `${id}-h` : undefined} className="label">{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor: string }) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="label block">{label}</label>
      {children}
      {hint && <p className="text-[12px] text-ink-3">{hint}</p>}
    </div>
  );
}

export const inputCls =
  "w-full rounded-md border border-line bg-paper px-2.5 py-1.5 text-[13px] text-ink placeholder:text-ink-3/70 focus:border-sage";

export function Notice({ kind, children, onClose }: { kind: "error" | "info" | "success" | "warn"; children: ReactNode; onClose?: () => void }) {
  const cls = {
    error: "border-alert/40 bg-alert-soft text-alert",
    warn: "border-amber/40 bg-amber-soft text-amber",
    info: "border-line bg-sunken text-ink-2",
    success: "border-sage/40 bg-sage-soft text-sage-strong",
  }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-[13px] ${cls}`}>
      <div>{children}</div>
      {onClose && (
        <button type="button" aria-label="Dismiss message" onClick={onClose} className="-mr-1 rounded px-1 text-base leading-none opacity-70 hover:opacity-100">
          ×
        </button>
      )}
    </div>
  );
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div title={hint} className="min-w-0">
      <div className="label">{label}</div>
      <div className="readout truncate text-ink">{value}</div>
    </div>
  );
}
