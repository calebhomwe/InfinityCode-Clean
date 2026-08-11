// HandoffArtifact v2.0 — 1:1 mirror of backend/runtime/schema/handoff.py.
//
// Every agent-to-agent communication uses this contract. The frontend
// validates on receive so a malformed handoff surfaces in the UI as a
// schema-rejection error, not a silent corrupt state.
//
// If you change a field name here, change it in the Python file too — the
// two schemas MUST stay in lock-step. There is no code-generator; a runtime
// check in the state machine will fail loudly on drift.

export const HANDOFF_SCHEMA_VERSION = "2.0" as const;
export const HANDOFF_MAX_SUMMARY_TOKENS = 500;
export const HANDOFF_TOKEN_CHAR_RATIO = 4.0;

export interface HandoffContext {
  /** Compressed narrative. ≤ 500 tokens (chars/4). */
  summary: string;
  keyDecisions: string[];
  openQuestions: string[];
  risks: string[];
}

export interface HandoffArtifacts {
  filePaths: string[];
  toolOutputs: unknown[];
  /** IDs into the search cache. Never the raw text they point to. */
  searchCacheIds: string[];
}

export interface HandoffArtifact {
  version: typeof HANDOFF_SCHEMA_VERSION;
  fromAgent: string;
  toAgent: string;
  taskId: string;
  context: HandoffContext;
  artifacts: HandoffArtifacts;
  verificationRequired: boolean;
  acceptanceCriteria: string[];
}

/** Thrown by `validateHandoff` when a payload violates the non-negotiables.
 *  Distinct class so callers can catch just schema failures and route them
 *  to the schema-rejection UI without swallowing unrelated errors. */
export class HandoffValidationError extends Error {
  readonly name = "HandoffValidationError";
}

function tokensIsh(text: string): number {
  return Math.max(1, Math.floor(text.length / HANDOFF_TOKEN_CHAR_RATIO));
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === "string" && v.length > 0;
}

/** Strict runtime check. Returns the value narrowed to HandoffArtifact on
 *  success; throws HandoffValidationError otherwise. Mirrors the pydantic
 *  validators in the Python side so both surfaces reject the same shapes. */
export function validateHandoff(value: unknown): HandoffArtifact {
  if (!value || typeof value !== "object") {
    throw new HandoffValidationError("HandoffArtifact must be an object.");
  }
  const v = value as Record<string, unknown>;

  if (v.version !== HANDOFF_SCHEMA_VERSION) {
    throw new HandoffValidationError(
      `HandoffArtifact.version must be '${HANDOFF_SCHEMA_VERSION}', got '${String(v.version)}'.`,
    );
  }
  for (const field of ["fromAgent", "toAgent", "taskId"] as const) {
    if (!isNonEmptyString(v[field])) {
      throw new HandoffValidationError(`${field} must be a non-empty string.`);
    }
  }

  const ctx = v.context as HandoffContext | undefined;
  if (!ctx || typeof ctx !== "object") {
    throw new HandoffValidationError("context is required.");
  }
  if (!isNonEmptyString(ctx.summary)) {
    throw new HandoffValidationError("context.summary is required.");
  }
  const summaryTokens = tokensIsh(ctx.summary);
  if (summaryTokens > HANDOFF_MAX_SUMMARY_TOKENS) {
    throw new HandoffValidationError(
      `context.summary is ~${summaryTokens} tokens (limit ${HANDOFF_MAX_SUMMARY_TOKENS}). Distil before handing off.`,
    );
  }
  for (const arr of ["keyDecisions", "openQuestions", "risks"] as const) {
    if (!Array.isArray(ctx[arr])) {
      throw new HandoffValidationError(`context.${arr} must be an array.`);
    }
  }

  const arts = v.artifacts as HandoffArtifacts | undefined;
  if (!arts || typeof arts !== "object") {
    throw new HandoffValidationError("artifacts is required.");
  }
  for (const arr of ["filePaths", "toolOutputs", "searchCacheIds"] as const) {
    if (!Array.isArray(arts[arr])) {
      throw new HandoffValidationError(`artifacts.${arr} must be an array.`);
    }
  }
  arts.toolOutputs.forEach((out, i) => {
    if (typeof out === "string" && tokensIsh(out) > HANDOFF_MAX_SUMMARY_TOKENS) {
      throw new HandoffValidationError(
        `artifacts.toolOutputs[${i}] looks like a raw text dump. Store it in the search cache and pass the id in searchCacheIds instead.`,
      );
    }
  });

  if (typeof v.verificationRequired !== "boolean") {
    throw new HandoffValidationError("verificationRequired must be a boolean.");
  }
  if (!Array.isArray(v.acceptanceCriteria)) {
    throw new HandoffValidationError("acceptanceCriteria must be an array.");
  }
  if (v.verificationRequired && (v.acceptanceCriteria as string[]).length === 0) {
    throw new HandoffValidationError(
      "verificationRequired=true but acceptanceCriteria is empty. The verifier has nothing to check against.",
    );
  }

  return v as unknown as HandoffArtifact;
}
