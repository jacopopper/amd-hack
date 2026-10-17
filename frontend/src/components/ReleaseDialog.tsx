"use client";

import { useState } from "react";
import { Check, Copy, ExternalLink, Loader2, Send, ShieldOff, X } from "lucide-react";
import { api, json } from "@/lib/api";
import type { Assessment, Report } from "@/lib/types";

const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long" }) : "");

// Final step: the reviewer reads the draft next to the decision, releases it, and hands the family a link shown only once.
export default function ReleaseDialog({ assessment, report, reload, onClose }: { assessment: Assessment; report: Report; reload: () => Promise<unknown>; onClose: () => void }) {
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [link, setLink] = useState<{ url: string; expires: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const released = report.status === "released";

  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const release = () =>
    run(async () => {
      const result = await api<{ token: string; expires_at: string }>(`/reports/${report.id}/release`, {
        method: "POST",
        body: json({ expected_input_revision: assessment.input_revision, expected_review_revision: assessment.review_revision }),
      });
      setLink({ url: `${location.origin}/api/family/${result.token}`, expires: result.expires_at });
      await reload();
    });

  const revoke = () =>
    run(async () => {
      await api(`/reports/${report.id}/revoke`, { method: "POST" });
      setLink(null);
      await reload();
    });

  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center gap-4 bg-black/60 p-6 backdrop-blur-sm">
      <iframe src={report.html_url} title="Preview of the family report" className="h-full max-h-[900px] min-w-0 max-w-[820px] flex-1 rounded-2xl bg-white shadow-2xl" />
      <div className="glass relative w-[420px] shrink-0 rounded-2xl p-6">
        <button onClick={onClose} className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white" aria-label="Close">
          <X className="h-4 w-4" />
        </button>
        <h2 className="text-xl font-semibold">{link ? "Report released" : released ? "Family report" : "Release to the family"}</h2>

        <div className="mt-4 flex gap-2">
          <a href={report.html_url} target="_blank" rel="noreferrer" className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white/[0.06] py-2.5 text-sm hover:bg-white/10">
            Web version <ExternalLink className="h-3.5 w-3.5" />
          </a>
          <a href={report.pdf_url} target="_blank" rel="noreferrer" className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white/[0.06] py-2.5 text-sm hover:bg-white/10">
            PDF <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>

        {link ? (
          <div className="mt-5 space-y-2">
            <p className="text-sm leading-relaxed text-white/70">
              Copy the link now: <span className="text-white">it will not be shown again</span>. It opens only the report and expires on {date(link.expires)}.
            </p>
            <a href={link.url} target="_blank" rel="noreferrer" className="flex h-11 items-center justify-center gap-2 rounded-xl bg-white text-sm font-medium text-black hover:bg-white/90">
              Open the family link <ExternalLink className="h-4 w-4" />
            </a>
            <div className="flex gap-2">
              <input readOnly value={link.url} onFocus={(e) => e.target.select()} className="h-10 min-w-0 flex-1 rounded-xl bg-black/30 px-3 font-mono text-xs text-white/80 outline-none ring-1 ring-white/15" />
              <button
                onClick={async () => {
                  await navigator.clipboard.writeText(link.url);
                  setCopied(true);
                }}
                className="flex h-10 items-center gap-1.5 rounded-xl bg-white/10 px-3 text-sm font-medium hover:bg-white/15"
              >
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        ) : released ? (
          <p className="mt-5 text-sm leading-relaxed text-white/70">
            {report.revoked ? "The family link has been revoked." : `Released on ${date(report.released_at)}. The family link expires on ${date(report.family_expires_at)}.`}
          </p>
        ) : (
          <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-xl bg-white/[0.04] px-3.5 py-3 text-sm leading-relaxed text-white/80 hover:bg-white/[0.07]">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-1 h-4 w-4 shrink-0 accent-white" />
            I have checked the evidence, the final text and the PDF. After release the assessment can no longer be changed.
          </label>
        )}

        {error && <p className="mt-3 text-xs text-[var(--urgent)]">{error}</p>}

        <div className="mt-6 flex gap-2">
          <button onClick={onClose} className="flex-1 rounded-xl py-2.5 text-sm text-white/70 hover:bg-white/5">
            Close
          </button>
          {!released && (
            <button disabled={!checked || busy} onClick={release} className={`flex flex-1 items-center justify-center gap-2 rounded-xl bg-white py-2.5 text-sm font-medium text-black disabled:opacity-40 ${checked && !busy ? "pulse-cta" : ""}`}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Release
            </button>
          )}
          {released && !report.revoked && (
            <button disabled={busy} onClick={revoke} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white/[0.06] py-2.5 text-sm text-[var(--urgent)] hover:bg-white/10 disabled:opacity-40">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldOff className="h-4 w-4" />} Revoke link
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
