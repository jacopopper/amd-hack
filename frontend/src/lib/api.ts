export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
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
