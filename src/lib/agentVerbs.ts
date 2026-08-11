// Shared vocabulary for narrating what a council role is doing.
//
// Lives here rather than inside TurboActivity because the mission list needs
// the same words: a card that says "Engineer writing code" and a panel that
// says something different about the same moment reads like two apps.

export const AGENT_VERB: Record<string, string> = {
  Director: "planning the mission",
  Planner: "planning",
  Architect: "designing the approach",
  Engineer: "writing code",
  "Engineer (local)": "writing code locally",
  Artist: "generating imagery",
  Critic: "reviewing the work",
  Inspector: "checking the result",
  RedTeam: "stress-testing",
  Redteam: "stress-testing",
  Scribe: "recording evidence",
  Tester: "running the code",
  Eye: "inspecting the result",
  Debugger: "hunting bugs",
  Worker: "working",
  Creative: "exploring ideas",
};

/** A short, human verb for a role. Unknown roles fall back to "working". */
export function verbFor(agent: string | null | undefined): string {
  if (!agent) {
    return "working";
  }
  return AGENT_VERB[agent] ?? "working";
}

/** Compact form for tight spaces — the first word of the verb. */
export function shortVerbFor(agent: string | null | undefined): string {
  return verbFor(agent).split(" ")[0];
}
