import { describe, expect, it } from "vitest";
import { buildGreeting, buildMissionGreeting, getDaypart } from "../greetings";
import type { PersonalitySettings } from "../types";

const base: PersonalitySettings = { enabled: true, name: "Infinity", tone: "warm", userName: "", emoji: false };

describe("getDaypart", () => {
  it("maps morning hours", () => {
    expect(getDaypart(5)).toBe("morning");
    expect(getDaypart(11)).toBe("morning");
  });
  it("maps afternoon hours", () => {
    expect(getDaypart(12)).toBe("afternoon");
    expect(getDaypart(17)).toBe("afternoon");
  });
  it("maps evening hours", () => {
    expect(getDaypart(18)).toBe("evening");
    expect(getDaypart(21)).toBe("evening");
  });
  it("maps night hours including wrap", () => {
    expect(getDaypart(22)).toBe("night");
    expect(getDaypart(0)).toBe("night");
    expect(getDaypart(4)).toBe("night");
  });
});

describe("buildGreeting", () => {
  it("uses warm tone and appends user name", () => {
    const s = { ...base, userName: "Caleb" };
    expect(buildGreeting("morning", s)).toBe("Good morning, Caleb \u2014 what can I help you with?");
  });
  it("omits name when empty", () => {
    expect(buildGreeting("morning", base)).toBe("Good morning \u2014 what can I help you with?");
  });
  it("never contains emoji", () => {
    for (const dp of ["morning", "afternoon", "evening", "night"] as const) {
      for (const tone of ["warm", "sharp", "minimal", "energetic"] as const) {
        const out = buildGreeting(dp, { ...base, tone });
        expect(out).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u);
      }
    }
  });
});

describe("buildMissionGreeting", () => {
  it("ends with the build prompt", () => {
    expect(buildMissionGreeting("afternoon", base)).toBe("Good afternoon \u2014 what should we build?");
  });
});
