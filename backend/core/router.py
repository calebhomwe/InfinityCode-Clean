"""Model council routing for Infinity Code.

Maps a task's shape (type, complexity, vision needs) to the right council
member, and converts token usage into AUD cost.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel
try:
    from backend.core.model_catalog import EXTRA_CHAT_MODELS, QWEN_CHAT_MODELS
except ImportError:  # running with backend/ as the working directory
    from core.model_catalog import EXTRA_CHAT_MODELS, QWEN_CHAT_MODELS  # type: ignore[no-redef]



logger = logging.getLogger(__name__)

USD_PER_AUD: float = 0.67  # 1 AUD buys 0.67 USD → AUD = USD / 0.67

Complexity = Literal["simple", "complex", "architectural"]

# Task-shape lanes from the custom model strategy. Each lane is a degradation
# chain: primary first, then fallbacks if the primary fails verification or
# budget approval.
LANE_CHEAP: str = "cheap"
LANE_SMART: str = "smart"
LANE_VISION: str = "vision"
LANE_LOCAL: str = "local"
LANE_CUSTOM: str = "custom"
VALID_LANES: Tuple[str, ...] = (LANE_CHEAP, LANE_SMART, LANE_VISION, LANE_LOCAL, LANE_CUSTOM)

CREATIVE_TASK_TYPES: Tuple[str, ...] = ("creative", "story", "dialogue", "marketing")
VISION_CRITIQUE_TASK_TYPES: Tuple[str, ...] = (
    "visual critique",
    "visual_critique",
    "critique",
    "compare",
)
IMAGE_GEN_TASK_TYPES: Tuple[str, ...] = ("image_gen", "image_generation")

# Tasks that should start in the smart lane even at "complex" complexity.
SMART_TASK_TYPES: Tuple[str, ...] = (
    "debug",
    "refactor",
    "system_design",
    "architecture",
    "hard",
    "plan",
    "critique",
    "review",
)

# Ascension Engine form policy: which lanes each form may use. The approved
# MODEL list lives in core/ascension.py (FORM_MODELS); this table is the lane
# half of the same lock. Unknown form names fall back to X Code.
ASCENSION_POLICY: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "X Code":     {"lanes": (LANE_CHEAP,)},
    "SS1":        {"lanes": (LANE_CHEAP,)},
    "SS2":        {"lanes": (LANE_CHEAP, LANE_SMART)},
    "SS3":        {"lanes": (LANE_CHEAP, LANE_SMART, LANE_VISION)},
    "Blue":       {"lanes": (LANE_CHEAP, LANE_SMART, LANE_VISION, LANE_CUSTOM)},
    "Mr X Final": {"lanes": (LANE_CHEAP, LANE_SMART, LANE_VISION, LANE_CUSTOM, LANE_LOCAL)},
}


def apply_ascension(
    state_name: str,
    lane: str,
    chain: Tuple[ModelSpec, ...],
    approved: Optional[Tuple[str, ...]] = None,
    available: Optional[Callable[[str], bool]] = None,
) -> Tuple[str, Tuple[ModelSpec, ...]]:
    """Constrain a lane chain to the current ascension form.

    Form lock: only models in `approved` may run (pass the engine's
    approved_models()). Models whose key is missing (available=False) are
    skipped. Qwen 3.8 Max is always excluded from lane chains — it is the
    oracle and never drafts (role lock). A lane outside the form's policy is
    demoted to the cheapest allowed lane. If nothing survives, the default
    coder chain (DeepSeek Flash 1731) is returned, filtered the same way, so
    routing always lands on a legal model.
    """
    policy = ASCENSION_POLICY.get(state_name, ASCENSION_POLICY["X Code"])
    allowed_lanes: Tuple[str, ...] = policy["lanes"]
    if lane not in allowed_lanes:
        lane = LANE_CHEAP if LANE_CHEAP in allowed_lanes else allowed_lanes[0]
    out: List[ModelSpec] = []
    for spec in chain:
        if spec.id == "dashscope/qwen3.8-max":
            continue  # role lock: the oracle never drafts
        if approved is not None and spec.id not in approved:
            continue  # form lock
        if available is not None and not available(spec.id):
            continue  # missing key -> locked
        out.append(spec)
    if not out:
        out = _default_coder_chain(approved, available)
    return lane, tuple(out)


def _default_coder_chain(
    approved: Optional[Tuple[str, ...]],
    available: Optional[Callable[[str], bool]],
) -> List[ModelSpec]:
    """DeepSeek Flash 1731 (and fallbacks) filtered by the form's locks;
    if the form approves nothing else, the cheapest approved spec wins."""
    primary = COUNCIL["longtask_builder"]
    chain: List[ModelSpec] = [primary]
    for fid in primary.fallbacks:
        spec = MODEL_SPECS.get(fid)
        if spec is not None:
            chain.append(spec)
    out: List[ModelSpec] = []
    for spec in chain:
        if approved is not None and spec.id not in approved:
            continue
        if available is not None and not available(spec.id):
            continue
        out.append(spec)
    if not out and approved:
        for mid in approved:
            if mid == "dashscope/qwen3.8-max":
                continue  # role lock survives even the final fallback
            spec = MODEL_SPECS.get(mid)
            if spec is not None and (available is None or available(mid)):
                out = [spec]
                break
    return out or [primary]


class ModelSpec(BaseModel):
    id: str
    cost_in_per_million: float
    cost_out_per_million: float
    context_window: int
    supports_vision: bool = False
    supports_image_gen: bool = False
    # Declarative degradation path: tried in order when the primary errors
    # (bad slug, 429, provider outage) — not just when the budget is tight.
    fallbacks: Tuple[str, ...] = ()


def is_kimi(model_id: str) -> bool:
    """Kimi models can be served straight from api.moonshot.ai (cheaper + faster
    than the OpenRouter hop) when a funded Moonshot key is configured."""
    return model_id.startswith(("moonshotai/kimi", "kimi-"))


def is_dashscope(model_id: str) -> bool:
    """`dashscope/<id>` models go straight to Alibaba Model Studio — the owner's
    benchmarked-fast Qwen set (qwen3-coder-480b ~1.5s, qwen3.7-max, qwen3-vl-plus)."""
    return model_id.startswith("dashscope/")


def is_openrouter(model_id: str) -> bool:
    """`openrouter/<slug>` models go through the OpenRouter API directly
    (e.g. openrouter/free, the $0 free-tier router)."""
    return model_id.startswith("openrouter/")


def is_minimax(model_id: str) -> bool:
    """`minimax/<id>` models go straight to api.minimax.io (OpenAI-compatible)
    when a MiniMax key is configured — skips the OpenRouter hop."""
    return model_id.startswith("minimax/")


def is_glm(model_id: str) -> bool:
    """`glm/<id>` models go straight to Zhipu BigModel (OpenAI-compatible)
    when a GLM key is configured — skips the OpenRouter hop."""
    return model_id.startswith("glm/")


# Every model the swarm can reach - DashScope-direct + local, priced for in-app
# (verified 2026-07-20). Fallback ids resolve here so degraded calls are still
# costed correctly.
MODEL_SPECS: Dict[str, ModelSpec] = {
    # --- DashScope-direct Qwen (owner-benchmarked, ~1-1.5s each, 2026-08-02).
    # Pricing is estimated from the OpenRouter-equivalent tiers; the account is
    # metered on Alibaba so these figures are for in-app budgeting only.
    "dashscope/qwen3-coder-480b-a35b-instruct": ModelSpec(
        id="dashscope/qwen3-coder-480b-a35b-instruct",
        cost_in_per_million=0.35, cost_out_per_million=1.40,
        context_window=262_144,
    ),
    "dashscope/qwen3.7-max-2026-05-17": ModelSpec(
        id="dashscope/qwen3.7-max-2026-05-17",
        cost_in_per_million=1.48, cost_out_per_million=4.42,
        context_window=1_000_000,
    ),
    # Long Task builder flagship (owner-requested). Pricing mirrors the
    # qwen3.7-max tier until Alibaba publishes 3.8 figures; budgeting only.
    "dashscope/qwen3.8-max": ModelSpec(
        id="dashscope/qwen3.8-max",
        cost_in_per_million=1.48, cost_out_per_million=4.42,
        context_window=1_000_000,
    ),
    "dashscope/qwen3.7-flash": ModelSpec(
        id="dashscope/qwen3.7-flash",
        cost_in_per_million=0.20, cost_out_per_million=0.80,
        context_window=1_000_000,
    ),
    "dashscope/qwen-turbo": ModelSpec(
        id="dashscope/qwen-turbo",
        cost_in_per_million=0.05, cost_out_per_million=0.20,
        context_window=1_000_000,
    ),
    "dashscope/qwen-plus": ModelSpec(
        id="dashscope/qwen-plus",
        cost_in_per_million=0.20, cost_out_per_million=0.80,
        context_window=1_000_000,
    ),
    "dashscope/qwen-max": ModelSpec(
        id="dashscope/qwen-max",
        cost_in_per_million=1.00, cost_out_per_million=4.00,
        context_window=1_000_000,
    ),
    "dashscope/qwen-coder-plus": ModelSpec(
        id="dashscope/qwen-coder-plus",
        cost_in_per_million=0.35, cost_out_per_million=1.40,
        context_window=1_000_000,
    ),
    "dashscope/qwen-vl-plus": ModelSpec(
        id="dashscope/qwen-vl-plus",
        cost_in_per_million=0.35, cost_out_per_million=1.20,
        context_window=131_072, supports_vision=True,
    ),
    "dashscope/qwen3-vl-plus": ModelSpec(
        id="dashscope/qwen3-vl-plus",
        cost_in_per_million=0.35, cost_out_per_million=1.20,
        context_window=131_072, supports_vision=True,
    ),
    # ---- OpenRouter Qwen-VL (verified live 2026-08-10; the DashScope
    # token-plan has no vision model). Used by the eye role and the
    # weak-model visual augmentation layer.
    "openrouter/qwen3-vl-32b-instruct": ModelSpec(
        id="openrouter/qwen3-vl-32b-instruct",
        cost_in_per_million=0.35, cost_out_per_million=1.40,
        context_window=131_072, supports_vision=True,
    ),
    "openrouter/qwen3-vl-8b-instruct": ModelSpec(
        id="openrouter/qwen3-vl-8b-instruct",
        cost_in_per_million=0.04, cost_out_per_million=0.16,
        context_window=131_072, supports_vision=True,
    ),
    # Local Qwen-VL via Ollama (pulled 2026-08-10; free, private). Used as
    # the eye fallback so vision judging works with no cloud budget.
    "local/qwen3-vl-8b": ModelSpec(
        id="local/qwen3-vl-8b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=32_768, supports_vision=True,
    ),
    # DashScope image generation (qwen-image-2.0-pro on the plan key).
    "dashscope/qwen-image-2.0-pro": ModelSpec(
        id="dashscope/qwen-image-2.0-pro",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=131_072, supports_image_gen=True,
    ),
    # ---- DeepSeek direct (api.deepseek.com, BFB-mode workhorse) ----------
    # Pricing per DeepSeek's published per-million rates (2026-08). BFB mode
    # swaps coding roles onto these; default mode leaves them as fallbacks.
    "deepseek/deepseek-v4-flash": ModelSpec(
        id="deepseek/deepseek-v4-flash",
        cost_in_per_million=0.14, cost_out_per_million=0.28,
        context_window=1_000_000,
    ),
    "deepseek/deepseek-v4-pro": ModelSpec(
        id="deepseek/deepseek-v4-pro",
        cost_in_per_million=0.56, cost_out_per_million=1.68,
        context_window=1_000_000,
    ),
    # ---- local / zero-cost models (loaded via llama.cpp, vLLM, Ollama, etc.) --
    # qwen3:8b served by Ollama on localhost:11434 (zero cost).
    # NOTE: thinking mode is DISABLED by the eval harness (think:false)
    # because qwen3 defaults to reasoning and burns the token budget.
    "local/qwen3:8b": ModelSpec(
        id="local/qwen3:8b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=32_768,
    ),
    "local/qwen2.5-coder-14b": ModelSpec(
        id="local/qwen2.5-coder-14b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=131_072,
    ),
    "local/deepseek-coder-v2-lite-16b": ModelSpec(
        id="local/deepseek-coder-v2-lite-16b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=128_000,
    ),
    "local/qwen3-14b": ModelSpec(
        id="local/qwen3-14b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=131_072,
    ),
    "local/phi-4-14b": ModelSpec(
        id="local/phi-4-14b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=131_072,
    ),
    # The FABLE local stack on the 4080 (served by llama.cpp, zero cost).
    # fable-max-35b: Qwen3.6-35B-A3B UD-Q2_K_XL on port 8081 (~47 tok/s warm,
    # fully VRAM-resident) — the local big brain. fable-fusion-27b: dense 27B
    # on port 8082 when its launcher is running.
    "local/fable-max-35b": ModelSpec(
        id="local/fable-max-35b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=8192,
    ),
    "local/fable-fusion-27b": ModelSpec(
        id="local/fable-fusion-27b",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=8192,
    ),
    # Same 35B-A3B brain as fable-max-35b, referenced by config.yaml under its
    # llama.cpp serving name (models.fable_max + the vibe lane's primary).
    "local/fable-max-llamacpp": ModelSpec(
        id="local/fable-max-llamacpp",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=8192,
    ),
    "local/fable-fast": ModelSpec(
        id="local/fable-fast",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=65_536,
    ),
    # ---- OpenRouter FREE tier. `openrouter/free` auto-picks among the
    # provider's :free models (cost 0; requires OPENROUTER_API_KEY). The chain
    # walker degrades to the next lane entry when the key is absent.
    "openrouter/free": ModelSpec(
        id="openrouter/free",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=262_144,
    ),
    # ---- DashScope FREE-quota catalog (Model Studio console, 2026-08-09).
    # Priced 0 so budget guardrails never block free calls; when a quota
    # exhausts the provider errors and the chain walker degrades instead.
    **{f"dashscope/{m['id']}": ModelSpec(
        id=f"dashscope/{m['id']}",
        cost_in_per_million=0.0, cost_out_per_million=0.0,
        context_window=int(m.get("context", 131_072)),
        supports_vision=bool(m.get("vision", False)),
    ) for m in QWEN_CHAT_MODELS},
    # ---- Direct third-party chat models (Kimi / MiniMax / GLM). Paid; priced
    # from OpenRouter-equivalent tiers for in-app budgeting only.
    **{m["id"]: ModelSpec(
        id=m["id"],
        cost_in_per_million=float(m.get("cost_in_per_million", 0.0)),
        cost_out_per_million=float(m.get("cost_out_per_million", 0.0)),
        context_window=int(m.get("context", 131_072)),
        supports_vision=bool(m.get("vision", False)),
    ) for m in EXTRA_CHAT_MODELS},
}


def _spec(model_id: str, **overrides: object) -> ModelSpec:
    """Build a role binding from the pricing registry so a role and its
    fallbacks can never drift out of sync with real pricing."""
    base = MODEL_SPECS[model_id]
    return base.model_copy(update=overrides)


# Each role is bound to the model that is genuinely best at ITS job, with a
# declared degradation path. Previously every role collapsed onto the same one
# or two models, which made the "swarm" mostly cosmetic.
COUNCIL: Dict[str, ModelSpec] = {
    # Deep planning/decomposition — the owner's benchmarked reasoning pick,
    # direct from DashScope (~1.5s), with the Kimi + OpenRouter chain behind it.
    "architect": _spec(
        "dashscope/qwen-max",
        fallbacks=("dashscope/qwen-plus", "dashscope/qwen-turbo", "dashscope/qwen3.8-max"),
    ),
    # Code generation — Qwen3-Coder-480B direct is the fastest verified engine
    # (~1.5s) and a dedicated code model; Kimi-code + OR coder back it up.
    "engineer": _spec(
        "dashscope/qwen-coder-plus",
        fallbacks=("dashscope/qwen-max", "dashscope/qwen-plus", "dashscope/qwen3-coder-480b-a35b-instruct"),
    ),
    # Goal-fit judging / acceptance checks — strong reasoning, cheap.
    "inspector": _spec(
        "dashscope/qwen-plus",
        fallbacks=("dashscope/qwen-max", "dashscope/qwen-turbo", "dashscope/qwen3.7-flash"),
    ),
    # Hardest failures after a retry — the heavyweight.
    "debugger": _spec(
        "dashscope/qwen-max",
        fallbacks=("dashscope/qwen-plus", "dashscope/qwen3.8-max", "dashscope/qwen3.7-flash"),
    ),
    # Vision critique — the owner's designated vision judge, direct.
    "eye": _spec(
        "openrouter/qwen3-vl-32b-instruct",
        fallbacks=("openrouter/qwen3-vl-8b-instruct", "local/qwen3-vl-8b", "dashscope/qwen-vl-plus", "dashscope/qwen3-vl-plus"),
    ),
    # Image generation (verified to return real image data).
    "artist": _spec("dashscope/qwen-image-2.0-pro"),
    # Prose/ideation.
    "creative": _spec(
        "dashscope/qwen-max",
        fallbacks=("dashscope/qwen-plus", "dashscope/qwen3.7-flash"),
    ),
    # Cheap, fast, high-frequency grunt work.
    "worker": _spec(
        "dashscope/qwen-turbo",
        fallbacks=("dashscope/qwen-plus", "dashscope/qwen-max", "dashscope/qwen3.7-flash"),
    ),
    # Long Task builder - CREDIT-SAVING: cheap flash models do all the coding.
    # Qwen 3.7 flash is the primary coder; qwen-coder-plus is the second
    # coder (owner-directed "3.7 and deepseek flash do the coding"). Qwen 3.8
    # Max is deliberately NOT here — it only advises (see longtask_reviewer).
    "longtask_builder": _spec(
        "dashscope/qwen3.7-flash",
        fallbacks=("dashscope/qwen-coder-plus",
                   "dashscope/qwen3-coder-480b-a35b-instruct",
                   "dashscope/qwen-turbo"),
    ),
    # Long Task reviewer/judge - Qwen 3.8 Max only SEES and ADVISES (never
    # codes). Reviews run rarely (finish gate + judge cap) so putting the
    # strong model here instead of the builder saves credits while keeping
    # quality high. Local FABLE stays in the chain for zero-cost advice when
    # the 4080 server is up.
    "longtask_reviewer": _spec(
        "dashscope/qwen3.8-max",
        fallbacks=("local/fable-max-llamacpp",
                   "dashscope/qwen3-coder-480b-a35b-instruct"),
    ),
}


# BFB (Bang For Buck) mode: swap coding roles onto DeepSeek V4 while it is
# cheap. The oracle (qwen3.8-max) and the cheapest lane (worker) never move —
# role lock and cost lane are preserved in both modes.
BFB_OVERRIDES: Dict[str, str] = {
    "longtask_builder": "deepseek/deepseek-v4-flash",
    "engineer": "deepseek/deepseek-v4-flash",
    "debugger": "deepseek/deepseek-v4-pro",
}


def resolve_mode(mode: str) -> str:
    """Normalize a routing-mode string; unknown modes fall back to default."""
    if isinstance(mode, str) and mode.strip() in ("default", "bfb"):
        return mode.strip()
    return "default"


def _spec_or_default(model_id: str) -> ModelSpec:
    """Return a priced spec for a model id; unknown ids get a safe default
    (zero cost, 128k context) so config.yaml overrides never crash routing."""
    if model_id in MODEL_SPECS:
        return MODEL_SPECS[model_id]
    logger.warning("Model %r has no pricing entry; using default spec.", model_id)
    return ModelSpec(
        id=model_id,
        cost_in_per_million=0.0,
        cost_out_per_million=0.0,
        context_window=131_072,
    )


def build_council(
    overrides: Optional[Dict[str, str]] = None,
    mode: str = "default",
) -> Dict[str, ModelSpec]:
    """Build a council from hardcoded defaults, applying config.yaml overrides.

    mode="bfb" (Bang For Buck) first swaps coding roles onto DeepSeek V4 via
    BFB_OVERRIDES, then explicit config overrides are applied on top. Unknown
    override models fall back to a default spec so a stale slug surfaces in
    logs/validation rather than crashing a mission.
    """
    council = dict(COUNCIL)
    if resolve_mode(mode) == "bfb":
        for role, model_id in BFB_OVERRIDES.items():
            if role not in council:
                continue
            base = council[role]
            council[role] = _spec_or_default(model_id).model_copy(
                update={"fallbacks": base.fallbacks}
            )
    for role, model_id in (overrides or {}).items():
        if role not in council:
            logger.warning("Ignoring unknown council role override %r.", role)
            continue
        if not isinstance(model_id, str) or not model_id:
            continue
        base = council[role]
        # Preserve the original fallback chain; only swap the primary model.
        council[role] = _spec_or_default(model_id).model_copy(
            update={"fallbacks": base.fallbacks}
        )
    # Role lock, enforced centrally: the oracle (qwen3.8-max) never drafts, in
    # ANY mode — strip it from every non-reviewer chain's fallbacks.
    for role, spec in council.items():
        if role == "longtask_reviewer":
            continue
        if "dashscope/qwen3.8-max" in spec.fallbacks:
            council[role] = spec.model_copy(update={
                "fallbacks": tuple(
                    f for f in spec.fallbacks if f != "dashscope/qwen3.8-max")
            })
    return council


class ModelRouter:
    """Routes tasks to council members and prices their token usage in AUD."""

    def __init__(self, council: Optional[Dict[str, ModelSpec]] = None) -> None:
        self.council: Dict[str, ModelSpec] = council if council is not None else COUNCIL

    def route(
        self,
        task_type: str = "general",
        complexity: Complexity = "simple",
        has_vision: bool = False,
    ) -> str:
        """Return the council role key that should handle this task."""
        try:
            normalized_task: str = (task_type or "general").strip().lower()

            if has_vision and normalized_task in VISION_CRITIQUE_TASK_TYPES:
                return "eye"
            if has_vision and normalized_task in IMAGE_GEN_TASK_TYPES:
                return "artist"
            if complexity == "architectural":
                return "architect"
            if complexity == "complex":
                return "engineer"
            if normalized_task in CREATIVE_TASK_TYPES:
                return "creative"
            return "worker"
        except (AttributeError, TypeError) as exc:
            logger.error("route() received bad inputs (%s); defaulting to worker", exc)
            return "worker"

    def get_spec(self, role: str) -> ModelSpec:
        """Look up a council member's spec; unknown roles fall back to worker."""
        try:
            return self.council[role]
        except KeyError:
            logger.warning("Unknown council role %r; falling back to worker", role)
            return self.council["worker"]

    def spec_for_id(self, model_id: str) -> Optional[ModelSpec]:
        """Resolve any model id (primary or fallback) to a priced spec."""
        if model_id in MODEL_SPECS:
            return MODEL_SPECS[model_id]
        for spec in self.council.values():
            if spec.id == model_id:
                return spec
        return None

    def chain_for(self, role: str) -> Tuple[ModelSpec, ...]:
        """The full ordered degradation path for a role: primary then each
        declared fallback. Callers walk this on provider errors so one bad
        slug or a 429 can't kill a mission."""
        primary = self.get_spec(role)
        chain = [primary]
        for fid in primary.fallbacks:
            spec = self.spec_for_id(fid)
            if spec is None:
                logger.warning("Role %s declares unknown fallback %r", role, fid)
                continue
            chain.append(spec)
        return tuple(chain)

    def validate(self) -> Dict[str, list]:
        """Boot-time sanity check: every role's primary and fallbacks must
        resolve to a priced spec. Returns {"ok": [...], "problems": [...]}."""
        ok: list = []
        problems: list = []
        for role, spec in self.council.items():
            if self.spec_for_id(spec.id) is None:
                problems.append(f"{role}: primary {spec.id!r} has no pricing entry")
            else:
                ok.append(f"{role} -> {spec.id}")
            for fid in spec.fallbacks:
                if self.spec_for_id(fid) is None:
                    problems.append(f"{role}: fallback {fid!r} unknown")
        return {"ok": ok, "problems": problems}

    def route_spec(
        self,
        task_type: str = "general",
        complexity: Complexity = "simple",
        has_vision: bool = False,
    ) -> Tuple[str, ModelSpec]:
        """Convenience: route and return (role, spec) in one call."""
        role: str = self.route(
            task_type=task_type, complexity=complexity, has_vision=has_vision
        )
        return role, self.get_spec(role)

    def calculate_cost(
        self, spec: ModelSpec, input_tokens: int, output_tokens: int
    ) -> float:
        """Convert token usage to AUD: tokens * price / 1M USD, then USD / 0.67."""
        try:
            usd: float = (
                input_tokens * spec.cost_in_per_million / 1_000_000.0
                + output_tokens * spec.cost_out_per_million / 1_000_000.0
            )
            return usd / USD_PER_AUD
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            logger.error("calculate_cost() failed: %s", exc)
            return 0.0


