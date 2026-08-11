"""Self-training loop — the app teaches itself, on a schedule.

Pipeline per topic:
  1. LIVE   — Perplexity Sonar (via OpenRouter) pulls current, cited facts off
              the real web, so the knowledge is genuinely up to date.
  2. DISTIL — Kimi K3 turns that raw brief into a durable "knowledge card":
              dated facts, concrete techniques, links. No fluff, no speculation.
  3. INDEX  — the card is written to <DATA_DIR>/learned/<topic>/ and folded into
              the KnowledgeStore, so chat/missions retrieve it via RAG.

This is deliberately a REAL loop (fetch -> distil -> index -> retrieved later),
not a dashboard: after a cycle the assistant genuinely knows things it did not
know before. Runs on demand or nightly via the scheduler.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("infinity.self_training")

# General-purpose live-web model (cited, current) and the distiller. Benchmark
# drills do *not* use these paid defaults: they run on ``benchmark_model``.
LIVE_MODEL = "perplexity/sonar"
DISTIL_MODEL = "moonshotai/kimi-k3"
DISTIL_FALLBACK = "moonshotai/kimi-k2.6"


class Topic:
    __slots__ = ("key", "label", "query", "lens")

    def __init__(self, key: str, label: str, query: str, lens: str) -> None:
        self.key = key
        self.label = label
        self.query = query
        self.lens = lens


# Curated curriculum. Each cycle refreshes these; add more via add_topic().
DEFAULT_TOPICS: List[Topic] = [
    Topic(
        "ai-news",
        "AI & LLM news",
        "What are the most important AI and LLM developments, model releases, "
        "and tooling changes from the past week? Include dates and sources.",
        "New models/tools, what changed, and what it means for someone building "
        "AI-powered desktop apps and agent swarms.",
    ),
    Topic(
        "automation",
        "Automation & agents",
        "What are the newest practical automation and AI-agent techniques, "
        "frameworks, and workflow patterns developers are adopting right now?",
        "Concrete automation patterns, orchestration techniques, and things that "
        "can be wired into an agent-swarm app.",
    ),
    Topic(
        "web-dev",
        "Web & app development",
        "What are the current best practices, notable releases, and performance "
        "techniques in React, TypeScript, Tauri and desktop web app development?",
        "Actionable techniques: performance, architecture, APIs, gotchas.",
    ),
    Topic(
        "creative-tools",
        "Blender / Unreal / 3D",
        "What are the latest Blender, Unreal Engine, and 3D/VFX production "
        "techniques, releases, and workflow tips?",
        "Production techniques a technical artist would actually use: pipelines, "
        "rendering, rigging, scripting.",
    ),
    Topic(
        "spatial-reasoning",
        "Spatial reasoning for 3D agents",
        "What are the most reliable, practical methods for 3D coordinate reasoning, transforms, vector math, ray casting, collision checks and spatial verification in game tools?",
        "Executable spatial reasoning: declare coordinate frames and units, use transforms explicitly, then validate with measurements, ray casts or collision checks.",
    ),
    Topic(
        "game-code",
        "Game code & technical design",
        "What are current robust patterns for 3D game movement, collision, state machines, ECS, A-star pathfinding, Godot, Unity and Unreal gameplay systems?",
        "Small engine-appropriate implementations with invariants, tests, edge cases and debugging guidance rather than generic pseudocode.",
    ),
    Topic(
        "agentic-evals",
        "Agent harnesses & evaluation",
        "What are current practical methods for evaluating and improving AI agents with plan-act-verify-recover loops, sandboxed tool use, regression suites and cost-aware routing?",
        "Evidence-driven agent improvement: benchmark failures become scoped drills, retrieval updates, tool guidance and repeatable regressions.",
    ),
    Topic(
        "design",
        "UI/UX & product design",
        "What are the current UI/UX design patterns, interaction trends, and "
        "product-polish techniques that top desktop and AI apps are using?",
        "Design patterns and polish techniques that make an app feel premium.",
    ),
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "topic"


class SelfTrainer:
    """Fetches, distils and indexes fresh knowledge into the KnowledgeStore."""

    def __init__(
        self,
        knowledge: Any,
        client: Any,
        data_dir: Path,
        topics: Optional[List[Topic]] = None,
        direct: Any = None,
        benchmark_model: str = "local/fable-fast",
    ) -> None:
        self.knowledge = knowledge
        self.client = client
        # Optional MoonshotClient: when the account is funded, K3 distillation
        # goes straight to api.moonshot.ai (no router hop). Falls back to the
        # OpenRouter client automatically on any failure.
        self.direct = direct
        self.learned_dir = Path(data_dir) / "learned"
        self.learned_dir.mkdir(parents=True, exist_ok=True)
        self.topics = topics or list(DEFAULT_TOPICS)
        self.last_run: Optional[Dict[str, Any]] = None
        self.last_distiller: str = ""
        # Personal topics live in a JSON file the user edits from Settings.
        # Read on every cycle so an edit takes effect on the next run without
        # a backend restart.
        self.personal_topics_path: Path = Path(data_dir) / "personal_topics.json"
        self.benchmark_curriculum_path: Path = Path(data_dir) / "benchmark_curriculum.json"
        # Benchmark remediation must be cheap by default. This is intentionally
        # a local lane, so it is zero-cost when the configured local server is
        # running. A user may opt into another model via config/environment.
        self.benchmark_model = benchmark_model.strip() or "local/fable-fast"

    def _benchmark_topics(self) -> List["Topic"]:
        """Turn unresolved eval failures into deduplicated research topics.

        This lets the lowest-cost capable model do research/distillation work
        after the benchmark identifies a weakness.  A later passing eval marks
        its task mastered, so resolved drills stop consuming learning cycles.
        """
        try:
            raw = json.loads(self.benchmark_curriculum_path.read_text(encoding="utf-8"))
            drills = raw.get("drills", {}) if isinstance(raw, dict) else {}
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot load benchmark drills: %s", exc)
            return []
        if not isinstance(drills, dict):
            return []
        out: List[Topic] = []
        seen = {topic.key for topic in self.topics}
        for item in drills.values():
            if not isinstance(item, dict) or item.get("status") != "queued":
                continue
            capability = _slug(str(item.get("capability") or "general"))
            key = f"benchmark-{capability}"
            if key in seen:
                continue
            seen.add(key)
            out.append(Topic(
                key=key,
                label=f"Benchmark drill: {item.get('label') or capability}",
                query=str(item.get("query") or f"Practical techniques for {capability}."),
                lens=str(item.get("lens") or "Concrete, testable improvements for the agent."),
            ))
        return out

    def _personal_topics(self) -> List["Topic"]:
        """User-added topics: '<label> — <lens>' lines saved from Settings.

        Non-fatal on read/format errors; the built-in curriculum still runs.
        """
        try:
            if not self.personal_topics_path.is_file():
                return []
            raw = json.loads(self.personal_topics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot load personal topics: %s", exc)
            return []
        items = raw.get("topics") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        out: List[Topic] = []
        seen_keys: set[str] = {t.key for t in self.topics}
        for item in items:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("query") or "").strip()
                query = str(item.get("query") or label).strip()
                lens = str(item.get("lens") or "").strip() or (
                    "Concrete, actionable insights for the user's own work."
                )
            elif isinstance(item, str):
                label = item.strip()
                query = f"What are the newest developments and best practices in: {label}?"
                lens = "Concrete, actionable insights for the user's own work."
            else:
                continue
            if not label:
                continue
            key = _slug(label)
            if not key or key in seen_keys:
                continue  # dedupe against built-ins + previous personal entries
            seen_keys.add(key)
            out.append(Topic(key=key, label=label, query=query, lens=lens))
        return out

    def add_topic(
        self, key: str, label: str, query: str, lens: str
    ) -> None:
        """Add a topic to the in-memory curriculum for this process."""
        if any(t.key == key for t in self.topics):
            return
        self.topics.append(Topic(key=key, label=label, query=query, lens=lens))

    def get_personal_topics(self) -> List[Dict[str, str]]:
        """Return the persisted personal topics as plain dicts (for the API)."""
        try:
            if not self.personal_topics_path.is_file():
                return []
            raw = json.loads(self.personal_topics_path.read_text(encoding="utf-8"))
            items = raw.get("topics") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                return []
            out: List[Dict[str, str]] = []
            for item in items:
                if isinstance(item, dict):
                    out.append(
                        {
                            "label": str(item.get("label") or "").strip(),
                            "query": str(item.get("query") or "").strip(),
                            "lens": str(item.get("lens") or "").strip(),
                        }
                    )
                elif isinstance(item, str) and item.strip():
                    out.append({"label": item.strip(), "query": "", "lens": ""})
            return [t for t in out if t["label"]]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("get_personal_topics failed: %s", exc)
            return []

    def set_personal_topics(self, topics: List[Any]) -> List[Dict[str, str]]:
        """Persist the user's personal topics. Atomic write via temp+replace."""
        cleaned: List[Dict[str, str]] = []
        for item in topics or []:
            if isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                query = str(item.get("query") or "").strip()
                lens = str(item.get("lens") or "").strip()
            elif isinstance(item, str):
                label, query, lens = item.strip(), "", ""
            else:
                continue
            if not label:
                continue
            cleaned.append({"label": label, "query": query, "lens": lens})
        try:
            self.personal_topics_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.personal_topics_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"topics": cleaned}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            import os as _os
            _os.replace(tmp, self.personal_topics_path)
        except OSError as exc:
            logger.error("Cannot save personal topics: %s", exc)
        return cleaned

    # ---------------- internals ---------------- #

    def _chat(self, model: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        result = self.client.chat(model, messages, max_tokens=max_tokens)
        text = result["text"] if isinstance(result, dict) else getattr(result, "text", "")
        return (text or "").strip()

    def _live_brief(self, topic: Topic) -> str:
        """Current, cited facts from the live web."""
        return self._chat(
            LIVE_MODEL,
            [
                {
                    "role": "system",
                    "content": "Answer with current facts. Include dates and cite "
                    "sources inline as URLs. Be specific and concrete.",
                },
                {"role": "user", "content": topic.query},
            ],
            max_tokens=1200,
        )

    def _benchmark_brief(self, topic: Topic) -> str:
        """Make a practice brief from the failed harness evidence, locally.

        It avoids calling a paid web-search model. The resulting card teaches
        execution strategy, checks, and failure recovery; it is later validated
        by rerunning the originating benchmark rather than trusted on prose.
        """
        return self._chat(
            self.benchmark_model,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a low-cost agent coach. Build a concise practice brief "
                        "for a benchmark weakness. Give an executable checklist, likely "
                        "failure modes, and how to verify success. Do not claim to browse "
                        "or cite sources you did not receive."
                    ),
                },
                {"role": "user", "content": f"WEAKNESS: {topic.label}\n{topic.query}\n\n{topic.lens}"},
            ],
            max_tokens=1200,
        )

    def _distil(
        self,
        topic: Topic,
        brief: str,
        today: str,
        model: Optional[str] = None,
    ) -> str:
        """Turn a brief into a durable, retrievable knowledge card."""
        system = (
            "You distil live web research into a durable knowledge card that will "
            "be embedded and retrieved later by an AI assistant.\n"
            "Rules:\n"
            "- Only keep concrete, verifiable facts, techniques and links.\n"
            "- Every claim keeps its date and source URL where available.\n"
            "- Drop hype, speculation and filler. If something is uncertain, say so.\n"
            "- Write in tight markdown: '## <heading>' sections with short bullets.\n"
            "- Optimise for a future reader asking a practical question."
        )
        user = (
            f"TOPIC: {topic.label}\n"
            f"LENS: {topic.lens}\n"
            f"DATE: {today}\n\n"
            f"RAW LIVE RESEARCH:\n{brief}\n\n"
            "Produce the knowledge card now."
        )
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]

        # Benchmark drills remain on the local/cheap coach; never silently
        # escalate them to K3 simply because the local lane is unavailable.
        if model is not None:
            text = self._chat(model, msgs, max_tokens=1800)
            self.last_distiller = f"benchmark:{model}"
            return text

        # 1. K3 straight from Moonshot (preferred — no router hop, full context).
        if self.direct is not None:
            try:
                result = self.direct.chat("kimi-k3", msgs, max_tokens=2400)
                text = (result.get("text") or "").strip()
                if text:
                    self.last_distiller = "moonshot-direct:kimi-k3"
                    return text
                raise RuntimeError("empty response")
            except Exception as exc:  # noqa: BLE001 - e.g. 429 unfunded account
                logger.warning("Moonshot direct K3 failed (%s); using OpenRouter.", str(exc)[:160])

        # 2. K3 via OpenRouter.
        try:
            text = self._chat(DISTIL_MODEL, msgs, max_tokens=2400)
            self.last_distiller = f"openrouter:{DISTIL_MODEL}"
            return text
        except Exception as exc:  # noqa: BLE001 - fall back rather than lose the cycle
            logger.warning("K3 distil failed (%s); falling back to %s", exc, DISTIL_FALLBACK)
            text = self._chat(DISTIL_FALLBACK, msgs, max_tokens=2400)
            self.last_distiller = f"openrouter:{DISTIL_FALLBACK}"
            return text

    # ---------------- public ---------------- #

    def run_cycle(
        self,
        topic_keys: Optional[List[str]] = None,
        embed: Optional[Callable[[List[str]], List[List[float]]]] = None,
    ) -> Dict[str, Any]:
        """Refresh one knowledge card per topic, then re-index. Never raises:
        a failing topic is recorded and the rest of the cycle continues."""
        started = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Personal topics get merged fresh each cycle so edits from Settings
        # take effect on the next run without a backend restart.
        curriculum: List[Topic] = list(self.topics) + self._personal_topics() + self._benchmark_topics()
        chosen = [t for t in curriculum if not topic_keys or t.key in topic_keys]
        written: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []

        for topic in chosen:
            try:
                is_benchmark_drill = topic.key.startswith("benchmark-")
                brief = self._benchmark_brief(topic) if is_benchmark_drill else self._live_brief(topic)
                if not brief:
                    raise RuntimeError("live search returned nothing")
                card = self._distil(
                    topic,
                    brief,
                    today,
                    model=self.benchmark_model if is_benchmark_drill else None,
                )
                if not card:
                    raise RuntimeError("distiller returned nothing")

                topic_dir = self.learned_dir / _slug(topic.key)
                topic_dir.mkdir(parents=True, exist_ok=True)
                path = topic_dir / f"{today}-{_slug(topic.key)}.md"
                path.write_text(
                    f"# {topic.label} — {today}\n\n"
                    f"_Auto-learned by Infinity Code. Lens: {topic.lens}_\n\n{card}\n",
                    encoding="utf-8",
                )
                written.append(
                    {"topic": topic.key, "label": topic.label,
                     "path": str(path), "chars": len(card)}
                )
                logger.info("Learned %s (%d chars) -> %s", topic.key, len(card), path.name)
            except Exception as exc:  # noqa: BLE001 - one bad topic must not kill the cycle
                logger.error("Self-training topic %s failed: %s", topic.key, exc)
                errors.append({"topic": topic.key, "error": str(exc)[:300]})

        # Fold the learned corpus into the knowledge base so RAG can retrieve it.
        indexed: Dict[str, Any] = {}
        if written:
            try:
                self.knowledge.add_source(str(self.learned_dir), kind="learned")
                if embed is not None:
                    indexed = self.knowledge.reindex(embed)
            except Exception as exc:  # noqa: BLE001
                logger.error("Indexing learned corpus failed: %s", exc)
                errors.append({"topic": "_index", "error": str(exc)[:300]})

        self.last_run = {
            "at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(time.time() - started, 1),
            "learned": written,
            "indexed": indexed,
            "errors": errors,
            "distiller": self.last_distiller,
        }
        return self.last_run

    def status(self) -> Dict[str, Any]:
        cards = sorted(self.learned_dir.rglob("*.md"))
        return {
            "topics": [{"key": t.key, "label": t.label} for t in self.topics],
            "cards": len(cards),
            "learned_dir": str(self.learned_dir),
            "last_run": self.last_run,
            "recent": [c.name for c in cards[-8:]],
            "direct_k3": self.direct is not None,
            "last_distiller": self.last_distiller,
            "benchmark_model": self.benchmark_model,
        }
