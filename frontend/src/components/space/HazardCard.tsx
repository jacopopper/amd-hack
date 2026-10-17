"use client";

import { useEffect, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Crosshair, HelpCircle, Loader2, Pencil, X } from "lucide-react";
import { categoryLabel, priorityMeta } from "@/lib/labels";
import { answerOf, answers, effective, type Answer } from "@/lib/hazards";
import type { Anchor, Decision, Hazard, Photo, Priority } from "@/lib/types";
import EvidenceThumb from "./EvidenceThumb";

export type ReviewPayload = { decision: Decision; note: string; observation?: string; suggested_action?: string; priority?: Priority; anchor?: Anchor | null };

const answerIcon: Record<Answer, typeof Check> = { confirm: Check, dismiss: X, verify: HelpCircle };

// Mattertag-style card: what was found, why it matters for this person, and one question to answer.
export default function HazardCard({
  hazard,
  photos,
  patientName,
  index,
  total,
  disabled,
  placing,
  pickedAnchor,
  onPrev,
  onNext,
  onClose,
  onTogglePlace,
  onSave,
  onOpenPhoto,
}: {
  hazard: Hazard;
  photos: Photo[];
  patientName: string;
  index: number;
  total: number;
  disabled: boolean;
  placing: boolean;
  pickedAnchor: Anchor | null;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onTogglePlace: () => void;
  onSave: (payload: ReviewPayload) => Promise<void>;
  onOpenPhoto: (photoId: string) => void;
}) {
  const current = effective(hazard);
  const meta = priorityMeta[current.priority];
  const answered = answerOf(hazard.review?.decision);
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState(current.suggested_action);
  const [priority, setPriority] = useState<Priority>(current.priority);
  const [verifying, setVerifying] = useState(false);
  const [note, setNote] = useState(hazard.review?.note ?? "");
  const [busy, setBusy] = useState<Answer | "place" | null>(null);
  const [error, setError] = useState("");
  const evidence = current.evidence[0];
  const photo = photos.find((p) => p.id === evidence?.photo_id);
  const changed = action !== current.suggested_action || priority !== current.priority;

  async function submit(answer: Answer | "place", payload: ReviewPayload) {
    setBusy(answer);
    setError("");
    try {
      await onSave(payload);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  const confirm = () =>
    submit("confirm", changed ? { decision: "edit", note, observation: current.observation, suggested_action: action, priority } : { decision: "approve", note });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(event.target.tagName)) return;
      if (event.key === "ArrowRight") onNext();
      else if (event.key === "ArrowLeft") onPrev();
      else if (event.key === "Escape") onClose();
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onNext, onPrev, onClose]);

  return (
    <aside className="glass glass-solid flex max-h-full w-[400px] flex-col overflow-hidden rounded-2xl">
      <header className="flex items-center gap-2 px-4 pb-2 pt-3.5">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
        <span className="text-[13px] font-medium" style={{ color: meta.color }}>
          {meta.label}
        </span>
        <span className="text-[13px] text-white/45">· {categoryLabel[current.category] ?? current.category}</span>
        <span className="ml-auto flex items-center text-xs text-white/45">
          <button onClick={onPrev} className="grid h-7 w-7 place-items-center rounded-lg hover:bg-white/10 hover:text-white" aria-label="Precedente">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="tabular-nums">
            {index + 1} di {total}
          </span>
          <button onClick={onNext} className="grid h-7 w-7 place-items-center rounded-lg hover:bg-white/10 hover:text-white" aria-label="Successivo">
            <ChevronRight className="h-4 w-4" />
          </button>
          <button onClick={onClose} className="ml-1 grid h-7 w-7 place-items-center rounded-lg hover:bg-white/10 hover:text-white" aria-label="Chiudi">
            <X className="h-4 w-4" />
          </button>
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <h2 className="text-lg font-semibold leading-snug">{current.observation}</h2>

        <button onClick={() => photo && onOpenPhoto(photo.id)} className="mt-3 block w-full overflow-hidden rounded-xl ring-1 ring-white/10 transition hover:ring-white/30" title="Apri la foto">
          <EvidenceThumb photo={photo} evidence={evidence} width={368} height={180} />
        </button>

        <h3 className="mt-5 text-[13px] font-medium text-white/50">Perché conta per {patientName}</h3>
        <p className="mt-1 text-[15px] leading-relaxed text-white/90">{current.profile_relevance}</p>

        <div className="mt-5 flex items-center justify-between">
          <h3 className="text-[13px] font-medium text-white/50">Cosa fare</h3>
          {!editing && !disabled && (
            <button onClick={() => setEditing(true)} className="flex items-center gap-1 text-xs text-white/45 hover:text-white">
              <Pencil className="h-3 w-3" /> Modifica
            </button>
          )}
        </div>
        {editing ? (
          <div className="mt-1.5 space-y-2">
            <textarea value={action} onChange={(e) => setAction(e.target.value)} rows={3} className="w-full resize-none rounded-lg border border-white/15 bg-black/30 p-2.5 text-sm outline-none focus:border-white/40" />
            <div className="flex gap-1.5">
              {(["urgent", "important", "recommended"] as Priority[]).map((p) => (
                <button key={p} onClick={() => setPriority(p)} className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs transition ${priority === p ? "bg-white/15 text-white" : "text-white/50 hover:bg-white/5"}`}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: priorityMeta[p].color }} />
                  {priorityMeta[p].label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <p className="mt-1 text-[15px] leading-relaxed text-white/90">{action}</p>
        )}

        <details className="group mt-5 text-[13px] text-white/50">
          <summary className="cursor-pointer list-none hover:text-white/80">
            <span className="group-open:hidden">Mostra dettagli e fonti</span>
            <span className="hidden group-open:inline">Nascondi dettagli</span>
          </summary>
          <div className="mt-2 space-y-2 leading-relaxed">
            <p>{current.uncertainty}</p>
            <p>Linee guida: {current.guidance_ids.join(", ") || "nessuna"}</p>
            {hazard.producer && <p>Rilevato da: {hazard.producer}</p>}
            {!disabled && (
              <button onClick={onTogglePlace} className={`flex items-center gap-1.5 ${placing ? "text-white" : "hover:text-white"}`}>
                <Crosshair className="h-3.5 w-3.5" />
                {placing ? "Clicca nella casa per posizionarlo… (annulla)" : current.anchor ? "Correggi la posizione nella casa" : "Posiziona nella casa"}
              </button>
            )}
          </div>
        </details>
        {pickedAnchor && (
          <div className="mt-3 flex items-center justify-between rounded-lg bg-white/10 px-3 py-2 text-[13px]">
            Nuova posizione scelta
            <button
              disabled={!!busy}
              onClick={() => submit("place", { decision: "edit", note, observation: current.observation, suggested_action: action, priority, anchor: pickedAnchor })}
              className="rounded-md bg-white px-2.5 py-1 text-xs font-medium text-black"
            >
              Salva
            </button>
          </div>
        )}
      </div>

      <footer className="border-t border-white/10 p-3">
        {answered && !verifying ? (
          <p className="mb-2 px-1 text-[13px] text-white/55">
            Hai risposto: <span className="text-white">{answers[answered].label}</span>
            {hazard.review?.note && <> · “{hazard.review.note}”</>}
          </p>
        ) : null}
        {verifying ? (
          <div className="space-y-2">
            <textarea
              autoFocus
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="Cosa serve verificare? Es. foto del bagno, fissaggio del tappeto…"
              className="w-full resize-none rounded-lg border border-white/15 bg-black/30 p-2.5 text-sm outline-none placeholder:text-white/35 focus:border-white/40"
            />
            <div className="flex gap-2">
              <button onClick={() => setVerifying(false)} className="flex-1 rounded-xl py-2.5 text-sm text-white/60 hover:bg-white/5">
                Annulla
              </button>
              <button
                disabled={!note.trim() || !!busy}
                onClick={() => submit("verify", { decision: "defer", note })}
                className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-white py-2.5 text-sm font-medium text-black disabled:opacity-40"
              >
                {busy === "verify" && <Loader2 className="h-4 w-4 animate-spin" />} Invia
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <button
              disabled={disabled || !!busy}
              onClick={confirm}
              className={`flex w-full items-center justify-center gap-2 rounded-xl py-3 text-[15px] font-medium transition disabled:opacity-40 ${answered === "confirm" ? "bg-white/15 text-white ring-1 ring-white/40" : "bg-white text-black hover:bg-white/90"}`}
            >
              {busy === "confirm" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              {answers.confirm.label}
              {changed && <span className="text-xs opacity-60">con modifiche</span>}
            </button>
            <div className="grid grid-cols-2 gap-1.5">
              {(["dismiss", "verify"] as Answer[]).map((a) => {
                const Icon = answerIcon[a];
                return (
                  <button
                    key={a}
                    disabled={disabled || !!busy}
                    onClick={() => (a === "verify" ? setVerifying(true) : submit("dismiss", { decision: "reject", note }))}
                    className={`flex items-center justify-center gap-1.5 rounded-xl py-2.5 text-sm transition disabled:opacity-40 ${answered === a ? "bg-white/15 text-white ring-1 ring-white/40" : "bg-white/[0.06] text-white/80 hover:bg-white/10"}`}
                  >
                    {busy === a ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
                    {answers[a].label}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        {error && <p className="mt-2 px-1 text-xs text-[var(--urgent)]">{error}</p>}
      </footer>
    </aside>
  );
}
