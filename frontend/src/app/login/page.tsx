"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import { api, json } from "@/lib/api";
import Brand from "@/components/Brand";

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/login", { method: "POST", body: json({ username: data.get("username"), password: data.get("password") }) });
      router.replace("/");
    } catch {
      setError("Utente o password non corretti.");
      setBusy(false);
    }
  }

  const field = "mt-1.5 h-11 w-full rounded-xl bg-white/[0.06] px-3.5 text-[15px] text-white outline-none ring-1 ring-white/10 transition focus:bg-white/10 focus:ring-white/30";

  return (
    <main className="grid h-full place-items-center bg-[radial-gradient(ellipse_at_50%_30%,#23272d_0%,#0e1013_55%,#0a0b0d_100%)] p-6 text-white">
      <div className="w-full max-w-[380px]">
        <Brand size="lg" />
        <h1 className="mt-10 text-[28px] font-semibold leading-tight tracking-tight">Una casa sicura prima del rientro.</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-white/55">Rivedi i pericoli trovati nelle foto della casa e prepara il piano d’azione per la famiglia.</p>
        <form onSubmit={submit} className="mt-8 space-y-4">
          <label className="block text-sm text-white/60">
            Utente
            <input name="username" defaultValue="reviewer" autoComplete="username" required className={field} />
          </label>
          <label className="block text-sm text-white/60">
            Password
            <input name="password" type="password" autoComplete="current-password" autoFocus required className={field} />
          </label>
          {error && (
            <p className="text-sm text-[var(--urgent)]" role="alert">
              {error}
            </p>
          )}
          <button disabled={busy} className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-white text-[15px] font-medium text-black transition hover:bg-white/90 disabled:opacity-60">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <>Accedi <ArrowRight className="h-4 w-4" /></>}
          </button>
        </form>
        <p className="mt-10 text-xs leading-relaxed text-white/35">Prototipo con dati fittizi. Non è un dispositivo medico.</p>
      </div>
    </main>
  );
}
