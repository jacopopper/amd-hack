import { priorityMeta } from "./labels";
import type { Assessment, Decision, Hazard } from "./types";

// The reviewer's latest version of a finding, or the model's if still undecided.
export const effective = (h: Hazard) => (h.review ? { ...h, ...h.review.effective } : h);

export const sceneOf = (h: Hazard, assessment: Assessment) => assessment.photos.find((p) => p.id === h.evidence[0]?.photo_id)?.scene_id;

export const byPriority = (a: Hazard, b: Hazard) => priorityMeta[effective(a).priority].rank - priorityMeta[effective(b).priority].rank;

// What the reviewer answers, in plain words, and how it maps to the backend decision.
export const answers = {
  confirm: { label: "Confermo", hint: "È un rischio reale: va nel piano d’azione" },
  dismiss: { label: "Non è un problema", hint: "Escluso dal piano" },
  verify: { label: "Serve verificare", hint: "Richiede un controllo o altre foto" },
} as const;
export type Answer = keyof typeof answers;

// How a saved decision reads back to the reviewer, as on the review slide.
export const decisionLabel: Record<Decision, string> = { approve: "Confermato", edit: "Corretto e confermato", reject: "Escluso", defer: "Da verificare" };

export const answerOf = (decision?: Decision): Answer | null =>
  decision === "approve" || decision === "edit" ? "confirm" : decision === "reject" ? "dismiss" : decision === "defer" ? "verify" : null;
