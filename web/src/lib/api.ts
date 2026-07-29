const API_BASE = '/api';

/** Fired when the gateway says the Entra session is gone. Layout listens and
 * shows a sign-in bar. Without this, an expired session is indistinguishable
 * from an empty corpus: the fetch throws, callers coalesce to `?? []`, and the
 * owner is told "Your corpus is quiet" about 3,900 gists. */
export const SESSION_EXPIRED_EVENT = 'cortex:session-expired';

/** A failed API call, with the status and the server's own explanation kept
 * intact. Before this every failure collapsed into one untyped Error, so
 * nothing could tell "not signed in" from "not allowed" from "backend down",
 * and a grep for "401" across web/src returned zero hits. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly path: string;

  constructor(status: number, detail: string, path: string) {
    super(detail ? `${status}: ${detail}` : `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.path = path;
  }

  /** The gateway answers an unauthenticated /api call with 401 rather than a
   * redirect, specifically so the SPA fails visibly. Honour that. */
  get isSessionExpired(): boolean {
    return this.status === 401;
  }

  /** Signed in, but this corpus is not yours (owner OID mismatch). */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** A 403 with no body at all did not come from Cortex: the platform auth
   * middleware rejected it before the app was reached. This is what every
   * authenticated POST looked like while Referrer-Policy was `no-referrer`,
   * and it cost an evening to identify because the UI only said "403:". */
  get isBlockedUpstream(): boolean {
    return this.status === 403 && this.detail === '';
  }

  /** What to actually show a human. */
  get userMessage(): string {
    if (this.isSessionExpired) return 'Your session expired. Sign in again to continue.';
    if (this.isBlockedUpstream) {
      return 'Blocked before reaching Cortex. That is a platform auth or config issue, not your data.';
    }
    if (this.isForbidden) return `Not allowed: ${this.detail}`;
    if (this.status >= 500) return `Cortex is not responding (${this.status}).`;
    return this.detail || `Request failed (${this.status}).`;
  }
}

/** Pull the server's explanation out of whatever it sent. FastAPI uses
 * {"detail": ...}; the core's envelope uses {"error": ...}; the platform sends
 * nothing at all. */
function extractDetail(text: string): string {
  if (!text) return '';
  try {
    const body = JSON.parse(text);
    const d = body?.detail ?? body?.error;
    if (typeof d === 'string') return d;
    if (d) return JSON.stringify(d);
  } catch {
    /* not JSON, fall through to the raw text */
  }
  return text.slice(0, 300);
}

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
    const err = new ApiError(
      resp.status, extractDetail(await resp.text()), path);
    // Announce once, centrally, so every surface reacts instead of each one
    // silently rendering "nothing here".
    if (err.isSessionExpired) {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    }
    throw err;
  }

  return resp.json();
}

export function apiStreamUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/** Send the owner to Entra and bring them back where they were. The hash is
 * preserved so re-authenticating does not also lose the tab they were on. */
export function signInUrl(): string {
  const back = encodeURIComponent(
    window.location.pathname + window.location.search + window.location.hash);
  return `/.auth/login/aad?post_login_redirect_uri=${back}`;
}

export const SIGN_OUT_URL = '/.auth/logout';
