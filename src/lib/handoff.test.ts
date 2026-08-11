import { describe, it, expect } from "vitest";
import {
  validateHandoff,
  HandoffValidationError,
  HANDOFF_SCHEMA_VERSION,
  HANDOFF_MAX_SUMMARY_TOKENS,
  HANDOFF_TOKEN_CHAR_RATIO,
} from "./handoff";

/** Build a minimal valid artifact; tests override specific fields. */
function valid(overrides: Record<string, unknown> = {}): unknown {
  return {
    version: HANDOFF_SCHEMA_VERSION,
    fromAgent: "Engineer",
    toAgent: "Critic",
    taskId: "task-1",
    context: {
      summary: "Short summary",
      keyDecisions: ["decision A"],
      openQuestions: [],
      risks: [],
    },
    artifacts: {
      filePaths: ["src/main.py"],
      toolOutputs: [],
      searchCacheIds: [],
    },
    verificationRequired: false,
    acceptanceCriteria: [],
    ...overrides,
  };
}

describe("validateHandoff", () => {
  it("returns the artifact on valid input", () => {
    const result = validateHandoff(valid());
    expect(result).toMatchObject({
      version: HANDOFF_SCHEMA_VERSION,
      fromAgent: "Engineer",
      toAgent: "Critic",
      taskId: "task-1",
    });
  });

  it("rejects non-object input", () => {
    expect(() => validateHandoff(null)).toThrow(HandoffValidationError);
    expect(() => validateHandoff("string")).toThrow(HandoffValidationError);
    expect(() => validateHandoff(42)).toThrow(HandoffValidationError);
  });

  it("rejects wrong schema version", () => {
    expect(() => validateHandoff(valid({ version: "1.0" }))).toThrow(
      /version must be/,
    );
  });

  it.each(["fromAgent", "toAgent", "taskId"])(
    "rejects empty %s",
    (field) => {
      expect(() => validateHandoff(valid({ [field]: "" }))).toThrow(
        new RegExp(`${field} must be a non-empty string`),
      );
    },
  );

  it("rejects missing context", () => {
    expect(() => validateHandoff(valid({ context: undefined }))).toThrow(
      /context is required/,
    );
  });

  it("rejects empty summary", () => {
    expect(() =>
      validateHandoff(
        valid({
          context: {
            summary: "",
            keyDecisions: [],
            openQuestions: [],
            risks: [],
          },
        }),
      ),
    ).toThrow(/context\.summary is required/);
  });

  it("rejects summary exceeding token limit", () => {
    const longSummary = "x".repeat(HANDOFF_MAX_SUMMARY_TOKENS * HANDOFF_TOKEN_CHAR_RATIO + 100);
    expect(() =>
      validateHandoff(
        valid({
          context: {
            summary: longSummary,
            keyDecisions: [],
            openQuestions: [],
            risks: [],
          },
        }),
      ),
    ).toThrow(/context\.summary is ~\d+ tokens/);
  });

  it("rejects non-array context fields", () => {
    expect(() =>
      validateHandoff(
        valid({
          context: {
            summary: "ok",
            keyDecisions: "not an array",
            openQuestions: [],
            risks: [],
          },
        }),
      ),
    ).toThrow(/context\.keyDecisions must be an array/);
  });

  it("rejects missing artifacts", () => {
    expect(() => validateHandoff(valid({ artifacts: undefined }))).toThrow(
      /artifacts is required/,
    );
  });

  it("rejects oversized toolOutput strings", () => {
    const bigOutput = "raw text dump ".repeat(500);
    expect(() =>
      validateHandoff(
        valid({
          artifacts: {
            filePaths: [],
            toolOutputs: [bigOutput],
            searchCacheIds: [],
          },
        }),
      ),
    ).toThrow(/toolOutputs\[0\] looks like a raw text dump/);
  });

  it("rejects verificationRequired=true with empty acceptanceCriteria", () => {
    expect(() =>
      validateHandoff(
        valid({ verificationRequired: true, acceptanceCriteria: [] }),
      ),
    ).toThrow(/acceptanceCriteria is empty/);
  });

  it("allows verificationRequired=true when criteria exist", () => {
    const result = validateHandoff(
      valid({
        verificationRequired: true,
        acceptanceCriteria: ["Must compile"],
      }),
    );
    expect(result.verificationRequired).toBe(true);
    expect(result.acceptanceCriteria).toEqual(["Must compile"]);
  });

  it("rejects non-boolean verificationRequired", () => {
    expect(() =>
      validateHandoff(valid({ verificationRequired: "yes" })),
    ).toThrow(/verificationRequired must be a boolean/);
  });
});
