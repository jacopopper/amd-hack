"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Camera, FileText, Images, Map as MapIcon, Ruler } from "lucide-react";
import { api, json } from "@/lib/api";
import { byPriority, effective, sceneOf } from "@/lib/hazards";
import type { Anchor, Assessment, Findings, Modes, Report, Scene } from "@/lib/types";
import { useAuthGuard } from "@/lib/useAuthGuard";
import type { Tool, ViewMode } from "@/components/space/SpaceViewer";
import HazardCard, { type ReviewPayload } from "@/components/space/HazardCard";
import PatientChip from "@/components/space/PatientChip";
import PhotoView from "@/components/space/PhotoView";
import Reel from "@/components/space/Reel";
import TourSummary from "@/components/space/TourSummary";
import PipelineStatus from "@/components/PipelineStatus";
import PreparePanel from "@/components/PreparePanel";
import ReleaseDialog from "@/components/ReleaseDialog";

const SpaceViewer = dynamic(() => import("@/components/space/SpaceViewer"), { ssr: false });
const ACTIVE = ["queued", "processing", "report_drafting"];
const PREPARING = ["draft", "failed"];
type View = ViewMode | "photos";

function ToolButton({ active, disabled, title, onClick, children }: { active?: boolean; disabled?: boolean; title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={`grid h-11 w-11 place-items-center rounded-xl transition disabled:opacity-30 ${active ? "bg-white text-black" : "text-white/75 hover:bg-white/10 hover:text-white"}`}
    >
      {children}
    </button>
  );
}

