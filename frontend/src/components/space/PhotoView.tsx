"use client";

import { useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { categoryLabel, priorityMeta } from "@/lib/labels";
import { effective } from "@/lib/hazards";
import type { Hazard, Photo } from "@/lib/types";

// Inside view: the room's own photos, with each finding's region clickable.
export default function PhotoView({ photos, hazards, photoId, selectedId, panelOpen, onPhoto, onSelect }: { photos: Photo[]; hazards: Hazard[]; photoId: string; selectedId?: string; panelOpen: boolean; onPhoto: (id: string) => void; onSelect: (id: string) => void }) {
  const index = Math.max(0, photos.findIndex((p) => p.id === photoId));
  const photo = photos[index];
  const step = (d: number) => photos.length && onPhoto(photos[(index + d + photos.length) % photos.length].id);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "PageDown") step(1);
      else if (e.key === "PageUp") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!photo) return <div className="absolute inset-0 grid place-items-center bg-[#0a0b0d] text-sm text-white/50">No photos for this room.</div>;
  const regions = hazards.flatMap((h) => effective(h).evidence.filter((e) => e.photo_id === photo.id && e.box).map((e) => ({ h, box: e.box! })));

  return (
    <div className={`absolute inset-0 flex items-center justify-center bg-[#0a0b0d] pb-36 pl-24 pt-20 transition-[padding] ${panelOpen ? "pr-[460px]" : "pr-24"}`}>
      <div className="relative max-h-full" style={{ aspectRatio: `${photo.width} / ${photo.height}` }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- same-origin API image */}
        <img src={photo.asset_url} alt={`Room photo ${index + 1}`} className="max-h-[calc(100vh-14rem)] w-full rounded-lg object-contain" />
        <svg viewBox={`0 0 ${photo.width} ${photo.height}`} className="absolute inset-0 h-full w-full">
          {regions.map(({ h, box }) => {
            const e = effective(h);
            const selected = h.id === selectedId;
            return (
              <g key={h.id} className="cursor-pointer" onClick={() => onSelect(h.id)}>
                <rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} rx={6} fill={selected ? "rgb(255 255 255 / 10%)" : "transparent"} stroke={priorityMeta[e.priority].color} strokeWidth={selected ? 4 : 2.5} strokeDasharray={selected ? undefined : "10 6"} />
                <text x={box[0] + 8} y={box[1] - 10} fill="white" fontSize={Math.max(14, photo.width / 55)} fontWeight={600} style={{ paintOrder: "stroke", stroke: "rgb(0 0 0 / 70%)", strokeWidth: 4 }}>
                  {categoryLabel[e.category] ?? e.category}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {photos.length > 1 && (
        <>
          <button onClick={() => step(-1)} className="glass absolute left-6 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full" aria-label="Previous photo">
            <ChevronLeft className="h-5 w-5" />
          </button>
          <button onClick={() => step(1)} className={`glass absolute top-1/2 ${panelOpen ? "right-[436px]" : "right-6"} grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full`} aria-label="Next photo">
            <ChevronRight className="h-5 w-5" />
          </button>
        </>
      )}
      <span className="glass absolute left-24 top-20 rounded-full px-3 py-1 text-xs tabular-nums text-white/70">
        Photo {index + 1} of {photos.length}
      </span>
    </div>
  );
}
