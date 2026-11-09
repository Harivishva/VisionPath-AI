"use client";

import { useState } from "react";
import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";
import type { EhrSyncResult, FhirResult } from "@/lib/types";
import { Badge, Button, Notice, Panel } from "./ui";

export function FhirView({ intake }: { intake: Intake }) {
  const { session, guard, setNotice, token } = intake;
  const [res, setRes] = useState<FhirResult | null>(null);
  const [exported, setExported] = useState(false);
  const [ehr, setEhr] = useState<EhrSyncResult | null>(null);
  if (!session) return null;

  const generate = async () => {
    const r = await guard(() => api.generateFhir(session.id, token!));
    if (r) { setRes(r); setExported(false); }
  };
  const syncEhr = async () => {
    if (!res) return;
    const r = await guard(() => api.ehrSync(res.bundle, token!));
    if (r) setEhr(r);
  };
  const json = res ? JSON.stringify(res.bundle, null, 2) : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json);
      setNotice({ kind: "success", text: "FHIR JSON copied to clipboard" });
    } catch {
      setNotice({ kind: "error", text: "Clipboard unavailable in this browser context" });
    }
  };
  const download = () => {
    const blob = new Blob([json], { type: "application/fhir+json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `visionpath-${session.reference.replace(/[^A-Za-z0-9._-]/g, "_")}-fhir-bundle.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setExported(true);
    setNotice({ kind: "success", text: "Record exported: validated bundle downloaded." });
  };

  const stale = res && res.finalized !== session.review.finalized;
  const blockedReason = !res ? "Generate the bundle first" : !res.validation.valid ? "Bundle failed validation" : !res.finalized ? "Finalize the review first" : "";

  return (
    <div className="mx-auto grid max-w-[1180px] gap-3 p-4 lg:grid-cols-[380px_1fr]" data-testid="fhir-view">
      <div className="space-y-3">
        <Panel title="FHIR R4 export">
          <div className="space-y-3 p-3">
            <p className="text-[13px] text-ink-2">Builds a bundle from reviewed data only. Rejected and unconfirmed machine-extracted symptoms are excluded.</p>
            <Button variant="primary" onClick={() => void generate()}>{res ? "Regenerate & validate" : "Generate & validate"}</Button>
            {stale && <Notice kind="warn">Review state changed since this bundle was generated. Regenerate before exporting.</Notice>}
            {res && (
              <div className="space-y-2" data-testid="fhir-validation">
                <div className="flex items-center gap-2">
                  {res.validation.valid ? <Badge tone="sage">Local structural validation passed</Badge> : <Badge tone="alert">Validation failed</Badge>}
                  <Badge tone={res.finalized ? "sage" : "amber"}>{res.finalized ? "Reviewed & finalized" : "Draft · not finalized"}</Badge>
                </div>
                <p className="readout text-ink-2">{res.validation.standard}</p>
                <ul className="readout columns-2 text-ink-2" aria-label="Resource types included">
                  {Object.entries(res.validation.resource_counts).map(([k, v]) => <li key={k}>{k} × {v}</li>)}
                </ul>
                <p className="text-[12px] text-ink-3">{res.validation.references_checked} internal references checked.</p>
                {res.notes.map((n) => <p key={n} className="text-[12px] text-amber">• {n}</p>)}
                {res.validation.errors.length > 0 && (
                  <ul className="space-y-1 text-[12px] text-alert" role="alert" data-testid="fhir-errors">
                    {res.validation.errors.map((e, i) => <li key={i}><span className="readout">{e.resource} {e.path}</span> — {e.message}</li>)}
                  </ul>
                )}
                <p className="text-[12px] text-ink-3">{res.validation.disclaimer}</p>
              </div>
            )}
            <div className="flex gap-2">
              <Button onClick={() => void copy()} disabled={!res}>Copy JSON</Button>
              <Button variant="primary" onClick={download} disabled={!res || !res.exportable || !!stale} title={blockedReason}>Download JSON</Button>
            </div>
            <Button onClick={() => void syncEhr()} disabled={!res || !res.exportable || !!stale} title={blockedReason}>Simulate EHR sync</Button>
            {ehr && (
              <Notice kind="info">
                <span data-testid="ehr-result">Simulated 201 Created ({ehr.transmission_id}). {ehr.target}.</span>
              </Notice>
            )}
            {blockedReason && res && <p className="text-[12px] text-ink-3">Download disabled: {blockedReason}.</p>}
            {exported && <Notice kind="success">Exported {new Date().toLocaleTimeString()} — validated bundle saved to your downloads.</Notice>}
          </div>
        </Panel>
      </div>
      <Panel title="Bundle preview" aside={res ? <span className="readout text-ink-3">{json.length.toLocaleString()} chars</span> : undefined}>
        <pre className="scroll-thin max-h-[68vh] overflow-auto p-3 font-mono text-[11.5px] leading-snug text-ink-2" data-testid="fhir-json" tabIndex={0} aria-label="FHIR bundle JSON preview">
          {res ? json : "No bundle generated yet."}
        </pre>
      </Panel>
    </div>
  );
}
