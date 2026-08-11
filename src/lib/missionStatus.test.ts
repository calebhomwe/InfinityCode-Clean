import { describe, it, expect } from "vitest";
import {
  titleCaseEnum,
  missionStatusLabel,
  missionModeLabel,
  MISSION_STATUS_LABELS,
} from "./missionStatus";

describe("titleCaseEnum", () => {
  it("converts snake_case to Title Case", () => {
    expect(titleCaseEnum("awaiting_review")).toBe("Awaiting Review");
  });

  it("converts hyphen-case to Title Case", () => {
    expect(titleCaseEnum("in-progress")).toBe("In Progress");
  });

  it("converts space-separated words", () => {
    expect(titleCaseEnum("ready for review")).toBe("Ready For Review");
  });

  it("handles single word", () => {
    expect(titleCaseEnum("queued")).toBe("Queued");
  });

  it("handles ALL_CAPS", () => {
    expect(titleCaseEnum("SOME_STATUS")).toBe("Some Status");
  });

  it("collapses multiple separators", () => {
    expect(titleCaseEnum("a__b--c  d")).toBe("A B C D");
  });

  it("handles empty string", () => {
    expect(titleCaseEnum("")).toBe("");
  });
});

describe("missionStatusLabel", () => {
  it("returns known labels for all defined statuses", () => {
    for (const [key, label] of Object.entries(MISSION_STATUS_LABELS)) {
      expect(missionStatusLabel(key)).toBe(label);
    }
  });

  it('maps "completed" to "Ready for review"', () => {
    expect(missionStatusLabel("completed")).toBe("Ready for review");
  });

  it('maps "cancelled" and "canceled" both to "Cancelled"', () => {
    expect(missionStatusLabel("cancelled")).toBe("Cancelled");
    expect(missionStatusLabel("canceled")).toBe("Cancelled");
  });

  it("falls back to titleCaseEnum for unknown statuses", () => {
    expect(missionStatusLabel("some_new_state")).toBe("Some New State");
  });
});

describe("missionModeLabel", () => {
  it("returns labels for all known modes", () => {
    expect(missionModeLabel("auto")).toBe("Auto");
    expect(missionModeLabel("code")).toBe("Code");
    expect(missionModeLabel("image")).toBe("Image");
    expect(missionModeLabel("video")).toBe("Video");
    expect(missionModeLabel("3d")).toBe("3D");
  });

  it("falls back to titleCaseEnum for unknown modes", () => {
    expect(missionModeLabel("new_mode")).toBe("New Mode");
  });
});
