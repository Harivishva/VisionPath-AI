"use client";

import type { Intake } from "@/lib/useIntake";
import { api } from "@/lib/api";

/** Flashing nurse-workstation banner for the waiting-room re-triage alert. Stays until acknowledged. */
export function AcuityBanner({ intake }: { intake: Intake }) {
  const { unified, session, token, guard } = intake;
  const w = unified?.waiting_room_monitoring;
  if (!w?.acuity_escalation_alert || !session) return null;
  return (
    <div
      role="alert"
      data-testid="acuity-banner"
      className="acuity-flash mx-3 mt-2 flex flex-wrap items-center gap-3 rounded-md border border-alert bg-alert px-3 py-2 text-paper"
    >
      <b className="text-[14px]">RE-TRIAGE ALERT — Acuity escalation suggested</b>
      <span className="min-w-0 flex-1 text-[12.5px]">
        {w.reasons.join("; ") || "Change from the registration baseline"}. Prompt for a nurse to reassess; not a diagnosis.
      </span>
      <button
        type="button"
        className="rounded border border-paper/70 px-2.5 py-1 text-[12.5px] font-medium hover:bg-paper/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-paper"
        onClick={() => void guard(() => api.waitingRoom(session.id, token!, "ack"))}
      >
        Acknowledge
      </button>
    </div>
  );
}
