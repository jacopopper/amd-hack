import type { Evidence, Photo } from "@/lib/types";

// Crops a photo around the evidence box with CSS only; falls back to the whole photo.
export default function EvidenceThumb({ photo, evidence, width, height, className = "" }: { photo?: Photo; evidence?: Evidence; width: number; height: number; className?: string }) {
  if (!photo) return <div style={{ width, height }} className={`bg-white/5 ${className}`} />;
  const box = evidence?.photo_id === photo.id ? evidence.box : null;
  const [x0, y0, x1, y1] = box ?? [0, 0, photo.width, photo.height];
  const pad = box ? 0.9 : 0;
  const w = (x1 - x0) * (1 + pad * 2);
  const h = (y1 - y0) * (1 + pad * 2);
  const scale = Math.max(width / Math.min(w, photo.width), height / Math.min(h, photo.height));
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const left = Math.min(0, Math.max(width - photo.width * scale, width / 2 - cx * scale));
  const top = Math.min(0, Math.max(height - photo.height * scale, height / 2 - cy * scale));
  return (
    <div
      role="img"
      aria-label="Dettaglio della foto"
      style={{ width, height, backgroundImage: `url(${photo.asset_url})`, backgroundSize: `${photo.width * scale}px ${photo.height * scale}px`, backgroundPosition: `${left}px ${top}px` }}
      className={`bg-no-repeat ${className}`}
    />
  );
}
