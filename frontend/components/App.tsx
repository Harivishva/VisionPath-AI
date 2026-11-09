"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useIntake } from "@/lib/useIntake";
import { AlertsPanel } from "./AlertsPanel";
import { CameraStage } from "./CameraStage";
import { DiagnosticsView } from "./DiagnosticsView";
import { FhirView } from "./FhirView";
import { Header, type Tab } from "./Header";
import { SetupView } from "./SetupView";
import { SignalsStrip } from "./SignalsStrip";
import { SummaryView } from "./SummaryView";
import { SymptomsPanel } from "./SymptomsPanel";
import { TranscriptPanel } from "./TranscriptPanel";
import { XaiPanel } from "./XaiPanel";
import { AcuityBanner } from "./AcuityBanner";
import { Notice } from "./ui";

export default function App() {
  const intake = useIntake();
  const [tab, setTab] = useState<Tab>("intake");
  const { session, notice, setNotice, ws } = intake;

  if (!session) {
    return (
      <>
        <SetupView onCreate={intake.create} busy={intake.busy} error={notice?.kind === "error" ? notice.text : null} />
        {notice && notice.kind !== "error" && (
          <div className="fixed bottom-4 left-1/2 w-[min(520px,92vw)] -translate-x-1/2"><Notice kind={notice.kind} onClose={() => setNotice(null)}>{notice.text}</Notice></div>
        )}
      </>
    );
  }

  return (
    <div className="flex h-dvh flex-col">
      <Header intake={intake} tab={tab} onTab={setTab} />
      {(ws === "reconnecting" || ws === "closed") && (
        <div className="px-4 pt-2"><Notice kind="warn">
          {ws === "reconnecting" ? "Reconnecting to the backend… live camera/audio processing is paused. Transcript, review and records already saved are not affected." : "Backend connection closed. Live processing has stopped."}
        </Notice></div>
      )}
      <AcuityBanner intake={intake} />
      {notice && (
        <div className="px-4 pt-2"><Notice kind={notice.kind} onClose={() => setNotice(null)}>{notice.text}</Notice></div>
      )}
      <div id="main-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="relative min-h-0 min-w-0 flex-1 overflow-auto scroll-thin">
        {tab === "intake" && (
          <div className="grid min-h-0 grid-cols-[minmax(0,1fr)] gap-3 p-3 xl:h-full xl:grid-cols-[minmax(0,1fr)_400px]" data-testid="intake-view">
            <div className="flex min-h-0 min-w-0 flex-col gap-2 xl:overflow-y-auto scroll-thin">
              <CameraStage intake={intake} />
              <SignalsStrip intake={intake} />
              <XaiPanel intake={intake} />
            </div>
            <div className="flex min-h-0 min-w-0 flex-col gap-2 xl:overflow-y-auto scroll-thin">
              <AlertsPanel session={session} onAck={(id) => void intake.mutate((s, t) => api.ack(s, t, id))} />
              <TranscriptPanel intake={intake} />
              <SymptomsPanel intake={intake} />
            </div>
          </div>
        )}
        {tab === "summary" && <SummaryView intake={intake} />}
        {tab === "fhir" && <FhirView intake={intake} />}
        {tab === "diagnostics" && <DiagnosticsView intake={intake} />}
      </div>
    </div>
  );
}
