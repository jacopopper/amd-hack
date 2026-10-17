"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, Play, Upload, X } from "lucide-react";
import { api, json } from "@/lib/api";
import type { Assessment, Modes, PhotoLimits, Scene } from "@/lib/types";

const field = "h-10 rounded-xl bg-white/[0.06] px-3 text-sm text-white outline-none ring-1 ring-white/10 transition placeholder:text-white/30 focus:bg-white/10 focus:ring-white/30";

// Before analysis: photos room by room, the consents the configured models need, then start.
export default function PreparePanel({ assessment, scenes, modes, limits, reload, onClose }: { assessment: Assessment; scenes: Scene[]; modes: Modes | null; limits: PhotoLimits | null; reload: () => Promise<Assessment>; onClose?: () => void }) {
  const [room, setRoom] = useState(scenes[0]?.label ?? "");
  const [role, setRole] = useState<"overview" | "detail">("overview");
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputs = assessment.inputs;

  const rooms = scenes.map((s) => ({ ...s, photos: assessment.photos.filter((p) => p.scene_id === s.id) }));
  const overview = assessment.photos.filter((p) => p.role === "overview").length;
  const thinRooms = rooms.filter((r) => r.photos.filter((p) => p.role === "overview").length < 2).map((r) => r.label);
  const overCap = limits && modes?.reconstruction === "vggt" ? rooms.filter((r) => r.photos.filter((p) => p.role === "overview").length > limits.frame_cap) : [];
  const selectedRoom = rooms.find((r) => r.label === room.trim());
  const roomOverviews = selectedRoom?.photos.filter((p) => p.role === "overview").length ?? 0;
  const needsPhotoConsent = modes?.jev === "live" || modes?.gemma === "http";
  const needsClinicalConsent = modes?.gemma === "http";
  const synthetic = modes?.reconstruction === "fixture" && modes?.gemma === "fixture";
  const failed = assessment.status === "failed" ? assessment.jobs.find((j) => j.kind === "analyze" && j.error) : undefined;
  const blockers = [
    (!modes || !limits) && "Loading configuration…",
    ...overCap.map((r) => `${r.label}: over the limit of ${limits?.frame_cap} overview photos. Remove the least useful shots before the analysis.`),
    !inputs.profile_confirmed && "The profile is not confirmed.",
    overview === 0 && "At least one overview photo is needed.",
    needsPhotoConsent && !inputs.external_processing_consent && "Consent to external photo analysis is missing.",
    needsClinicalConsent && !inputs.clinical_external_processing_consent && "Consent to send the profile to the model is missing.",
  ].filter(Boolean) as string[];

  async function run(task: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }

  // Each upload bumps the input revision, so files go one at a time against the latest one.
  const upload = (files: File[]) =>
    run(async () => {
      if (!limits || !modes) throw new Error("Wait for the configuration to load.");
      if (assessment.photos.length + files.length > limits.max_photos) throw new Error(`At most ${limits.max_photos} photos per home. No files uploaded.`);
      if (role === "overview" && modes.reconstruction === "vggt" && roomOverviews + files.length > limits.frame_cap) throw new Error(`This room has ${roomOverviews} overview photos: you can add ${Math.max(0, limits.frame_cap - roomOverviews)} more. No files uploaded.`);
      if (files.some((file) => !/\.(jpe?g|png)$/i.test(file.name) || file.size > limits.max_file_bytes)) throw new Error(`Use JPG or PNG files, up to ${Math.round(limits.max_file_bytes / 1024 / 1024)} MB each. No files uploaded.`);
      let current = assessment;
      for (const [i, file] of [...files].entries()) {
        setProgress(`Uploading ${i + 1} of ${files.length}…`);
        const body = new FormData();
        body.set("file", file);
        body.set("scene_label", room.trim());
        body.set("role", role);
        body.set("expected_input_revision", String(current.input_revision));
        await api(`/assessments/${assessment.id}/photos`, { method: "POST", body });
        current = await reload();
      }
    });

  const remove = (photoId: string) =>
    run(async () => {
      await api(`/assessments/${assessment.id}/photos/${photoId}?expected_input_revision=${assessment.input_revision}`, { method: "DELETE" });
      await reload();
    });

  const consent = (key: "external_processing_consent" | "clinical_external_processing_consent", value: boolean) =>
    run(async () => {
      await api(`/assessments/${assessment.id}`, { method: "PATCH", body: json({ ...inputs, [key]: value, expected_input_revision: assessment.input_revision }) });
      await reload();
    });

  const analyze = () =>
    run(async () => {
      await api(`/assessments/${assessment.id}/analyze`, { method: "POST", body: json({ expected_input_revision: assessment.input_revision, expected_review_revision: assessment.review_revision }) });
      await reload();
    });

  return (
    <div className="glass glass-solid flex max-h-full w-[640px] max-w-full flex-col overflow-hidden rounded-2xl">
      <header className="flex items-start gap-3 px-6 pb-3 pt-5">
        <div>
          <h2 className="text-xl font-semibold">Photos of the home</h2>
          <p className="mt-0.5 text-sm text-white/50">Add new photos to the same room: they will be reconstructed together with the existing ones.</p>
        </div>
        {onClose && (
          <button disabled={busy} onClick={onClose} className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white disabled:opacity-30" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 pb-6">
        <details className="rounded-xl bg-white/[0.04] px-4 py-3 text-sm text-white/65">
          <summary className="cursor-pointer font-medium text-white/85">How to photograph the room for 3D</summary>
          <ul className="mt-2 list-disc space-y-1 pl-4">
            <li>Aim for 12–20 sharp overview shots. Move along the walls, keeping about two thirds of the scene shared between neighbouring shots.</li>
            <li>Frame every corner, the floor, the walls and the hidden sides of furniture from several positions.</li>
            <li>Use the same lens, steady light and furniture that stays put. Upload the original JPG/PNG files, not screenshots or stitched panoramas.</li>
            <li>“Overview” means ordinary wide shots. “Detail” photos help the review but are not used for 3D.</li>
          </ul>
        </details>
        {failed && (
          <p className="flex gap-2 rounded-xl bg-[var(--urgent)]/10 px-3.5 py-3 text-sm text-[var(--urgent)]">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> The last analysis failed: {failed.error}
          </p>
        )}

        {rooms.length === 0 && <p className="rounded-xl bg-white/[0.04] px-4 py-6 text-center text-sm text-white/45">No photos yet.</p>}
        {rooms.map((r) => (
          <section key={r.id}>
            <h3 className="text-sm font-medium">
              {r.label} <span className="font-normal text-white/40">· {r.photos.filter((p) => p.role === "overview").length} overview · {r.photos.filter((p) => p.role === "detail").length} detail</span>
            </h3>
            <div className="mt-2 grid grid-cols-5 gap-2">
              {r.photos.map((p) => (
                <div key={p.id} className="group relative aspect-square overflow-hidden rounded-lg bg-white/5 ring-1 ring-white/10">
                  {/* eslint-disable-next-line @next/next/no-img-element -- same-origin API image */}
                  <img src={p.asset_url} alt="" className="h-full w-full object-cover" />
                  {p.role === "detail" && <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1.5 text-[10px]">detail</span>}
                  <button disabled={busy} onClick={() => remove(p.id)} className="absolute right-1 top-1 hidden h-6 w-6 place-items-center rounded-md bg-black/70 text-white/80 hover:text-white group-hover:grid" aria-label="Remove photo">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </section>
        ))}

        <section className="rounded-xl bg-white/[0.04] p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <input disabled={busy} value={room} onChange={(e) => setRoom(e.target.value)} list="rooms" maxLength={120} placeholder="Room, e.g. Bathroom" aria-label="Room" className={`${field} min-w-0 flex-1`} />
            <datalist id="rooms">
              {scenes.map((s) => (
                <option key={s.id} value={s.label} />
              ))}
            </datalist>
            <select disabled={busy} value={role} onChange={(e) => setRole(e.target.value as "overview" | "detail")} aria-label="Photo type" className={field}>
              <option value="overview">Overview</option>
              <option value="detail">Detail</option>
            </select>
            <label className={`flex h-10 cursor-pointer items-center gap-2 rounded-xl bg-white px-4 text-sm font-medium text-black hover:bg-white/90 ${!room.trim() || busy ? "pointer-events-none opacity-40" : ""}`}>
              {progress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {progress ?? "Add photos"}
              <input
                disabled={busy || !room.trim() || !limits || !modes}
                type="file"
                accept="image/jpeg,image/png"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) upload([...e.target.files]);
                  e.target.value = "";
                }}
              />
            </label>
          </div>
          {limits && <p className="mt-2 text-xs text-white/45">{assessment.photos.length}/{limits.max_photos} photos in the home · JPG/PNG up to {Math.round(limits.max_file_bytes / 1024 / 1024)} MB.{modes?.reconstruction === "vggt" && ` This room: ${roomOverviews}/${limits.frame_cap} overview photos.`}</p>}
          <p className="mt-2 text-xs text-white/45">New photos need a new analysis and review. Hand-completed surfaces must be refitted to the new reconstruction.</p>
        </section>

        <section className="space-y-2">
          <h3 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Consent</h3>
          {(
            [
              ["external_processing_consent", "The photos may be sent to external image-analysis services.", needsPhotoConsent],
              ["clinical_external_processing_consent", "The profile may be sent to the model that assesses the risks for this person.", needsClinicalConsent],
            ] as const
          ).map(([key, label, needed]) => (
            <label key={key} className="flex cursor-pointer items-start gap-3 rounded-xl bg-white/[0.04] px-3.5 py-3 text-sm leading-relaxed text-white/80 hover:bg-white/[0.07]">
              <input type="checkbox" checked={inputs[key]} disabled={busy} onChange={(e) => consent(key, e.target.checked)} className="mt-1 h-4 w-4 shrink-0 accent-white" />
              <span>
                {label} {needed ? <span className="text-white/45">Required with the configured models.</span> : <span className="text-white/35">Not used in demo mode.</span>}
              </span>
            </label>
          ))}
          <p className="px-1 text-xs text-white/35">Adding or removing photos withdraws photo consent: it must be given again.</p>
        </section>
      </div>

      <footer className="border-t border-white/10 px-6 py-4">
        {synthetic && <p className="mb-2 text-xs text-[var(--important)]">Demo mode: hazards and geometry are synthetic, not derived from the photos.</p>}
        {blockers.map((b) => (
          <p key={b} className="text-xs text-white/50">
            {b}
          </p>
        ))}
        {!blockers.length && thinRooms.length > 0 && <p className="text-xs text-white/50">Fewer than 2 overview photos in: {thinRooms.join(", ")}. These rooms will have no 3D.</p>}
        {error && <p className="mt-1 text-xs text-[var(--urgent)]">{error}</p>}
        <button disabled={busy || blockers.length > 0} onClick={analyze} className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-white text-[15px] font-medium text-black transition hover:bg-white/90 disabled:opacity-40">
          {busy && !progress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Start analysis
        </button>
      </footer>
    </div>
  );
}
