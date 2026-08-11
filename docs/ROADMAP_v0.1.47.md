# Infinity Code — "Amp it up" roadmap (K3 recon swarm, 2026-07-18)

Source: 9-agent recon swarm (Cursor/OpenRouter/OpenCode/Continue/Cline research +
harsh internal audit of frontend/backend/perf). 745K tokens, 8 sources.

## Where it shines
- Chat mode streams tokens live, feels instant (nativeFeel + skeleton shimmer).
- Real swarm-OS bones: WS streams status/agents/cost/screenshot; FastAPI engine; 245-agent roster; RAG-over-vaults.
- Creative-tooling moat (Blender/Unreal/ComfyUI/fal.ai via MCP) — no coding-agent competitor has this.
- Considered design system (desaturated status tokens, one-accent, motion tokens).
- Cost/evidence/tournament/red-team/quality-gate modules already exist as code.

## Where it does NOT shine (fix these)
1. **Build view stutters** — everything on a 5s poll while the live WS frame is thrown away. Flagship surface feels laggier than the Chat sidebar. ← #1
2. **Swarm is largely theater** — `ModelRouter.route()` is dead code; predictive routing is a coin-flip; all 6 roles resolve to the same 1-2 models. No decomposition/delegation.
3. **Success is unmeasured** — with no reference image, "pass" = `subprocess exit 0`. Nothing checks it did the right thing.
4. **Unsandboxed code exec** — LLM code (incl. red-team scripts) runs on host w/ full privileges.
5. **"Self-learning" is a read-only dashboard** — swarm never actually gets better; every mission starts cold.
6. **~3.8MB media blocks first paint** (2.3MB logo @112px + 1.46MB splash) + 481kB un-split bundle.
7. **Amateur tells** — `window.prompt()` for reject, raw enum labels, two green/red systems, disabled mic button.
8. **3 divergent model-ID tables, no validation**; no fallback chain in mission path; no concurrency cap.

## Execution order (this build = the S-effort high-impact "turbo out the box" set)
- [x] Sounds + desktop notifications (lib/feedback.ts) — DONE
- [x] Composer-style TurboActivity dropdown (components/TurboActivity.tsx) — DONE (v1)
- [ ] **S** Drive Build view off WebSocket; gate `useMissions`/`useCost` polling by view + WS state
- [ ] **S** Render the live mission screenshot already on the wire
- [ ] **S** Compress startup media (logo→WebP) + code-split heavy components + manualChunks
- [ ] **S** iPhone/LAN: `vite --host` + derive API/WS base from `window.location.hostname` (localhost hardcode is the gotcha)
- [ ] **S** VS Code: .vscode launch/tasks/extensions
- [ ] **S** Native-feel polish: inline reject textarea, humanize enum labels, hide disabled mic

## Next big push (M/L — "swarms that hit hard and know what they're doing")
- **M** Per-agent model routing + auto-tier + fallback chains + single model-ID source of truth (revive ModelRouter)
- **M** Real acceptance-check quality gate (goal-derived assertions, not exit-code)
- **M** Compose-your-crew: choose + combine + pin agents (versioned presets)
- **L** Continuous self-training: index successful attempts + failure→fix pairs, retrieve into engineer prompt; execute-and-verify tutorial ingest
- **L** Sandbox all LLM-generated code execution (WSL2/AppContainer jail)
- **M** Real adversarial critic + judge panel + genuine tournament diversity
- **M** Bounded work queue + concurrency cap + planner-writes-a-DAG orchestration
