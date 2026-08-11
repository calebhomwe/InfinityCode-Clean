// Single source of truth for the backend origin.
//
// The backend is always expected to run on the same machine as the app
// (localhost:8000). Deriving the host from window.location is a security
// footgun: opening the UI from a malicious host would send API keys / chats
// there. For LAN/mobile dev testing, set VITE_API_HOST at build time.

const DEFAULT_BACKEND_PORT = 8000;

function backendHost(): string {
  const override = import.meta.env.VITE_API_HOST as string | undefined;
  if (override) return override;
  return "127.0.0.1";
}

function backendPort(): number {
  // Lets a dev instance run against a second backend while the packaged app
  // keeps 8000. Build-time only, same as VITE_API_HOST.
  const override = import.meta.env.VITE_API_PORT as string | undefined;
  const parsed = override ? Number.parseInt(override, 10) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_BACKEND_PORT;
}

const HOST = backendHost();
const BACKEND_PORT = backendPort();

/** REST base, e.g. http://192.168.1.5:8000/api/v1 */
export const API_BASE = `http://${HOST}:${BACKEND_PORT}/api/v1`;

/** WebSocket base, e.g. ws://192.168.1.5:8000/api/v1 */
export const WS_BASE = `ws://${HOST}:${BACKEND_PORT}/api/v1`;

/** Bare origin, e.g. http://192.168.1.5:8000 (for /system, /health, etc.) */
export const API_ORIGIN = `http://${HOST}:${BACKEND_PORT}`;

// ---------------------------------------------------------------------------
// Session bearer-token auth (Stage 2.2)
//
// The backend generates a fresh token per process and serves it over
// localhost at GET /api/v1/auth/token (exempt from the auth middleware).
// apiFetch attaches it, and on a 401 refetches once + retries. A second 401
// dispatches "infinity:auth-failed" so the shell can show a reconnect banner.
// ---------------------------------------------------------------------------

let _token: string | null = null;
let _inflight: Promise<string | null> | null = null;

async function fetchToken(): Promise<string | null> {
  try {
    const r = await fetch(`${API_ORIGIN}/api/v1/auth/token`);
    if (!r.ok) return null;
    const body = (await r.json()) as { token?: unknown };
    return typeof body.token === "string" && body.token ? body.token : null;
  } catch {
    return null;
  }
}

function getToken(force = false): Promise<string | null> {
  if (_token && !force) return Promise.resolve(_token);
  if (!_inflight) {
    _inflight = fetchToken().then((t) => {
      _token = t;
      _inflight = null;
      return t;
    });
  }
  return _inflight;
}

/** Drop-in fetch replacement that carries the session bearer token. */
export async function apiFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = await getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    const fresh = await getToken(true);
    if (fresh) {
      headers.set("Authorization", `Bearer ${fresh}`);
      res = await fetch(input, { ...init, headers });
    }
    if (res.status === 401) {
      window.dispatchEvent(new Event("infinity:auth-failed"));
    }
  }
  return res;
}
// ---------------------------------------------------------------------------
// Providers status (first-run onboarding)
//
// GET /api/v1/providers returns ProvidersInfo (masked keys + a `configured`
// flag per provider). These helpers are additive - existing call shapes are
// unchanged. The shell uses them to guide a keyless first-run user.
// ---------------------------------------------------------------------------

export interface ProvidersInfo {
  comfyui_url: string;
  fal_key: string;
  novita_key: string;
  elevenlabs_key: string;
  deepseek_key: string;
  dashscope_key: string;
  nvidia_key: string;
  configured: Record<string, boolean>;
  other: { name: string; kind: string; note: string }[];
}

/** Provider ids that can actually power chat/mission requests. */
const LLM_PROVIDER_IDS = [
  "dashscope",
  "deepseek",
  "moonshot",
  "openrouter",
  "minimax",
  "glm",
  "nvidia",
];

/** True when at least one LLM provider key is configured and usable. */
export function hasConfiguredLlmProvider(
  info: ProvidersInfo | null | undefined,
): boolean {
  if (!info || !info.configured) return false;
  return LLM_PROVIDER_IDS.some((id) => info.configured[id] === true);
}

/** Fetch provider status; null on any failure (callers stay silent). */
export async function fetchProvidersInfo(): Promise<ProvidersInfo | null> {
  try {
    const response = await apiFetch(`${API_BASE}/providers`);
    if (!response.ok) return null;
    return (await response.json()) as ProvidersInfo;
  } catch {
    return null;
  }
}
