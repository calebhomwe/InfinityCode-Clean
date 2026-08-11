import { apiFetch } from "../lib/api";
// Typed client for the Ascension Engine API (Infinity Code X council).
import { API_BASE } from "./api";

export interface ModelCard {
  id: string;
  label: string;
  role: string;
  tier: string;
  available: boolean;
  locked: boolean;
  forms: string[];
  min_form: string;
}

export interface ModelRef {
  id: string;
  role: string;
  tier: string;
  available: boolean;
  locked: boolean;
}

export interface AscensionSnapshot {
  state: string;
  level: number;
  form: string;
  models: ModelRef[];
  cards: ModelCard[];
  dwell_s: number;
  cooldown_s: number;
  dwell_remaining_s: number;
  cooldown_remaining_s: number;
  speed: number;
  effort: number;
  owner_override_required: boolean;
  protected: string[];
}

export interface AscensionLogEntry {
  ts: number;
  event: string;
  state: string;
  from_state?: string;
  to_state?: string;
  reason?: string;
  owner_override?: boolean;
  blocked_by?: string;
}

export interface AscensionResult {
  ok: boolean;
  state?: string;
  from?: string;
  to?: string;
  reason?: string;
  blocked_by?: string | null;
  detail?: string;
}

export interface OwnerInfo {
  name: string;
  alias: string;
  line: string;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as T;
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail ?? `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

export async function fetchState(): Promise<AscensionSnapshot> {
  const res = await apiFetch(`${API_BASE}/ascension/state`);
  if (!res.ok) throw new Error(`state: HTTP ${res.status}`);
  return (await res.json()) as AscensionSnapshot;
}

export async function escalate(reason: string): Promise<AscensionResult> {
  return post<AscensionResult>("/ascension/escalate", { reason });
}

export async function deescalate(reason: string): Promise<AscensionResult> {
  return post<AscensionResult>("/ascension/deescalate", { reason });
}

export async function override(
  target: string,
  passphrase: string,
  reason: string,
): Promise<AscensionResult> {
  return post<AscensionResult>("/ascension/override", {
    target,
    passphrase,
    reason,
  });
}

export async function fetchLog(limit = 12): Promise<AscensionLogEntry[]> {
  const res = await apiFetch(`${API_BASE}/ascension/log?limit=${limit}`);
  if (!res.ok) throw new Error(`log: HTTP ${res.status}`);
  const data = (await res.json()) as { entries?: AscensionLogEntry[] };
  return data.entries ?? [];
}

export async function fetchOwner(): Promise<OwnerInfo> {
  const res = await apiFetch(`${API_BASE}/owner`);
  if (!res.ok) throw new Error(`owner: HTTP ${res.status}`);
  return (await res.json()) as OwnerInfo;
}

export async function runGauntletGap(): Promise<{
  gap: number;
  effort: number;
  suggested: string | null;
  report?: string | null;
}> {
  return post("/gauntlet/gap", {});
}

export const ROLE_LABEL: Record<string, string> = {
  free_router: "Free Router",
  coder: "Primary Coder",
  research: "Research & Evidence",
  planning: "Planning & Memory",
  commander: "Long-Horizon Commander",
  oracle: "Oracle & Verifier",
  advisor: "Advisor",
};

export const FORM_COLORS = [
  "#e8e6e1", // X Code - soft white
  "#e8c46a", // SS1 - gold
  "#f5d76e", // SS2 - electric gold
  "#ffd54a", // SS3 - intense gold
  "#5ac8fa", // Blue - calm cyan/blue
  "#a78bfa", // Mr X Final - violet (gold handled by the aura)
];

export const FORM_NAMES = [
  "X Code",
  "SS1",
  "SS2",
  "SS3",
  "Blue",
  "Mr X Final",
];

/** Map a form level to the body aura class applied by the council panel. */
export function auraClassFor(level: number): string {
  if (level <= 0) return "aura-xcode";
  if (level === 1) return "aura-ss1";
  if (level === 2) return "aura-ss2";
  if (level === 3) return "aura-ss3";
  if (level === 4) return "aura-blue";
  return "aura-final";
}
