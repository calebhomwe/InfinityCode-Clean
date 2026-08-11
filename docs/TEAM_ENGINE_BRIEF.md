# Team Engine — Architecture Brief

_Authored by the product owner (Caleb), 2026-07-25. Canonical spec for the
multi-agent runtime the app is evolving toward. When any implementation
decision conflicts with this document, the document wins unless Caleb
explicitly overrules it._

## Part 1 — Non-Negotiables

1. **The Engine is the Product.** Team Engine (the deterministic orchestrator)
   is the core IP. Agents are replaceable containers. Never let an LLM decide
   who runs when — that produces a chatroom, not a runtime.
2. **Agents Must Not Trust Each Other.** Every handoff passes through a typed
   protocol with acceptance criteria. Schema-level rejection, not silent
   failure.
3. **Evidence Before Generation.** No generative agent uses training data for
   factual claims. Empty Evidence Dossier ⇒ the system says "I don't know" and
   halts. No hallucination.
4. **Context Is a Budget, Not a Dump.**
   - Hot (inline) ≤ 8k tokens
   - Warm (structured summaries) ≤ 500 tokens
   - Cold (raw sources) stored by ID, retrieved on demand
   - Never inline a 50-page PDF.
5. **Adversarial Verification Is Parallel, Not Linear.** A Red Team Agent
   actively tries to break the Writer's output while the Writer is still
   working. Find failure modes before the user does.
6. **Human Sovereignty With Predictive Support.** User is CEO, not
   stakeholder. Agents ask permission at checkpoints. System pre-spins
   likely next steps from user history so the user never waits.
7. **Confidence Is a Hard Gate.** Confidence < 0.7 ⇒ output blocked. No
   exceptions. Dashboard shows why + offers `[Get More Evidence]`
   `[Reduce Claim Scope]` `[Human Override]`.
8. **Documents Are Objects.** Not markdown strings. Document Object Model
   (DOM): typed nodes, dependency graphs, global citation registry. Render
   the DOM to any target format.

## Part 2 — The Kimi Standard (Feel)

> "Build the complexity of MiniMax. Ship the simplicity of Kimi."

**Never surface:**
- Agent names, status messages, handoff logs
- "Processing step 3 of 7"
- Context window warnings

**Always surface:**
- Natural conversation with a competent colleague
- Proactive suggestions ("You usually ask for slides next — shall I prepare those?")
- Transparent uncertainty ("I only found one source. Keep searching or mark tentative?")

## Part 3 — Build Brief

**Prompt prefix for any coding pass:** "Prioritize deterministic
orchestration over clever prompts. If you are about to solve a coordination
problem with an LLM, stop and build a state machine instead."

### 1. Core Philosophy

- Team Engine is deterministic code (state machine), not an LLM prompt.
- Agents are stateless, disposable containers. Payload in → execute → return.
- User experience is one natural conversation. Orchestration is invisible.

### 2. Architecture

- **Orchestrator:** Temporal.io or Windmill (durable execution, retries, observability)
- **State Machine:** XState (visualizable). States: `producing → verifying → red_team → retry → done`.
- **Agent Runtime:** Docker containers per agent. Each has its own model, toolset, permission boundary.
- **Memory:**
  - Run Memory: SQLite + full-text search
  - Semantic Memory: Vector DB for skill extraction + intent modelling
- **Handoff Bus:** Redis Streams or NATS (pub/sub, not file drops)

### 3. Handoff Protocol (non-negotiable schema)

```typescript
interface HandoffArtifact {
  version: "2.0";
  fromAgent: string;
  toAgent: string;
  taskId: string;
  context: {
    summary: string;            // ≤ 500 tokens
    keyDecisions: string[];
    openQuestions: string[];
    risks: string[];
  };
  artifacts: {
    filePaths: string[];
    toolOutputs: any[];
    searchCacheIds: string[];  // references only, no full text
  };
  verificationRequired: boolean;
  acceptanceCriteria: string[];
}
```

No agent may receive raw source text inline — summaries and retrievable IDs only.

### 4. Context Management (Temperature Layers)

- **Hot:** current task + last 3 user messages. Inlined full text. 8k tokens.
- **Warm:** session decisions, risks, open questions. Structured JSON. 500 tokens.
- **Cold:** raw research, prior runs, tool outputs. Stored by ID.
- Overflow ⇒ Context Distillation Agent preserves decisions + open questions only.

### 5. Verification & Adversarial Loop

- **Verifier Agent:** linear pass/fail against acceptance criteria.
- **Red Team Agent:** parallel or immediately after Verifier. Generates
  counterexamples, stress tests, edge cases. Tagged failures:
  `HALLUCINATION_CITATION`, `LOGIC_GAP`, `SECURITY_RISK`.
- Red Team failure ⇒ loop back to Worker with specific tags. Not blind retry.

### 6. Evidence Lock

Every factual claim needs a dossier entry:

```json
{
  "claim": "string",
  "source": "stable_url",
  "source_type": "peer_reviewed | official_doc | aggregator | search_cache",
  "freshness": "YYYY-MM-DD",
  "confidence": 0.0-1.0,
  "counter_evidence": "string | null"
}
```

Empty dossier or confidence < 0.7 ⇒ output must read: "Insufficient evidence for this claim."

### 7. Document Object Model

- Documents are graphs of typed nodes: `section | table | chart | paragraph | citation_block`
- Each node: `id`, `content`, `style_rules`, `dependencies[]`, `owner_agent`, `verification_status`
- Global `citation_registry` for dedup + consistency
- Render targets: Markdown, HTML, PDF, PPTX

### 8. Human-in-the-Loop Checkpoints

- `code_merge_to_main` ⇒ human approval unless all tests + security pass
- `research_topic_pivot` ⇒ notify and wait
- `document_section_complete` ⇒ auto-continue unless confidence < 0.85
- User feels like delegating, not being managed.

### 9. Predictive Pre-Spin

- Lightweight User Intent Model (learned from history, not asked)
- Pre-warm likely next agents based on task type + user patterns
- E.g., after research ⇒ pre-spin Formatter + Security Reviewer in standby

### 10. Cost & Quality Dashboard (live)

- Tokens used / budget
- Active agents (with kill switch)
- Current stage + estimated time remaining
- Confidence score of latest output
- Time saved vs single-agent execution
- Auto-pause triggers: token limit, confidence threshold, red team failure count

### 11. Memory System

- **Run Memory:** every run produces a `RunReport` indexed by task type,
  failure mode, tools used, time taken. Prepended to similar future tasks.
- **Skill Memory:** after 3+ successful runs of a task type, Skill Extraction
  Agent distils reusable `Skill` objects (optimal tool sequence, common
  pitfalls, token estimates).

### 12. UX Constraints (Kimi Standard)

- No agent names, status updates, handoff logs surfaced to the user
- Never "I am processing step 3 of 7"
- Always ask, never announce: "I see 5 workstreams. Start all 5, or review the plan first?"
- Explicit graceful uncertainty: "Sources conflict — present both sides?"

### 13. Tech Stack

| Concern | Choice |
|---|---|
| Orchestration | Temporal.io |
| State Machine | XState |
| Agent Runtime | Docker |
| Structured memory | SQLite |
| Semantic memory | Pinecone / Weaviate |
| Handoff Bus | NATS |
| Frontend | Web-first, Server-Sent Events for live updates |
| Protocol | Open-source the handoff schema (Apache 2.0) |

### 14. What NOT to Build

- Agents chatting freely with each other
- LLM as the orchestrator
- Documents as markdown strings
- Verification only after generation is "complete"
- Factual claims without Evidence Dossier backing
