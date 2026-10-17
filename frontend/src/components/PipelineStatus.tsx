"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { stageLabel } from "@/lib/labels";
import type { Job } from "@/lib/types";

const where: Record<string, string> = {
  prepare: "CPU",
  jev: "TypeSafe API",
  reconstruct: "GPU · VGGT",
  geometry: "CPU",
  gemma: "Gemma",
  avatar: "Blender · MPFB",
  report: "CPU",
};

// Worker order (backend worker.STAGES); unknown stages keep their response order at the end.
const ORDER = ["prepare", "jev", "reconstruct", "gemma", "geometry", "avatar", "report"];
const rank = (name: string) => (ORDER.includes(name) ? ORDER.indexOf(name) : ORDER.length);

// Live view of the worker pipeline; each stage shows where it runs and how long it took.
export default function PipelineStatus({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    const poll = async () => {
      const result = await api<Job>(`/jobs/${jobId}`, { signal: abort.signal }).catch(() => null);
      if (abort.signal.aborted) return;
      if (result) setJob(result);
      if (!result || ["queued", "running"].includes(result.status)) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [jobId]);

  if (!job) return null;
  return (
    <div className="glass w-[400px] rounded-2xl p-6">
      <h2 className="text-lg font-semibold">Analysing the home</h2>
      <p className="mt-1 text-sm text-white/55">You can leave this page: the work continues on the server.</p>
      <ol className="mt-5 space-y-3.5">
        {Object.entries(job.stages)
          .sort(([a], [b]) => rank(a) - rank(b))
          .map(([name, stage]) => {
          const running = job.stage === name && stage.status !== "succeeded";
          const done = stage.status === "succeeded";
          return (
            <li key={name} className={`flex items-center gap-3 text-sm ${done || running ? "" : "text-white/40"}`}>
              <span className={`grid h-6 w-6 place-items-center rounded-full ${done ? "bg-white text-black" : running ? "bg-white/15" : stage.status === "failed" ? "bg-[var(--urgent)]" : "bg-white/[0.06]"}`}>
                {done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : stage.status === "failed" ? <X className="h-3.5 w-3.5" /> : null}
              </span>
              <span className="flex-1">{stageLabel[name] ?? name}</span>
              <span className="text-xs text-white/40">{where[name] ?? ""}</span>
              <span className="w-10 text-right text-xs tabular-nums text-white/55">{stage.elapsed_seconds != null ? `${stage.elapsed_seconds.toFixed(1)}s` : ""}</span>
            </li>
          );
        })}
      </ol>
      {job.error && <p className="mt-4 text-xs text-[var(--urgent)]">{job.error}</p>}
    </div>
  );
}
