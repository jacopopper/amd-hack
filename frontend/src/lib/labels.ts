import type { Priority } from "./types";

export const categoryLabel: Record<string, string> = {
  rug: "Rug",
  cable: "Floor cable",
  path: "Obstacle in the walkway",
  lighting: "Lighting",
  bathroom: "Bathroom",
  stairs: "Stairs and steps",
  reach: "Items out of reach",
};

export const priorityMeta: Record<Priority, { label: string; color: string; rank: number }> = {
  urgent: { label: "Urgent", color: "var(--urgent)", rank: 0 },
  important: { label: "Important", color: "var(--important)", rank: 1 },
  recommended: { label: "Recommended", color: "var(--recommended)", rank: 2 },
};

export const statusLabel: Record<string, string> = {
  draft: "To complete",
  queued: "Queued",
  processing: "Processing",
  review_ready: "To review",
  report_drafting: "Drafting report",
  report_ready: "Draft ready",
  released: "Released",
  failed: "Processing failed",
};

export const stageLabel: Record<string, string> = {
  prepare: "Preparing photos",
  jev: "Hazards in the photos",
  reconstruct: "3D reconstruction",
  geometry: "Geometry and scale",
  gemma: "Assessment for the patient",
  avatar: "Patient avatar",
  report: "Report and PDF",
};
