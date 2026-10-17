"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Box, Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { currentCopy } from "@/lib/demo";
import { effective } from "@/lib/hazards";
import { priorityMeta, statusLabel } from "@/lib/labels";
import type { Assessment, Findings, Health, Listing, Priority } from "@/lib/types";
import Brand from "@/components/Brand";

type Row = { assessment: Assessment; findings: Findings | null };

export default function PatientsPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [demo, setDemo] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      const health = await api<Health>("/health");
      setDemo(health.demo);
      // The public demo shows only this browser's copy of the case.
      const ids = health.demo ? [await currentCopy()] : (await api<Listing>("/assessments")).map((item) => item.id);
      setRows(
        await Promise.all(
          ids.map(async (id) => ({
            assessment: await api<Assessment>(`/assessments/${id}`),
            findings: await api<Findings>(`/assessments/${id}/findings`).catch(() => null),
          })),
        ),
      );
    })().catch((e) => setError((e as Error).message));
  }, []);

  const visible = rows?.filter((r) => r.assessment.inputs.label.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="min-h-full bg-[#0a0b0d] text-white">
      <header className="mx-auto flex max-w-6xl items-center gap-4 px-8 pt-8">
        <Brand />
        <div className="ml-auto flex items-center gap-2">
          <label className="flex h-10 w-64 items-center gap-2 rounded-xl bg-white/[0.06] px-3 text-sm text-white/50 focus-within:bg-white/10">
            <Search className="h-4 w-4" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search patients" className="w-full bg-transparent text-white outline-none placeholder:text-white/40" />
          </label>
          {!demo && (
            <Link href="/pazienti/nuovo" className="flex h-10 items-center gap-2 rounded-xl bg-white px-4 text-sm font-medium text-black hover:bg-white/90">
              <Plus className="h-4 w-4" /> New patient
            </Link>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-8 pb-16 pt-12">
        <h1 className="text-3xl font-semibold tracking-tight">Patients</h1>
        <p className="mt-1 text-white/50">The homes to assess before patients come home.</p>

        {error && <p className="mt-10 text-sm text-[var(--urgent)]" role="alert">{error}</p>}
        {rows === null && !error && <p className="mt-10 text-sm text-white/50">Loading…</p>}
        {visible?.length === 0 && <p className="mt-10 text-sm text-white/50">No patients.</p>}
        <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {visible?.map(({ assessment: a, findings: f }) => {
            const cover = a.photos.find((p) => p.role === "overview") ?? a.photos[0];
            const rooms = new Set(a.photos.map((p) => p.scene_id)).size;
            const live = f?.hazards.filter((h) => h.review?.decision !== "reject") ?? [];
            const open = f?.hazards.filter((h) => !h.review).length ?? 0;
            const [name, ...rest] = a.inputs.label.split(" · ");
            return (
              <Link key={a.id} href={`/pazienti?id=${a.id}`} className="group block">
                <div className="relative aspect-[16/10] overflow-hidden rounded-2xl bg-white/5 ring-1 ring-white/10 transition group-hover:ring-white/30">
                  {cover && (
                    // eslint-disable-next-line @next/next/no-img-element -- same-origin API image
                    <img src={cover.asset_url} alt="" className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.03]" />
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent" />
                  <span className="absolute left-3 top-3 rounded-full bg-black/55 px-2.5 py-1 text-xs backdrop-blur">{statusLabel[a.status] ?? a.status}</span>
                  <span className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-black shadow-lg transition group-hover:scale-105">
                    <Box className="h-3.5 w-3.5" /> Open the home in 3D
                  </span>
                  <div className="absolute bottom-3 left-3 flex gap-1.5">
                    {(["urgent", "important", "recommended"] as Priority[]).map((p) => {
                      const n = live.filter((h) => effective(h).priority === p).length;
                      return n ? (
                        <span key={p} className="flex items-center gap-1.5 rounded-full bg-black/55 px-2 py-0.5 text-xs backdrop-blur">
                          <span className="h-2 w-2 rounded-full" style={{ background: priorityMeta[p].color }} />
                          {n}
                        </span>
                      ) : null;
                    })}
                  </div>
                </div>
                <div className="mt-3 px-0.5">
                  <p className="font-medium">
                    {name}
                    {rest.length > 0 && <span className="font-normal text-white/50"> · {rest.join(" · ")}</span>}
                  </p>
                  <p className="mt-0.5 text-sm text-white/50">
                    {[a.inputs.profile.mobility_aid, `${rooms} ${rooms === 1 ? "room" : "rooms"}`, open ? `${open} to check` : null].filter(Boolean).join(" · ")}
                  </p>
                </div>
              </Link>
            );
          })}
        </div>
      </main>
    </div>
  );
}