// The patient’s home: preparation while in draft, then the 3D review space.
export default function PatientSpace({ id }: { id: string }) {
  const router = useRouter();
  const guard = useAuthGuard();
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [findings, setFindings] = useState<Findings | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [sceneId, setSceneId] = useState("");
  const [selected, setSelected] = useState<string | undefined>();
  const [view, setView] = useState<View>("dollhouse");
  const [tool, setTool] = useState<Tool>("none");
  const [photoId, setPhotoId] = useState("");
  const [pickedAnchor, setPickedAnchor] = useState<Anchor | null>(null);
  const [touring, setTouring] = useState(false);
  const [summary, setSummary] = useState(false);
  const [editingPhotos, setEditingPhotos] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [modes, setModes] = useState<Modes | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const a = await api<Assessment>(`/assessments/${id}`);
    const [f, s] = await Promise.all([api<Findings>(`/assessments/${id}/findings`), api<Scene[]>(`/assessments/${id}/scenes`)]);
    setAssessment(a);
    setFindings(f);
    setScenes(s);
    setSceneId((old) => (s.some((x) => x.id === old) ? old : (s[0]?.id ?? "")));
    setReport(a.reports[0] ? await api<Report>(`/reports/${a.reports[0].id}`) : null);
    if (PREPARING.includes(a.status)) setEditingPhotos(false);
    return a;
  }, [id]);

  useEffect(() => {
    api<{ modes: Modes }>("/health")
      .then((h) => setModes(h.modes))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const initial = setTimeout(() => load().catch((e) => guard(e) || setError((e as Error).message)));
    return () => clearTimeout(initial);
  }, [load, guard]);

  useEffect(() => {
    if (!assessment || !ACTIVE.includes(assessment.status)) return;
    const timer = setInterval(() => load().catch(() => undefined), 1500);
    return () => clearInterval(timer);
  }, [assessment, load]);

  const hazards = useMemo(() => findings?.hazards ?? [], [findings]);
  // Review order: room by room, most urgent first.
  const ordered = useMemo(() => {
    if (!assessment) return [];
    const rank = (sid?: string) => scenes.findIndex((s) => s.id === sid);
    return [...hazards].sort((a, b) => rank(sceneOf(a, assessment)) - rank(sceneOf(b, assessment)) || byPriority(a, b));
  }, [hazards, scenes, assessment]);
  const roomHazards = useMemo(() => (assessment ? ordered.filter((h) => sceneOf(h, assessment) === sceneId) : []), [ordered, assessment, sceneId]);
  const hazard = hazards.find((h) => h.id === selected);
  const decided = hazards.filter((h) => h.review).length;
  const busy = !!assessment && ACTIVE.includes(assessment.status);
  const released = assessment?.status === "released";
  const reportCurrent = !!report && !!assessment && report.review_revision === assessment.review_revision && report.input_revision === assessment.input_revision;
  const patientName = assessment?.inputs.label.split(/[ ·]/)[0] || "il paziente";

  const select = useCallback(
    (hid: string | undefined) => {
      setSelected(hid);
      setTool("none");
      setPickedAnchor(null);
      const h = hazards.find((x) => x.id === hid);
      if (!h || !assessment) return;
      const sid = sceneOf(h, assessment);
      if (sid) setSceneId(sid);
      const pid = effective(h).evidence[0]?.photo_id;
      if (pid) setPhotoId(pid);
      // Findings without a 3D location are shown on their photo.
      if (!effective(h).anchor) setView("photos");
    },
    [hazards, assessment],
  );

  const step = (d: number) => {
    if (!ordered.length) return;
    const i = ordered.findIndex((h) => h.id === selected);
    select(ordered[(i + d + ordered.length) % ordered.length].id);
  };

  function startTour() {
    const next = ordered.find((h) => !h.review) ?? ordered[0];
    if (!next) return;
    setTouring(true);
    setView("dollhouse");
    select(next.id);
  }

  async function saveReview(payload: ReviewPayload) {
    if (!assessment || !hazard) return;
    await api(`/assessments/${assessment.id}/reviews/${hazard.id}`, {
      method: "PUT",
      body: json({ expected_input_revision: assessment.input_revision, expected_review_revision: assessment.review_revision, ...payload }),
    });
    setTool("none");
    setPickedAnchor(null);
    await load();
    if (payload.anchor) return;
    // Guided review: move on to the next unanswered finding, then show the summary.
    const i = ordered.findIndex((h) => h.id === hazard.id);
    const next = [...ordered.slice(i + 1), ...ordered.slice(0, i)].find((h) => !h.review);
    if (next) {
      if (effective(next).anchor && view === "photos") setView("dollhouse");
      select(next.id);
    } else {
      setSelected(undefined);
      setTouring(false);
      setSummary(true);
    }
  }

  async function deleteAssessment() {
    await api(`/assessments/${id}`, { method: "DELETE" });
    router.replace("/");
  }

  async function draftReport() {
    if (!assessment) return;
    try {
      await api(`/assessments/${assessment.id}/reports`, { method: "POST", body: json({ expected_input_revision: assessment.input_revision, expected_review_revision: assessment.review_revision }) });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!assessment || !findings) {
    return <div className="grid h-full place-items-center bg-[#0a0b0d] text-sm text-white/50">{error || "Apertura della casa…"}</div>;
  }

  if (PREPARING.includes(assessment.status)) {
    return (
      <div className="relative grid h-full place-items-center overflow-hidden bg-[radial-gradient(ellipse_at_50%_30%,#23272d_0%,#0e1013_55%,#0a0b0d_100%)] p-4 pt-20 text-white">
        <div className="absolute left-4 top-4 z-10">
          <PatientChip name={assessment.inputs.label} profile={assessment.inputs.profile} onDelete={deleteAssessment} />
        </div>
        <div className="flex h-full max-h-[860px] w-full items-start justify-center">
          <PreparePanel assessment={assessment} scenes={scenes} modes={modes} reload={load} />
        </div>
      </div>
    );
  }

  const scene = scenes.find((s) => s.id === sceneId);
  const roomPhotos = assessment.photos.filter((p) => p.scene_id === sceneId);
  const unassessed = [...new Set(findings.coverage.filter((c) => c.coverage !== "visible").map((c) => c.category))];
  const calibrated = scene?.scale_state === "calibrated";

  return (
    <div className="relative h-full overflow-hidden bg-[#0a0b0d] text-white">
      {busy && assessment.status !== "report_drafting" && assessment.jobs[0] ? (
        <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(ellipse_at_50%_40%,#2a2e35_0%,#121418_60%,#0a0b0d_100%)]">
          <PipelineStatus jobId={assessment.jobs[0].id} />
        </div>
      ) : view === "photos" ? (
        <PhotoView photos={roomPhotos} hazards={roomHazards} photoId={photoId || roomPhotos[0]?.id} selectedId={selected} panelOpen={!!hazard} onPhoto={setPhotoId} onSelect={select} />
      ) : (
        <SpaceViewer scene={scene} hazards={hazards} selectedId={selected} mode={view} tool={tool} rightInset={hazard ? 420 : 0} onSelect={select} onPick={setPickedAnchor} />
      )}

      {/* Top-left: whose home this is */}
      <div className="absolute left-4 top-4 z-10">
        <PatientChip name={assessment.inputs.label} profile={assessment.inputs.profile} onDelete={deleteAssessment} />
      </div>

      {/* Top-center: rooms */}
      {scenes.length > 0 && (
        <div className={`absolute top-4 z-10 flex justify-center ${hazard ? "left-[360px] right-[440px]" : "left-[360px] right-[360px]"}`}>
          <div className="glass flex gap-1 rounded-2xl p-1">
            {scenes.map((s) => {
              const open = ordered.filter((h) => sceneOf(h, assessment) === s.id && !h.review).length;
              return (
                <button
                  key={s.id}
                  onClick={() => {
                    setSceneId(s.id);
                    setSelected(undefined);
                  }}
                  className={`flex items-center gap-2 rounded-xl px-4 py-2 text-sm transition ${s.id === sceneId ? "bg-white text-black" : "text-white/75 hover:bg-white/10"}`}
                >
                  {s.label}
                  {open > 0 && <span className={`rounded-full px-1.5 text-[11px] tabular-nums ${s.id === sceneId ? "bg-black/10" : "bg-white/15"}`}>{open}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Top-right: progress and report */}
      {!hazard && (
        <div className="absolute right-4 top-4 z-10 flex items-center gap-2">
          <div className="glass flex h-11 items-center gap-3 rounded-2xl px-4 text-sm">
            <span className="text-white/60">Verificati</span>
            <span className="tabular-nums">
              {decided} di {hazards.length}
            </span>
          </div>
          {!released && !busy && (
            <button onClick={() => setEditingPhotos(true)} className="glass flex h-11 items-center gap-2 rounded-2xl px-4 text-sm hover:bg-white/10">
              <Camera className="h-4 w-4" /> Foto
            </button>
          )}
          {reportCurrent ? (
            <button onClick={() => setReleasing(true)} className="flex h-11 items-center gap-2 rounded-2xl bg-white px-4 text-sm font-medium text-black">
              <FileText className="h-4 w-4" /> {released ? "Report rilasciato" : "Rivedi e rilascia"}
            </button>
          ) : (
            <button onClick={() => setSummary(true)} className="glass flex h-11 items-center gap-2 rounded-2xl px-4 text-sm hover:bg-white/10">
              <FileText className="h-4 w-4" /> Riepilogo
            </button>
          )}
        </div>
      )}

      {/* Right: the selected finding */}
      {hazard && (
        <div className="absolute bottom-4 right-4 top-4 z-10 flex">
          <HazardCard
            key={`${hazard.id}-${assessment.review_revision}`}
            hazard={hazard}
            photos={assessment.photos}
            patientName={patientName}
            index={ordered.findIndex((h) => h.id === hazard.id)}
            total={ordered.length}
            disabled={busy || released}
            placing={tool === "place"}
            pickedAnchor={pickedAnchor}
            onPrev={() => step(-1)}
            onNext={() => step(1)}
            onClose={() => {
              setSelected(undefined);
              setTouring(false);
            }}
            onTogglePlace={() => {
              setView("dollhouse");
              setTool((t) => (t === "place" ? "none" : "place"));
              setPickedAnchor(null);
            }}
            onSave={saveReview}
            onOpenPhoto={(pid) => {
              setPhotoId(pid);
              setView("photos");
            }}
          />
        </div>
      )}

      {/* Bottom-left: views and tools */}
      <div className="glass absolute bottom-4 left-4 z-10 flex flex-col gap-1 rounded-2xl p-1.5">
        <ToolButton title="Casa in 3D" active={view === "dollhouse"} onClick={() => setView("dollhouse")}>
          <Box className="h-5 w-5" />
        </ToolButton>
        <ToolButton title="Pianta dall’alto" active={view === "plan"} onClick={() => setView("plan")}>
          <MapIcon className="h-5 w-5" />
        </ToolButton>
        <ToolButton title="Foto della stanza" active={view === "photos"} onClick={() => setView("photos")}>
          <Images className="h-5 w-5" />
        </ToolButton>
        <span className="mx-2 my-0.5 h-px bg-white/10" />
        <ToolButton
          title={calibrated ? "Misura" : "Misura: serve la scala dal marker stampato"}
          active={tool === "measure"}
          disabled={!calibrated || view === "photos"}
          onClick={() => setTool((t) => (t === "measure" ? "none" : "measure"))}
        >
          <Ruler className="h-5 w-5" />
        </ToolButton>
      </div>

      {/* Bottom: highlight reel of this room */}
      <div className={`absolute bottom-4 left-[84px] z-10 flex ${hazard ? "right-[432px]" : "right-4"} justify-center`}>
        <div className="max-w-full">
          <Reel hazards={roomHazards} photos={assessment.photos} selectedId={selected} decided={decided} total={hazards.length} onSelect={select} onPlay={startTour} />
        </div>
      </div>

      {scene?.provenance === "fixture" && view !== "photos" && !busy && (
        <span className="pointer-events-none absolute left-4 top-[84px] z-0 rounded-full bg-[var(--important)]/15 px-3 py-1 text-xs text-[var(--important)]">Geometria sintetica · non ricostruita da foto</span>
      )}
      {touring && hazard && <span className="pointer-events-none absolute left-1/2 top-[76px] z-10 -translate-x-1/2 rounded-full bg-white/10 px-3 py-1 text-xs text-white/70 backdrop-blur">Revisione guidata · rispondi per passare al successivo</span>}
      {error && <p className="glass absolute left-1/2 top-24 z-30 -translate-x-1/2 rounded-xl px-4 py-2 text-sm text-[var(--urgent)]">{error}</p>}

      {editingPhotos && (
        <div className="absolute inset-0 z-20 grid place-items-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="flex h-full max-h-[860px] flex-col items-center gap-2">
            <p className="rounded-full bg-[var(--important)]/15 px-3 py-1 text-xs text-[var(--important)]">Cambiare le foto annulla l’analisi attuale e le risposte date finora.</p>
            <PreparePanel assessment={assessment} scenes={scenes} modes={modes} reload={load} onClose={() => setEditingPhotos(false)} />
          </div>
        </div>
      )}

      {releasing && report && <ReleaseDialog assessment={assessment} report={report} reload={load} onClose={() => setReleasing(false)} />}

      {summary && (
        <TourSummary
          hazards={hazards}
          unassessed={unassessed}
          followUps={findings.follow_up_requests}
          canDraft={decided === hazards.length && !released && !busy}
          drafting={assessment.status === "report_drafting"}
          onDraft={async () => {
            await draftReport();
            setSummary(false);
          }}
          onClose={() => setSummary(false)}
        />
      )}
    </div>
  );
}
