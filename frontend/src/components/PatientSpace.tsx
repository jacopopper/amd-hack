"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Camera, FileText, Images, Loader2, Map as MapIcon, Ruler } from "lucide-react";
import { api, json } from "@/lib/api";
import { byPriority, effective, sceneOf } from "@/lib/hazards";
import type { Anchor, Assessment, Findings, Health, Modes, PhotoLimits, Report, Scene } from "@/lib/types";
import type { Tool, ViewMode } from "@/components/space/SpaceViewer";
import HazardCard, { type ReviewPayload } from "@/components/space/HazardCard";
import AvatarViewer from "@/components/space/AvatarViewer";
import PatientChip from "@/components/space/PatientChip";
import PhotoView from "@/components/space/PhotoView";
import CaptureStage from "@/components/space/CaptureStage";
import Reel from "@/components/space/Reel";
import TourSummary from "@/components/space/TourSummary";
import PipelineStatus from "@/components/PipelineStatus";
import PreparePanel from "@/components/PreparePanel";
import ReleaseDialog from "@/components/ReleaseDialog";

const SpaceViewer = dynamic(() => import("@/components/space/SpaceViewer"), { ssr: false });
const ACTIVE = ["queued", "processing", "report_drafting"];
const PREPARING = ["draft", "failed"];
type View = ViewMode | "photos";

function ToolButton({ active, disabled, title, label, onClick, children }: { active?: boolean; disabled?: boolean; title: string; label?: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={`flex w-14 flex-col items-center justify-center gap-0.5 rounded-xl transition disabled:opacity-30 ${label ? "h-14" : "h-11"} ${active ? "bg-white text-black" : "text-white/75 hover:bg-white/10 hover:text-white"}`}
    >
      {children}
      {label && <span className="text-[11px] font-medium">{label}</span>}
    </button>
  );
}

