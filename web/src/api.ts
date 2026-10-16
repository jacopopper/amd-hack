export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, {...options, headers, credentials: 'same-origin'});
  if (!response.ok) {
    const error = await response.json().catch(() => ({error: 'Servizio non disponibile'}));
    throw new Error(response.status === 409 ? `${error.error ?? error.detail} Ricaricare prima di riprovare.` : (error.error ?? error.detail ?? `Errore ${response.status}`));
  }
  return response.json();
}
export const json = (value: unknown) => JSON.stringify(value);
