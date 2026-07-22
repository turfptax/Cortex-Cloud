const API_BASE = '/api';

export async function apiFetch<T = any>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API error ${resp.status}: ${text}`);
  }

  return resp.json();
}

export function apiStreamUrl(path: string): string {
  return `${API_BASE}${path}`;
}
