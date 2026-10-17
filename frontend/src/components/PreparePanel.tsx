"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, Play, Upload, X } from "lucide-react";
import { api, json } from "@/lib/api";
import type { Assessment, Modes, Scene } from "@/lib/types";

const field = "h-10 rounded-xl bg-white/[0.06] px-3 text-sm text-white outline-none ring-1 ring-white/10 transition placeholder:text-white/30 focus:bg-white/10 focus:ring-white/30";

// Before analysis: photos room by room, the consents the configured models need, then start.
export default function PreparePanel({ assessment, scenes, modes, reload, onClose }: { assessment: Assessment; scenes: Scene[]; modes: Modes | null; reload: () => Promise<Assessment>; onClose?: () => void }) {
  const [room, setRoom] = useState(scenes[0]?.label ?? "");
  const [role, setRole] = useState<"overview" | "detail">("overview");
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputs = assessment.inputs;

  const rooms = scenes.map((s) => ({ ...s, photos: assessment.photos.filter((p) => p.scene_id === s.id) }));
  const overview = assessment.photos.filter((p) => p.role === "overview").length;
  const thinRooms = rooms.filter((r) => r.photos.filter((p) => p.role === "overview").length < 2).map((r) => r.label);
  const needsPhotoConsent = modes?.jev === "live" || modes?.gemma === "http";
  const needsClinicalConsent = modes?.gemma === "http";
  const synthetic = modes?.reconstruction === "fixture" && modes?.gemma === "fixture";
  const failed = assessment.status === "failed" ? assessment.jobs.find((j) => j.kind === "analyze" && j.error) : undefined;
  const blockers = [
    !inputs.profile_confirmed && "Il profilo non è confermato.",
    overview === 0 && "Serve almeno una foto panoramica.",
    needsPhotoConsent && !inputs.external_processing_consent && "Manca il consenso all’analisi esterna delle foto.",
    needsClinicalConsent && !inputs.clinical_external_processing_consent && "Manca il consenso all’invio del profilo al modello.",
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
  const upload = (files: FileList) =>
    run(async () => {
      let current = assessment;
      for (const [i, file] of [...files].entries()) {
        setProgress(`Caricamento ${i + 1} di ${files.length}…`);
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
          <h2 className="text-xl font-semibold">Foto della casa</h2>
          <p className="mt-0.5 text-sm text-white/50">Per ogni stanza, foto panoramiche che si sovrappongono girando lungo le pareti. Per il 3D ne servono almeno 2, meglio 12–20.</p>
        </div>
        {onClose && (
          <button onClick={onClose} className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white" aria-label="Chiudi">
            <X className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 pb-6">
        {failed && (
          <p className="flex gap-2 rounded-xl bg-[var(--urgent)]/10 px-3.5 py-3 text-sm text-[var(--urgent)]">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> L’ultima analisi non è riuscita: {failed.error}
          </p>
        )}

        {rooms.length === 0 && <p className="rounded-xl bg-white/[0.04] px-4 py-6 text-center text-sm text-white/45">Nessuna foto ancora.</p>}
        {rooms.map((r) => (
          <section key={r.id}>
            <h3 className="text-sm font-medium">
              {r.label} <span className="font-normal text-white/40">· {r.photos.length} foto</span>
            </h3>
            <div className="mt-2 grid grid-cols-5 gap-2">
              {r.photos.map((p) => (
                <div key={p.id} className="group relative aspect-square overflow-hidden rounded-lg bg-white/5 ring-1 ring-white/10">
                  {/* eslint-disable-next-line @next/next/no-img-element -- same-origin API image */}
                  <img src={p.asset_url} alt="" className="h-full w-full object-cover" />
                  {p.role === "detail" && <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1.5 text-[10px]">dettaglio</span>}
                  <button disabled={busy} onClick={() => remove(p.id)} className="absolute right-1 top-1 hidden h-6 w-6 place-items-center rounded-md bg-black/70 text-white/80 hover:text-white group-hover:grid" aria-label="Rimuovi la foto">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </section>
        ))}

        <section className="rounded-xl bg-white/[0.04] p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <input value={room} onChange={(e) => setRoom(e.target.value)} list="rooms" maxLength={120} placeholder="Stanza, es. Bagno" aria-label="Stanza" className={`${field} min-w-0 flex-1`} />
            <datalist id="rooms">
              {scenes.map((s) => (
                <option key={s.id} value={s.label} />
              ))}
            </datalist>
            <select value={role} onChange={(e) => setRole(e.target.value as "overview" | "detail")} aria-label="Tipo di foto" className={field}>
              <option value="overview">Panoramiche</option>
              <option value="detail">Dettaglio</option>
            </select>
            <label className={`flex h-10 cursor-pointer items-center gap-2 rounded-xl bg-white px-4 text-sm font-medium text-black hover:bg-white/90 ${!room.trim() || busy ? "pointer-events-none opacity-40" : ""}`}>
              {progress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {progress ?? "Aggiungi foto"}
              <input
                type="file"
                accept="image/jpeg,image/png"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) upload(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Consensi</h3>
          {(
            [
              ["external_processing_consent", "Le foto possono essere inviate ai servizi esterni di analisi delle immagini.", needsPhotoConsent],
              ["clinical_external_processing_consent", "Il profilo può essere inviato al modello che valuta i rischi per questa persona.", needsClinicalConsent],
            ] as const
          ).map(([key, label, needed]) => (
            <label key={key} className="flex cursor-pointer items-start gap-3 rounded-xl bg-white/[0.04] px-3.5 py-3 text-sm leading-relaxed text-white/80 hover:bg-white/[0.07]">
              <input type="checkbox" checked={inputs[key]} disabled={busy} onChange={(e) => consent(key, e.target.checked)} className="mt-1 h-4 w-4 shrink-0 accent-white" />
              <span>
                {label} {needed ? <span className="text-white/45">Necessario con i modelli configurati.</span> : <span className="text-white/35">Non usato in modalità dimostrativa.</span>}
              </span>
            </label>
          ))}
          <p className="px-1 text-xs text-white/35">Aggiungere o togliere foto ritira il consenso alle foto: va dato di nuovo.</p>
        </section>
      </div>

      <footer className="border-t border-white/10 px-6 py-4">
        {synthetic && <p className="mb-2 text-xs text-[var(--important)]">Modalità dimostrativa: i pericoli e la geometria sono sintetici, non ricavati dalle foto.</p>}
        {blockers.map((b) => (
          <p key={b} className="text-xs text-white/50">
            {b}
          </p>
        ))}
        {!blockers.length && thinRooms.length > 0 && <p className="text-xs text-white/50">Meno di 2 panoramiche in: {thinRooms.join(", ")}. Queste stanze resteranno senza 3D.</p>}
        {error && <p className="mt-1 text-xs text-[var(--urgent)]">{error}</p>}
        <button disabled={busy || blockers.length > 0} onClick={analyze} className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-white text-[15px] font-medium text-black transition hover:bg-white/90 disabled:opacity-40">
          {busy && !progress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Avvia l’analisi
        </button>
      </footer>
    </div>
  );
}
