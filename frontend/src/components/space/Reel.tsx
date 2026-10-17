"use client";

import { Check, HelpCircle, Play, X } from "lucide-react";
import { categoryLabel, priorityMeta } from "@/lib/labels";
import { answerOf, effective } from "@/lib/hazards";
import type { Hazard, Photo } from "@/lib/types";
import EvidenceThumb from "./EvidenceThumb";

const answerBadge = { confirm: Check, dismiss: X, verify: HelpCircle };

// Highlight reel: the findings of this room in review order; play starts the guided review.
export default function Reel({ hazards, photos, selectedId, decided, total, pulse, onSelect, onPlay }: { hazards: Hazard[]; photos: Photo[]; selectedId?: string; decided: number; total: number; pulse?: boolean; onSelect: (id: string) => void; onPlay: () => void }) {
  return (
    <div className="glass flex items-center gap-3 rounded-2xl p-2">
      <button onClick={onPlay} className={`flex h-[76px] shrink-0 flex-col items-center justify-center gap-1 rounded-xl bg-white px-4 text-black transition hover:bg-white/90 ${pulse ? "pulse-cta" : ""}`}>
        <Play className="h-5 w-5 fill-black" />
        <span className="text-xs font-medium">{decided === 0 ? "Start review" : decided < total ? "Continue" : "Review again"}</span>
      </button>
      <div className="flex min-w-0 gap-2 overflow-x-auto">
        {hazards.map((h) => {
          const e = effective(h);
          const answered = answerOf(h.review?.decision);
          const Badge = answered ? answerBadge[answered] : null;
          const selected = h.id === selectedId;
          return (
            <button
              key={h.id}
              onClick={() => onSelect(h.id)}
              className={`relative shrink-0 overflow-hidden rounded-xl text-left ring-2 transition ${selected ? "ring-white" : "ring-transparent hover:ring-white/40"} ${answered === "dismiss" ? "opacity-45" : ""}`}
            >
              <EvidenceThumb photo={photos.find((p) => p.id === e.evidence[0]?.photo_id)} evidence={e.evidence[0]} width={128} height={76} />
              <span className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/10 to-transparent" />
              <span className="absolute left-2 top-2 h-2 w-2 rounded-full ring-2 ring-black/40" style={{ background: priorityMeta[e.priority].color }} />
              {Badge && (
                <span className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full bg-white text-black">
                  <Badge className="h-3 w-3" strokeWidth={3} />
                </span>
              )}
              <span className="absolute bottom-1.5 left-2 right-2 truncate text-xs font-medium text-white">{categoryLabel[e.category] ?? e.category}</span>
            </button>
          );
        })}
        {!hazards.length && <p className="px-3 text-sm text-white/50">No hazards in this room.</p>}
      </div>
    </div>
  );
}
