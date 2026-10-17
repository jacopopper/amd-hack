"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { api, json } from "@/lib/api";
import type { Profile } from "@/lib/types";
import Brand from "@/components/Brand";

const field = "mt-1.5 w-full rounded-xl bg-white/[0.06] px-3.5 py-2.5 text-[15px] text-white outline-none ring-1 ring-white/10 transition placeholder:text-white/30 focus:bg-white/10 focus:ring-white/30";

function Check({ name, required, children }: { name: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-xl bg-white/[0.04] px-3.5 py-3 text-sm leading-relaxed text-white/80 hover:bg-white/[0.07]">
      <input type="checkbox" name={name} required={required} className="mt-1 h-4 w-4 shrink-0 accent-white" />
      <span>{children}</span>
    </label>
  );
}

// Intake: who the person is and what they need. Photos are added on the next screen.
export default function NewPatientPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (key: string) => String(data.get(key) ?? "").trim() || null;
    const width = text("aid_width_cm");
    const profile: Profile = {
      description: text("description") ?? "",
      mobility_aid: text("mobility_aid"),
      aid_width_m: width ? Number(width) / 100 : null,
      vision: text("vision"),
      support_needs: text("support_needs"),
      relevant_history: text("relevant_history"),
      daily_routes: String(data.get("daily_routes") ?? "").split("\n").map((r) => r.trim()).filter(Boolean),
    };
    setBusy(true);
    setError("");
    try {
      const { id } = await api<{ id: string }>("/assessments", {
        method: "POST",
        body: json({
          label: text("label"),
          profile,
          profile_confirmed: data.get("profile_confirmed") === "on",
          staged_data_confirmed: true,
          processing_consent: true,
          external_processing_consent: data.get("external_processing_consent") === "on",
          clinical_external_processing_consent: data.get("clinical_external_processing_consent") === "on",
          clinical_facts: [],
        }),
      });
      router.replace(`/pazienti?id=${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="min-h-full bg-[#0a0b0d] text-white">
      <header className="mx-auto flex max-w-3xl items-center gap-4 px-8 pt-8">
        <Link href="/" className="grid h-10 w-10 place-items-center rounded-xl text-white/60 hover:bg-white/10 hover:text-white" aria-label="Torna ai pazienti">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <Brand />
      </header>
      <main className="mx-auto max-w-3xl px-8 pb-16 pt-10">
        <h1 className="text-3xl font-semibold tracking-tight">Nuovo paziente</h1>
        <p className="mt-1 text-white/50">Chi vive nella casa e di cosa ha bisogno. Le foto si aggiungono subito dopo.</p>

        <form onSubmit={submit} className="mt-10 space-y-10">
          <section className="space-y-4">
            <label className="block text-sm text-white/60">
              Nome o etichetta
              <input name="label" required maxLength={120} placeholder="Es. Maria R. · Via Roma" className={field} />
            </label>
            <label className="block text-sm text-white/60">
              Descrizione della persona
              <textarea name="description" required maxLength={2000} rows={3} placeholder="Età indicativa, situazione, cosa preoccupa la famiglia…" className={`${field} resize-none`} />
            </label>
          </section>

          <section>
            <h2 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Mobilità e bisogni</h2>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm text-white/60">
                Ausilio per camminare
                <input name="mobility_aid" maxLength={200} placeholder="Es. deambulatore, bastone" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Larghezza dell’ausilio (cm)
                <input name="aid_width_cm" type="number" min={1} max={300} step={1} placeholder="Es. 60" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Vista
                <input name="vision" maxLength={500} placeholder="Es. cataratta, occhiali da lettura" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Supporto necessario
                <input name="support_needs" maxLength={500} placeholder="Es. aiuto per alzarsi di notte" className={field} />
              </label>
            </div>
            <label className="mt-4 block text-sm text-white/60">
              Storia rilevante
              <textarea name="relevant_history" maxLength={1000} rows={2} placeholder="Es. caduta in bagno a marzo, protesi d’anca" className={`${field} resize-none`} />
            </label>
            <label className="mt-4 block text-sm text-white/60">
              Percorsi di tutti i giorni <span className="text-white/35">· uno per riga</span>
              <textarea name="daily_routes" rows={3} placeholder={"Camera → bagno di notte\nCucina → soggiorno"} className={`${field} resize-none`} />
            </label>
          </section>

          <section className="space-y-2">
            <h2 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Conferme</h2>
            <Check name="profile_confirmed" required>
              Ho verificato queste informazioni con la persona o la famiglia.
            </Check>
            <Check name="staged_data" required>
              Uso solo dati fittizi e foto di una casa allestita per la prova: il prototipo non è pronto per pazienti reali.
            </Check>
            <Check name="external_processing_consent">
              Le foto possono essere inviate ai servizi esterni di analisi delle immagini. <span className="text-white/45">Senza questo consenso l’analisi dal vivo non parte.</span>
            </Check>
            <Check name="clinical_external_processing_consent">
              Il profilo può essere inviato al modello che valuta i rischi per questa persona.
            </Check>
          </section>

          {error && (
            <p className="text-sm text-[var(--urgent)]" role="alert">
              {error}
            </p>
          )}
          <button disabled={busy} className="flex h-11 items-center gap-2 rounded-xl bg-white px-5 text-[15px] font-medium text-black transition hover:bg-white/90 disabled:opacity-60">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <>Continua con le foto <ArrowRight className="h-4 w-4" /></>}
          </button>
        </form>
      </main>
    </div>
  );
}
