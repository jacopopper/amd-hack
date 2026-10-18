"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { api, json, DEMO } from "@/lib/api";
import type { Profile } from "@/lib/types";
import Brand from "@/components/Brand";

const HomeLink = DEMO ? "a" : Link;

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

  if (DEMO) return <main className="grid h-full place-content-center gap-4 bg-[#0a0b0d] p-8 text-white"><p>Read-only demo: explore the reconstructed home.</p><HomeLink href="/" className="underline">Back to demo</HomeLink></main>;

  return (
    <div className="min-h-full bg-[#0a0b0d] text-white">
      <header className="mx-auto flex max-w-3xl items-center gap-4 px-8 pt-8">
        <Link href="/" className="grid h-10 w-10 place-items-center rounded-xl text-white/60 hover:bg-white/10 hover:text-white" aria-label="Back to patients">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <Brand />
      </header>
      <main className="mx-auto max-w-3xl px-8 pb-16 pt-10">
        <h1 className="text-3xl font-semibold tracking-tight">New patient</h1>
        <p className="mt-1 text-white/50">Who lives in the home and what they need. Photos come right after.</p>

        <form onSubmit={submit} className="mt-10 space-y-10">
          <section className="space-y-4">
            <label className="block text-sm text-white/60">
              Name or label
              <input name="label" required maxLength={120} placeholder="e.g. Maria R. · Via Roma" className={field} />
            </label>
            <label className="block text-sm text-white/60">
              About the person
              <textarea name="description" required maxLength={2000} rows={3} placeholder="Approximate age, situation, what worries the family…" className={`${field} resize-none`} />
            </label>
          </section>

          <section>
            <h2 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Mobility and needs</h2>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm text-white/60">
                Walking aid
                <input name="mobility_aid" maxLength={200} placeholder="e.g. walker, cane" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Aid width (cm)
                <input name="aid_width_cm" type="number" min={1} max={300} step={1} placeholder="e.g. 60" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Vision
                <input name="vision" maxLength={500} placeholder="e.g. cataracts, reading glasses" className={field} />
              </label>
              <label className="block text-sm text-white/60">
                Support needed
                <input name="support_needs" maxLength={500} placeholder="e.g. help getting up at night" className={field} />
              </label>
            </div>
            <label className="mt-4 block text-sm text-white/60">
              Relevant history
              <textarea name="relevant_history" maxLength={1000} rows={2} placeholder="e.g. fall in the bathroom in March, hip replacement" className={`${field} resize-none`} />
            </label>
            <label className="mt-4 block text-sm text-white/60">
              Everyday routes <span className="text-white/35">· one per line</span>
              <textarea name="daily_routes" rows={3} placeholder={"Bedroom → bathroom at night\nKitchen → living room"} className={`${field} resize-none`} />
            </label>
          </section>

          <section className="space-y-2">
            <h2 className="text-[13px] font-medium uppercase tracking-wider text-white/40">Confirmations</h2>
            <Check name="profile_confirmed" required>
              I have checked this information with the person or their family.
            </Check>
            <Check name="staged_data" required>
              I am using only fictional data and photos of a home staged for testing: the prototype is not ready for real patients.
            </Check>
            <Check name="external_processing_consent">
              The photos may be sent to external image-analysis services. <span className="text-white/45">Without this consent the live analysis will not start.</span>
            </Check>
            <Check name="clinical_external_processing_consent">
              The profile may be sent to the model that assesses the risks for this person.
            </Check>
          </section>

          {error && (
            <p className="text-sm text-[var(--urgent)]" role="alert">
              {error}
            </p>
          )}
          <button disabled={busy} className="flex h-11 items-center gap-2 rounded-xl bg-white px-5 text-[15px] font-medium text-black transition hover:bg-white/90 disabled:opacity-60">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <>Continue to photos <ArrowRight className="h-4 w-4" /></>}
          </button>
        </form>
      </main>
    </div>
  );
}