class LaneRouter:
    """High-level lane routing from the custom model strategy.

    Maps a task's shape to a lane (cheap/smart/vision/custom) and returns the
    primary model spec plus an ordered fallback chain. The custom lane is only
    used when explicitly configured with a reachable endpoint; otherwise the
    router falls back to smart/cheap/vision as appropriate.
    """

    def __init__(
        self,
        model_router: Optional[ModelRouter] = None,
        lanes: Optional[Dict[str, Tuple[str, ...]]] = None,
    ) -> None:
        self.model_router: ModelRouter = model_router if model_router is not None else ModelRouter()
        # Default lane chains: primary first, then fallbacks.
        self.lanes: Dict[str, Tuple[str, ...]] = {
            LANE_CHEAP: ("dashscope/qwen-turbo", "dashscope/qwen-plus", "dashscope/qwen-max"),
            LANE_SMART: ("dashscope/qwen3.8-max", "dashscope/qwen-max", "dashscope/qwen-plus"),
            LANE_VISION: ("dashscope/qwen3-vl-plus", "dashscope/qwen-vl-plus", "dashscope/qwen-max"),
            LANE_LOCAL: ("local/qwen2.5-coder-14b", "local/deepseek-coder-v2-lite-16b", "local/qwen3-14b"),
            LANE_CUSTOM: ("dashscope/qwen3-coder-480b-a35b-instruct", "dashscope/qwen-max", "dashscope/qwen3.7-flash"),
        }
        if lanes:
            self.lanes.update({k: tuple(v) if isinstance(v, list) else v for k, v in lanes.items()})

    def chain(self, lane: str) -> Tuple[ModelSpec, ...]:
        """Return the ordered model-spec chain for a lane, skipping unknown ids."""
        chain: List[ModelSpec] = []
        for model_id in self.lanes.get(lane, self.lanes[LANE_CHEAP]):
            spec = self.model_router.spec_for_id(model_id)
            if spec is None:
                logger.warning("Lane %s declares unknown model %r; skipping.", lane, model_id)
                continue
            chain.append(spec)
        if not chain:
            logger.warning("Lane %r resolved to no known models; falling back to cheap.", lane)
            return self.chain(LANE_CHEAP)
        return tuple(chain)

    @staticmethod
    def _pick_lane(
        task_type: str = "general",
        complexity: Complexity = "simple",
        has_vision: bool = False,
        has_images: bool = False,
        long_horizon: bool = False,
        preferred_lane: Optional[str] = None,
    ) -> str:
        if preferred_lane in VALID_LANES:
            return preferred_lane
        normalized_task: str = (task_type or "general").strip().lower()
        if has_images or (has_vision and normalized_task in VISION_CRITIQUE_TASK_TYPES):
            return LANE_VISION
        if complexity == "architectural" or long_horizon or normalized_task in SMART_TASK_TYPES:
            return LANE_SMART
        if complexity == "complex":
            return LANE_SMART
        return LANE_CHEAP

    def route(
        self,
        task_type: str = "general",
        complexity: Complexity = "simple",
        has_vision: bool = False,
        has_images: bool = False,
        long_horizon: bool = False,
        preferred_lane: Optional[str] = None,
        ascension_state: Optional[str] = None,
        approved: Optional[Tuple[str, ...]] = None,
        available: Optional[Callable[[str], bool]] = None,
    ) -> Tuple[str, ModelSpec]:
        """Return (lane, primary_spec) for a task shape."""
        lane = self._pick_lane(task_type, complexity, has_vision, has_images, long_horizon, preferred_lane)
        specs = self.chain(lane)
        if ascension_state is not None:
            lane, specs = apply_ascension(ascension_state, lane, specs, approved, available)
        return lane, specs[0]

    def route_chain(
        self,
        task_type: str = "general",
        complexity: Complexity = "simple",
        has_vision: bool = False,
        has_images: bool = False,
        long_horizon: bool = False,
        preferred_lane: Optional[str] = None,
        ascension_state: Optional[str] = None,
        approved: Optional[Tuple[str, ...]] = None,
        available: Optional[Callable[[str], bool]] = None,
    ) -> Tuple[str, Tuple[ModelSpec, ...]]:
        """Return (lane, full_chain) so callers can degrade across lane models."""
        lane = self._pick_lane(task_type, complexity, has_vision, has_images, long_horizon, preferred_lane)
        specs = self.chain(lane)
        if ascension_state is not None:
            lane, specs = apply_ascension(ascension_state, lane, specs, approved, available)
        return lane, specs

    def set_custom_lane(
        self,
        model_id: str,
        fallbacks: Tuple[str, ...] = ("dashscope/qwen-max", "dashscope/qwen3.7-flash"),
    ) -> None:
        """Promote a fine-tuned model to the custom lane and register its spec.

        The custom lane is the default once enough session data has been collected
        and a hosted fine-tune has beaten the previous version on the eval harness.
        """
        # Ensure the model registry has a priced spec so routing never skips it.
        if model_id not in MODEL_SPECS:
            MODEL_SPECS[model_id] = ModelSpec(
                id=model_id,
                cost_in_per_million=0.0,
                cost_out_per_million=0.0,
                context_window=1_000_000,
            )
        self.lanes[LANE_CUSTOM] = (model_id,) + tuple(fallbacks)
        logger.info("Custom lane updated to primary model %r", model_id)


__all__ = [
    "ModelSpec",
    "ModelRouter",
    "LaneRouter",
    "COUNCIL",
    "MODEL_SPECS",
    "USD_PER_AUD",
    "Complexity",
    "is_kimi",
    "is_dashscope",
    "build_council",
    "resolve_mode",
    "BFB_OVERRIDES",
    "_spec_or_default",
    "LANE_CHEAP",
    "LANE_SMART",
    "LANE_VISION",
    "LANE_LOCAL",
    "LANE_CUSTOM",
    "VALID_LANES",
]
