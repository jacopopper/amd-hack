"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { categoryLabel } from "@/lib/labels";
import { effective } from "@/lib/hazards";
import type { Capture, Hazard } from "@/lib/types";

const CHANNEL = "step-wise-room";

// The reconstructed room (streamed splats) in its own same-origin engine; pins and cards stay in sync.
export default function CaptureStage({ capture, hazards, selectedId, onSelect, onUnavailable }: { capture: Capture; hazards: Hazard[]; selectedId?: string; onSelect: (id: string) => void; onUnavailable: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [detailed, setDetailed] = useState(false);
  const [error, setError] = useState("");

  const send = useCallback((command: Record<string, unknown>) => {
    frame.current?.contentWindow?.postMessage({ channel: CHANNEL, ...command }, location.origin);
  }, []);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow || event.data?.channel !== CHANNEL) return;
      const message = event.data;
      if (message.type === "ready") setReady(true);
      if (message.type === "splats-ready") setDetailed(true);
      if (message.type === "error") setError(message.text);
      // No WebGL, or a first frame so slow that rendering is software-only: the photos carry the review.
      if (message.type === "error" || message.type === "slow") onUnavailable();
      if (message.type === "select") {
        const hit = Object.entries(capture.pins).find(([, p]) => p.pin === message.id);
        if (hit) onSelect(hit[0]);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [capture, onSelect, onUnavailable]);

  // Pins carry the same names and review order as the finding cards.
  useEffect(() => {
    if (!ready) return;
    const labels: Record<string, string> = {};
    const numbers: Record<string, number> = {};
    hazards.forEach((h, i) => {
      const pin = capture.pins[h.id]?.pin;
      if (!pin) return;
      labels[pin] = categoryLabel[effective(h).category] ?? effective(h).category;
      numbers[pin] = i + 1;
    });
    send({ type: "labels", labels, numbers });
  }, [ready, hazards, capture, send]);

  useEffect(() => {
    const pin = selectedId ? capture.pins[selectedId] : undefined;
    if (!ready || !pin) return;
    send({ type: "highlight", id: pin.pin });
    send({ type: "view", index: pin.view });
  }, [ready, selectedId, capture, send]);

  return (
    <div className="absolute inset-0">
      <iframe ref={frame} src={capture.viewer_url} title="The home in 3D" className="h-full w-full border-0" />
      {!ready && !error && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[#141b20] text-sm text-white/60">
          <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Opening the home in 3D…</span>
        </div>
      )}
      {ready && !detailed && !error && (
        <span className="glass pointer-events-none absolute left-1/2 top-[116px] flex -translate-x-1/2 items-center gap-2 rounded-full px-3 py-1 text-xs text-white/70">
          <Loader2 className="h-3 w-3 animate-spin" /> Loading the room…
        </span>
      )}
      {error && <p className="glass absolute left-1/2 top-24 -translate-x-1/2 rounded-xl px-4 py-2 text-sm text-[var(--urgent)]">Reconstruction unavailable: {error}</p>}
    </div>
  );
}
