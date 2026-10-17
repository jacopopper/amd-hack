import { api } from "./api";

// Public demo: each browser reviews its own copy of the case, kept across reloads.
const KEY = "step-wise-demo";

export async function newCopy() {
  const { id } = await api<{ id: string }>("/demo/session", { method: "POST" });
  localStorage.setItem(KEY, id);
  return id;
}

// Copies disappear when the server restarts; open a fresh one when the saved copy is gone.
export async function currentCopy() {
  const saved = localStorage.getItem(KEY);
  if (saved && (await api(`/assessments/${saved}`).then(() => true, () => false))) return saved;
  return newCopy();
}
