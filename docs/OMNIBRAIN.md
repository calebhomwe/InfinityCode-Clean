# OmniBrain grounding

Infinity Code grounds chat answers in **OmniBrain** — Caleb's unified vector
brain over his own Claude memories, the Obsidian GameDev-Vault, every project's
docs, and his agent/skill definitions (~4k curated personal vectors, 53.6k
total). It runs alongside the local `knowledge.db` RAG, not instead of it.

## Why it is a remote call, not a local index

The two systems embed with **different models**, so their vectors are not
comparable and the indexes cannot be merged:

| | model | dim |
|---|---|---|
| Infinity Code `knowledge.db` | `openai/text-embedding-3-small` (OpenRouter) | 1536 |
| OmniBrain | `nomic-embed-text-v1.5` | 768 |

Running nomic locally would mean shipping `torch` + `sentence-transformers` in
the PyInstaller freeze — roughly 2GB on top of an 88MB exe. So OmniBrain does
its own embedding server-side and we send raw text.

**That is also why it is nearly free inside the grounding budget.** The chat
path allows 2.5s for *all* grounding, and `client.embed()` dominates it.
OmniBrain needs no local embed, so it is dispatched on its own thread *before*
the local embed and joined afterwards — it overlaps rather than adds. Measured
round trip: ~1.0s, fully inside the window.

## Wiring

| Piece | Where |
|---|---|
| Client | `backend/core/omnibrain.py` — `OmniBrainClient` |
| Grounding | `backend/main.py` → `_grounding()`; dispatch before `client.embed()`, join via `_collect_omnibrain()` |
| Startup | `application.state.omnibrain = OmniBrainClient()` |
| Settings | `omnibrain_enabled` (default **true**), `omnibrain_url` |
| Endpoints | `GET /api/v1/omnibrain/status`, `POST /api/v1/omnibrain/search` |
| SSE | done frame carries `"omnibrain": N` alongside `knowledge` / `recalled` |
| UI | `ChatView.tsx` — "brain N" chip next to "recalled N" |

## The noise floor

`MIN_SCORE = 0.675`. Measured over 10 known-answer questions vs 8 deliberately
off-topic ones:

- genuine hits: **0.692 – 0.814**
- off-topic noise: **0.594 – 0.661** ("best recipe for sourdough bread" scores
  0.661 against a game-dev corpus — embeddings always return *something*)

Without the floor an unrelated question yields confident-looking junk. With it,
off-topic queries return zero hits and the model answers from general knowledge.

## Does it actually help?

Measured 2026-07-28. Same model (DeepSeek V3.1), same sampling, raw API, blind
independent judging; the only variable was whether OmniBrain context was
prepended:

**baseline 1/10 correct → with OmniBrain 10/10. Lift on 9 cases, harm on 0.**

Without it, models confidently gave the *opposite* of the working answer —
"update your NVIDIA drivers" (real cause: corrupted DerivedDataCache), "reduce
temperature to 0.1-0.5" (Kimi K3 requires exactly 1.0). Harness lives at
`D:\OmniBrain\Tools\ab_clean.py`.

## Failure behaviour

Fails soft, always — a brain outage must never cost a reply.

- request timeout 1.8s, joined with a 2.0s cap; overrun is logged and skipped
- any network/parse error returns `[]`
- if the **local** embed fails, OmniBrain still contributes (it embeds remotely),
  so grounding degrades instead of disappearing
- 5-min response cache, 128 entries, to keep repeated turns off the wire

## Ops

Health: `curl http://127.0.0.1:8000/api/v1/omnibrain/status`

The brain is a **snapshot**. After adding significant memories or vault notes,
refresh it — see `D:\OmniBrain\SYSTEMS.md` ("Refresh cycle"), and never skip the
dedup step. Turn the whole feature off with `omnibrain_enabled: false`.
