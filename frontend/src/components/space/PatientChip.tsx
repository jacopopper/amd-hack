"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, ChevronDown, Loader2, Trash2 } from "lucide-react";
import type { Profile } from "@/lib/types";
import { DEMO } from "@/lib/api";

const HomeLink = DEMO ? "a" : Link;

const rows: [keyof Profile, string][] = [
  ["mobility_aid", "Aid"],
  ["vision", "Vision"],
  ["relevant_history", "History"],
  ["support_needs", "Support"],
];

// Top-left identity of the space: who lives here, expandable to the full profile.
export default function PatientChip({ name, profile, onDelete, children }: { name: string; profile: Profile; onDelete?: () => Promise<void>; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  async function remove() {
    if (!onDelete) return;
    setDeleting(true);
    setError("");
    try {
      await onDelete();
    } catch (e) {
      setError((e as Error).message);
      setDeleting(false);
    }
  }
  const aid = profile.mobility_aid ? `${profile.mobility_aid}${profile.aid_width_m ? ` ${Math.round(profile.aid_width_m * 100)} cm` : ""}` : null;
  return (
    <div className="glass w-80 overflow-hidden rounded-2xl">
      <div className="flex items-center gap-1 p-1.5">
        <HomeLink href="/" className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-white/70 hover:bg-white/10 hover:text-white" title="All patients">
          <ArrowLeft className="h-4 w-4" />
        </HomeLink>
        <button onClick={() => setOpen((o) => !o)} className="flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-1 text-left hover:bg-white/5">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px] font-semibold">{name}</span>
            <span className="block truncate text-xs text-white/55">{[aid, profile.daily_routes[0]].filter(Boolean).join(" · ") || profile.description}</span>
          </span>
          <ChevronDown className={`h-4 w-4 shrink-0 text-white/50 transition ${open ? "rotate-180" : ""}`} />
        </button>
      </div>
      {open && (
        <div className="border-t border-white/10 px-4 pb-4 pt-3 text-sm">
          <p className="leading-relaxed text-white/80">{profile.description}</p>
          <dl className="mt-3 grid grid-cols-[84px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
            {rows.map(([key, label]) => (
              <div key={key} className="contents">
                <dt className="text-white/45">{label}</dt>
                <dd className={profile[key] ? "" : "text-white/35"}>{key === "mobility_aid" ? (aid ?? "Not given") : ((profile[key] as string | null) ?? "Not given")}</dd>
              </div>
            ))}
            <dt className="text-white/45">Routes</dt>
            <dd>{profile.daily_routes.join(", ") || <span className="text-white/35">Not given</span>}</dd>
          </dl>
          {children}
          {onDelete && (
            <div className="mt-4 border-t border-white/10 pt-3">
              {confirming ? (
                <div className="space-y-2">
                  <p className="text-[13px] leading-relaxed text-white/60">Photos, analysis and report will be deleted and the family link will stop working.</p>
                  <div className="flex gap-2">
                    <button onClick={() => setConfirming(false)} className="flex-1 rounded-lg py-1.5 text-[13px] text-white/60 hover:bg-white/5">
                      Cancel
                    </button>
                    <button disabled={deleting} onClick={remove} className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-[var(--urgent)] py-1.5 text-[13px] font-medium text-black disabled:opacity-60">
                      {deleting && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Delete
                    </button>
                  </div>
                </div>
              ) : (
                <button onClick={() => setConfirming(true)} className="flex items-center gap-1.5 text-[13px] text-white/45 hover:text-[var(--urgent)]">
                  <Trash2 className="h-3.5 w-3.5" /> Delete the assessment
                </button>
              )}
              {error && <p className="mt-2 text-xs text-[var(--urgent)]">{error}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
