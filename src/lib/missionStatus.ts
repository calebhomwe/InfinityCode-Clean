import { MODES } from "./composer";

/**
 * Human-facing labels for mission statuses and modes.
 *
 * The backend speaks in enums ("queued", "completed", "3d") and several
 * surfaces used to render those raw. This module is the single place that
 * translates them, so no screen ever shows a raw enum to the user.
 */

/**
 * Statuses the backend actually emits (`backend/main.py` + `backend/core/swarm.py`):
 * queued → running → completed | failed, then optionally approved / rejected.
 * A user-cancelled run is stored as "failed" with a "Cancelled by you."
 * reason. "completed" means the swarm finished and the result is waiting for
 * the user's approve/reject decision — hence the label. The extra aliases
 * keep future or legacy states legible instead of leaking raw enums.
 */
export const MISSION_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Ready for review",
  awaiting_review: "Ready for review",
  ready: "Ready for review",
  approved: "Approved",
  rejected: "Rejected",
  done: "Complete",
  complete: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
  canceled: "Cancelled",
};

/** Title-Case an unknown enum value so it never surfaces as snake_case. */
export function titleCaseEnum(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

export function missionStatusLabel(status: string): string {
  return MISSION_STATUS_LABELS[status] ?? titleCaseEnum(status);
}

/** Human label for a mission mode ("auto" | "code" | "image" | "3d"). */
export function missionModeLabel(mode: string): string {
  return MODES.find((entry) => entry.id === mode)?.label ?? titleCaseEnum(mode);
}
