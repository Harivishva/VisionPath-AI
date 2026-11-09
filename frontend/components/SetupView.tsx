"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Diagnostics } from "@/lib/types";
import { Badge, Button, Dot, Field, inputCls, Notice } from "./ui";
import type { CreateSessionInput } from "@/lib/api";

export function SetupView({
  onCreate, busy, error,
}: {
  onCreate: (i: CreateSessionInput) => Promise<boolean>;
  busy: boolean;
  error: string | null;
}) {
  const [reference, setReference] = useState("DEMO-001");
  const [synthetic, setSynthetic] = useState(true);
  const [notes, setNotes] = useState("");
  const [consent, setConsent] = useState(false);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [diagErr, setDiagErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.diagnostics().then(
      (d) => alive && setDiag(d),
      (e) => alive && setDiagErr(e instanceof ApiError ? e.message : "Backend unreachable"),
    );
    return () => { alive = false; };
  }, []);

  const valid = /^[A-Za-z0-9._\- ]{1,40}$/.test(reference.trim());

  return (
    <main className="mx-auto grid min-h-dvh max-w-[1080px] grid-cols-1 gap-10 px-6 py-10 md:grid-cols-[1.15fr_1fr]">
      <div>
        <p className="label">Emergency intake · decision support</p>
        <h1 className="mt-2 font-serif text-[40px] font-medium leading-none tracking-tight">
          VisionPath <span className="text-sage">AI</span>
        </h1>
        <p className="mt-3 max-w-[46ch] text-[14px] text-ink-2">
          Observe, transcribe and organise what is said and seen during intake. Staff review everything; rules
          surface evidence. This prototype never diagnoses.
        </p>

        <form
          className="mt-8 space-y-5"
          onSubmit={async (e) => {
            e.preventDefault();
            if (valid && consent) await onCreate({ reference: reference.trim(), synthetic, patient_reported_notes: notes.trim(), consent_acknowledged: consent });
          }}
          aria-label="New intake session"
        >
          <Field label="Session reference" htmlFor="ref" hint="Non-identifying label. Do not enter a real name, MRN or date of birth.">
            <input id="ref" className={inputCls} value={reference} maxLength={40} onChange={(e) => setReference(e.target.value)} aria-invalid={!valid} />
          </Field>

          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-sage)]" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
            <span>
              <b className="font-medium">Synthetic demonstration session</b>
              <span className="block text-ink-3">Labelled DEMO throughout and tagged as test data in exports. Enables the clearly marked synthetic transcript.</span>
            </span>
          </label>

          <Field label="Patient-reported information (optional)" htmlFor="notes" hint="Typed text becomes the first transcript entry (source: manual).">
            <textarea id="notes" rows={3} className={inputCls} value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} />
          </Field>

          <label className="flex items-start gap-2 rounded-md border border-line bg-paper p-3 text-[13px]">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-sage)]" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I have read the privacy information and confirm that everyone captured is a consenting participant or the data is synthetic.</span>
          </label>

          {error && <Notice kind="error">{error}</Notice>}

          <div className="flex items-center gap-3">
            <Button variant="primary" type="submit" disabled={!valid || !consent || busy || !!diagErr}>
              {busy ? "Creating…" : "Create intake session"}
            </Button>
            <span className="text-[12px] text-ink-3">Camera and microphone are requested only when you press Start capture.</span>
          </div>
        </form>
      </div>

      <aside className="space-y-6 md:pt-[88px]" aria-label="Privacy and system readiness">
        <section>
          <h2 className="label">Privacy</h2>
          <ul className="mt-2 space-y-1.5 text-[13px] text-ink-2">
            <li>No camera or microphone access until you press Start capture; a visible status shows when capture is active.</li>
            <li>Video frames and audio are processed in memory and never written to disk. Session data lives in server memory only and is erased when you delete the session or restart the server.</li>
            <li>Speech recognition runs locally unless an external provider is configured; the status panel below says which.</li>
            <li>Use synthetic or explicitly consented data only. This prototype makes no HIPAA or other regulatory-compliance claim.</li>
          </ul>
        </section>
        <section>
          <h2 className="label">System readiness</h2>
          <dl className="mt-2 divide-y divide-line-soft rounded-md border border-line bg-paper text-[13px]" data-testid="readiness">
            {diagErr ? (
              <div className="p-3"><Notice kind="error">{diagErr}</Notice></div>
            ) : !diag ? (
              <div className="p-3 text-ink-3">Checking backend…</div>
            ) : (
              <>
                <Row k="Backend" v={<span className="inline-flex items-center gap-1.5"><Dot tone="sage" />Connected · v{diag.version}</span>} />
                <Row k="Face tracking model" v={diag.vision.ready ? <Badge tone="sage">Ready</Badge> : <Badge tone="amber" title={diag.vision.error ?? ""}>Unavailable</Badge>} />
                <Row k="Speech-to-text" v={diag.transcription.ready ? <Badge tone="sage">{diag.transcription.provider}</Badge> : <Badge tone="amber">Unavailable</Badge>} sub={diag.transcription.detail} />
                <Row k="LLM summaries" v={diag.llm.ready ? <Badge tone="sage">Configured</Badge> : <Badge>Not configured</Badge>} sub={diag.llm.detail} />
              </>
            )}
          </dl>
        </section>
      </aside>
    </main>
  );
}

function Row({ k, v, sub }: { k: string; v: React.ReactNode; sub?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="flex items-center justify-between gap-3"><dt className="text-ink-2">{k}</dt><dd>{v}</dd></div>
      {sub && <p className="mt-0.5 text-[12px] text-ink-3">{sub}</p>}
    </div>
  );
}