// The patient’s home: preparation while in draft, then the 3D review space.
export default function PatientSpace({ id }: { id: string }) {
  const router = useRouter();
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [findings, setFindings] = useState<Findings | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [sceneId, setSceneId] = useState("");
  const [selected, setSelected] = useState<string | undefined>();
  const [view, setView] = useState<View>("dollhouse");
  // Set when this device cannot draw the reconstructed room usably; the review then stays on the photos.
  const [roomFallback, setRoomFallback] = useState(false);
  const [tool, setTool] = useState<Tool>("none");
  const [photoId, setPhotoId] = useState("");
  const [pickedAnchor, setPickedAnchor] = useState<Anchor | null>(null);
  const [touring, setTouring] = useState(false);
  const [summary, setSummary] = useState(false);
  const [editingPhotos, setEditingPhotos] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [modes, setModes] = useState<Modes | null>(null);
  const [photoLimits, setPhotoLimits] = useState<PhotoLimits | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const a = await api<Assessment>(`/assessments/${id}`);
    const [f, s] = await Promise.all([api<Findings>(`/assessments/${id}/findings`), api<Scene[]>(`/assessments/${id}/scenes`)]);
    setAssessment(a);
    setFindings(f);
    setScenes(s);
    setSceneId((old) => (s.some((x) => x.id === old) ? old : (s[0]?.id ?? "")));
    setReport(a.reports[0] ? await api<Report>(`/reports/${a.reports[0].id}`) : null);
    if (ACTIVE.includes(a.status)) setEditingPhotos(false);
    return a;
  }, [id]);

  useEffect(() => {
    api<Health>("/health")
      .then((h) => { setModes(h.modes); setPhotoLimits(h.photo_limits); setDemo(h.demo); })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const initial = setTimeout(() => load().catch((e) => setError((e as Error).message)));
    return () => clearTimeout(initial);
  }, [load]);

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
  const patientName = assessment?.inputs.label.split(/[ ·]/)[0] || "the patient";

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
      // Keep navigational/illustrative pins in 3D without promoting them to anchors.
      const room = scenes.find((s) => s.id === sid);
      const hasPresentationPin = room?.presentation?.hazard_positions[h.id] ?? room?.capture?.pins[h.id];
      if (!effective(h).anchor && !h.evidence_marker && !hasPresentationPin) setView("photos");
    },
    [hazards, assessment, scenes],
  );

  const step = (d: number) => {
    if (!ordered.length) return;
    const i = ordered.findIndex((h) => h.id === selected);
    select(ordered[(i + d + ordered.length) % ordered.length].id);
  };

  const startTour = useCallback(() => {
    const next = ordered.find((h) => !h.review) ?? ordered[0];
    if (!next) return;
    setTouring(true);
    if (!roomFallback) setView("dollhouse");
    select(next.id);
  }, [ordered, select, roomFallback]);

  const fallBackToPhotos = useCallback(() => {
    setRoomFallback(true);
    setView("photos");
  }, []);


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
    return (
      <div className="grid h-full place-items-center bg-[#0a0b0d] text-sm text-white/50">
        {error ? (
          <div className="text-center">
            <p>{error}</p>
            <Link href="/" className="mt-4 inline-block rounded-xl bg-white px-4 py-2.5 text-sm font-medium text-black">
              Torna ai pazienti
            </Link>
          </div>
        ) : (
          "Opening the home…"
        )}
      </div>
    );
  }

  if (PREPARING.includes(assessment.status) && !editingPhotos) {
    return (
      <div className="relative grid h-full place-items-center overflow-hidden bg-[radial-gradient(ellipse_at_50%_30%,#23272d_0%,#0e1013_55%,#0a0b0d_100%)] p-4 pt-20 text-white">
        <div className="absolute left-4 top-4 z-10">
          <PatientChip name={assessment.inputs.label} profile={assessment.inputs.profile} onDelete={deleteAssessment} />
        </div>
        <div className="flex h-full max-h-[860px] w-full items-start justify-center">
          <PreparePanel assessment={assessment} scenes={scenes} modes={modes} limits={photoLimits} reload={load} />
        </div>
      </div>
    );
  }

  const scene = scenes.find((s) => s.id === sceneId);
  const roomPhotos = assessment.photos.filter((p) => p.scene_id === sceneId);
  const unassessed = [...new Set(findings.coverage.filter((c) => c.coverage !== "visible").map((c) => c.category))];
  const calibrated = scene?.scale_state === "metric_calibrated";
  const drafting = assessment.status === "report_drafting";
  // The one action that moves the case forward pulses; everything else stays quiet.
  const next = released ? null : reportCurrent ? "release" : decided === hazards.length && !busy ? "report" : decided < hazards.length ? "review" : null;

  return (
    <div className="relative h-full overflow-hidden bg-[#0a0b0d] text-white">
      {busy && assessment.status !== "report_drafting" && assessment.jobs[0] ? (
        <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(ellipse_at_50%_40%,#2a2e35_0%,#121418_60%,#0a0b0d_100%)]">
          <PipelineStatus jobId={assessment.jobs[0].id} />
        </div>
      ) : (
        <>
          {/* The reconstructed room stays loaded behind the photos, so switching back is instant. */}
          {scene?.capture && (
            <div className={view === "photos" ? "invisible" : undefined}>
              <CaptureStage capture={scene.capture} hazards={roomHazards} selectedId={selected} onSelect={select} onUnavailable={fallBackToPhotos} />
            </div>
          )}
          {view === "photos" ? (
            <PhotoView photos={roomPhotos} hazards={roomHazards} photoId={photoId || roomPhotos[0]?.id} selectedId={selected} panelOpen={!!hazard} onPhoto={setPhotoId} onSelect={select} />
          ) : (
            !scene?.capture && <SpaceViewer scene={scene} hazards={hazards} selectedId={selected} mode={view} tool={tool} rightInset={hazard ? 420 : 0} onSelect={select} onPick={setPickedAnchor} />
          )}
        </>
      )}

      {/* Top-left: whose home this is */}
      <div className="absolute left-4 top-4 z-10">
        <PatientChip name={assessment.inputs.label} profile={assessment.inputs.profile} onDelete={demo ? undefined : deleteAssessment}>
          <AvatarViewer assessmentId={assessment.id} refreshKey={`${assessment.input_revision}-${assessment.jobs[0]?.id}-${assessment.jobs[0]?.status}`} />
        </PatientChip>
      </div>

      {/* Top-center: rooms */}
      {scenes.length > 1 && (
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
            <span className="text-white/60">Checked</span>
            <span className="tabular-nums">
              {decided} of {hazards.length}
            </span>
          </div>
          {!demo && !released && !busy && (
            <button onClick={() => setEditingPhotos(true)} className="glass flex h-11 items-center gap-2 rounded-2xl px-4 text-sm hover:bg-white/10">
              <Camera className="h-4 w-4" /> Photos
            </button>
          )}
          {reportCurrent ? (
            <button onClick={() => setReleasing(true)} className={`flex h-11 items-center gap-2 rounded-2xl bg-white px-4 text-sm font-medium text-black ${next === "release" ? "pulse-cta" : ""}`}>
              <FileText className="h-4 w-4" /> {released ? "Report released" : "Review and release"}
            </button>
          ) : drafting ? (
            <span className="glass flex h-11 items-center gap-2 rounded-2xl px-4 text-sm text-white/70">
              <Loader2 className="h-4 w-4 animate-spin" /> Preparing report
            </span>
          ) : next === "report" ? (
            <button onClick={() => setSummary(true)} className="pulse-cta flex h-11 items-center gap-2 rounded-2xl bg-white px-4 text-sm font-medium text-black">
              <FileText className="h-4 w-4" /> Prepare report
            </button>
          ) : (
            <button onClick={() => setSummary(true)} className="glass flex h-11 items-center gap-2 rounded-2xl px-4 text-sm hover:bg-white/10">
              <FileText className="h-4 w-4" /> Summary
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
        <ToolButton title="Home in 3D" label="3D" active={view === "dollhouse"} onClick={() => setView("dollhouse")}>
          <Box className="h-5 w-5" />
        </ToolButton>
        {/* The reconstructed room has no plan view and no metric scale, so it shows only 3D and photos. */}
        {!scene?.capture && (
          <ToolButton title="Top-down floor plan" label="Floor plan" active={view === "plan"} onClick={() => setView("plan")}>
            <MapIcon className="h-5 w-5" />
          </ToolButton>
        )}
        <ToolButton title="Room photos" label="Photos" active={view === "photos"} onClick={() => setView("photos")}>
          <Images className="h-5 w-5" />
        </ToolButton>
        {!scene?.capture && (
          <>
            <span className="mx-2 my-0.5 h-px bg-white/10" />
            <ToolButton
              title={calibrated ? "Measure" : "Measure: calibrate the scale with tape measurements first"}
              active={tool === "measure"}
              disabled={!calibrated || view === "photos"}
              onClick={() => setTool((t) => (t === "measure" ? "none" : "measure"))}
            >
              <Ruler className="h-5 w-5" />
            </ToolButton>
          </>
        )}
      </div>

      {/* Bottom: highlight reel of this room */}
      <div className={`absolute bottom-4 left-[96px] z-10 flex ${hazard ? "right-[432px]" : "right-4"} justify-center`}>
        <div className="max-w-full">
          <Reel hazards={roomHazards} photos={assessment.photos} selectedId={selected} decided={decided} total={hazards.length} pulse={next === "review" && !hazard} onSelect={select} onPlay={startTour} />
        </div>
      </div>

      {scene?.provenance === "fixture" && view !== "photos" && !busy && (
        <span className="pointer-events-none absolute left-4 top-[84px] z-0 rounded-full bg-[var(--important)]/15 px-3 py-1 text-xs text-[var(--important)]">Synthetic geometry · not reconstructed from photos</span>
      )}
      {touring && hazard && <span className="pointer-events-none absolute left-1/2 top-[76px] z-10 -translate-x-1/2 rounded-full bg-white/10 px-3 py-1 text-xs text-white/70 backdrop-blur">Guided review · answer to move to the next one</span>}
      {error && <p className="glass absolute left-1/2 top-24 z-30 -translate-x-1/2 rounded-xl px-4 py-2 text-sm text-[var(--urgent)]">{error}</p>}

      {editingPhotos && (
        <div className="absolute inset-0 z-20 grid place-items-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="flex h-full max-h-[860px] flex-col items-center gap-2">
            <p className="rounded-full bg-[var(--important)]/15 px-3 py-1 text-xs text-[var(--important)]">Changing the photos discards the current analysis and the answers given so far.</p>
            <PreparePanel assessment={assessment} scenes={scenes} modes={modes} limits={photoLimits} reload={load} onClose={() => setEditingPhotos(false)} />
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
          drafting={drafting}
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
