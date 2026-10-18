export const DEMO = process.env.NEXT_PUBLIC_STATIC_DEMO === "true";

let demoData: Promise<Record<string, unknown>> | undefined;

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (DEMO) {
    if ((options.method ?? "GET").toUpperCase() !== "GET") throw new Error("Read-only demo: changes are not saved.");
    demoData ??= fetch("/demo/responses.json").then((response) => {
      if (!response.ok) throw new Error("Demo unavailable.");
      return response.json();
    }).catch((error) => { demoData = undefined; throw error; });
    const data = await demoData;
    if (!Object.hasOwn(data, path)) throw new Error("Content unavailable in this demo.");
    return structuredClone(data[path]) as T;
  }
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: "Service unavailable" }));
    const message = error.error ?? error.detail ?? `Error ${response.status}`;
    throw new Error(response.status === 409 ? `${message} Reload before trying again.` : message);
  }
  return response.json();
}

export const json = (value: unknown) => JSON.stringify(value);
