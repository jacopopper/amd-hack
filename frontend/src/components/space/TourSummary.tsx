"use client";

import { Check, FileText, HelpCircle, Loader2, X } from "lucide-react";
import { categoryLabel } from "@/lib/labels";
import { answerOf } from "@/lib/hazards";
import type { Hazard } from "@/lib/types";

// End of the guided review: what goes into the plan, and what the photos could not show.
export default function TourSummary({ hazards, unassessed, followUps, canDraft, drafting, onDraft, onClose }: { hazards: Hazard[]; unassessed: string[]; followUps: string[]; canDraft: boolean; drafting: boolean; onDraft: () => void; onClose: () => void }) {
  const count = (a: string) => hazards.filter((h) => answerOf(h.review?.decision) === a).length;
  const corrected = hazards.filter((h) => h.review?.decision === "edit").length;
  const rows = [
    { icon: Check, label: corrected ? `In the action plan (${corrected} edited by you)` : "In the action plan", n: count("confirm") },
    { icon: HelpCircle, label: "Check at the first visit", n: count("verify") },
    { icon: X, label: "Excluded, not sent to the family", n: count("dismiss") },
  ];
  const open = hazards.length - rows.reduce((s, r) => s + r.n, 0);
  return (
    <div className="absolute inset-0 z-20 grid place-items-center bg-black/50 backdrop-blur-sm">
      <div className="glass w-[440px] rounded-2xl p-6">
        <h2 className="text-xl font-semibold">{open ? "Review in progress" : "Review complete"}</h2>
        <p className="mt-1 text-sm text-white/55">{open ? `${open} ${open === 1 ? "hazard is" : "hazards are"} still unanswered.` : "The report will contain only the confirmed hazards."}</p>
        <ul className="mt-5 space-y-2">
          {rows.map((r) => (
            <li key={r.label} className="flex items-center gap-3 rounded-xl bg-white/[0.05] px-3 py-2.5 text-sm">
              <r.icon className="h-4 w-4 text-white/60" />
              <span className="flex-1">{r.label}</span>
              <span className="tabular-nums text-white/70">{r.n}</span>
            </li>
          ))}
        </ul>
        {(unassessed.length > 0 || followUps.length > 0) && (
          <div className="mt-5">
            <h3 className="text-[13px] font-medium text-white/50">The photos cannot assess</h3>
            <p className="mt-1 text-sm leading-relaxed text-white/80">{unassessed.map((c) => categoryLabel[c] ?? c).join(", ")}</p>
            {followUps.map((f) => (
              <p key={f} className="mt-1 text-sm leading-relaxed text-white/55">
                {f}
              </p>
            ))}
          </div>
        )}
        <div className="mt-6 flex gap-2">
          <button onClick={onClose} className="flex-1 rounded-xl py-2.5 text-sm text-white/70 hover:bg-white/5">
            Back to the home
          </button>
          <button disabled={!canDraft || drafting} onClick={onDraft} className={`flex flex-1 items-center justify-center gap-2 rounded-xl bg-white py-2.5 text-sm font-medium text-black disabled:opacity-40 ${canDraft && !drafting ? "pulse-cta" : ""}`}>
            {drafting ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />} Prepare report
          </button>
        </div>
      </div>
    </div>
  );
}
