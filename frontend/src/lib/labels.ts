import type { Priority } from "./types";

export const categoryLabel: Record<string, string> = {
  rug: "Tappeto",
  cable: "Cavo a terra",
  path: "Ostacolo sul percorso",
  lighting: "Illuminazione",
  bathroom: "Bagno",
  stairs: "Scale e gradini",
  reach: "Oggetti fuori portata",
};

export const priorityMeta: Record<Priority, { label: string; color: string; rank: number }> = {
  urgent: { label: "Urgente", color: "var(--urgent)", rank: 0 },
  important: { label: "Importante", color: "var(--important)", rank: 1 },
  recommended: { label: "Consigliato", color: "var(--recommended)", rank: 2 },
};

export const statusLabel: Record<string, string> = {
  draft: "Da completare",
  queued: "In coda",
  processing: "In elaborazione",
  review_ready: "Da revisionare",
  report_drafting: "Bozza in preparazione",
  report_ready: "Bozza pronta",
  released: "Rilasciato",
  failed: "Elaborazione fallita",
};

export const stageLabel: Record<string, string> = {
  prepare: "Preparazione foto",
  jev: "Pericoli nelle foto",
  reconstruct: "Ricostruzione 3D",
  geometry: "Geometria e scala",
  gemma: "Valutazione sul paziente",
  avatar: "Avatar del paziente",
  report: "Report e PDF",
};
