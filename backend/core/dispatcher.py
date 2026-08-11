"""Demand dispatcher: one entry point that deploys the right small team.

The user never picks from 249 agents. A cheap cached classifier maps the
goal to a task category; the category selects a small templated team
(research+design / coding / testing / media / long-horizon); the team
compiles into the existing mission params (mode/effort/fast/tools) consumed
by swarm.spawn() -- the swarm's own mode branches and _EFFORT_PLAN drive the
council roles underneath, so nothing in the engine changes.

Classifier: DeepSeek Flash (worker role) with an 8-token verdict, parsed
conservatively; any failure or ambiguity falls back to "coding". Results are
cached per normalized goal so repeated calls are free.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("infinity.dispatcher")

# --------------------------------------------------------------------- #
# Team templates: category -> mission params
# --------------------------------------------------------------------- #

CATEGORY_LABELS: Dict[str, str] = {
    "research_design": "Research + design",
    "coding": "Coding",
    "testing": "Testing + QA",
    "media": "Media (image/video)",
    "longtask": "Long-horizon mission",
}

# Modes/efforts map onto the existing swarm branches (auto/code/image/video/
# bfb/swarm/vibe) and _EFFORT_PLAN tiers. Tools are the safe default set the
# missions router already understands; agents stay empty so the swarm picks
# council roles from the category's mode rather than chat personas.
TEAM_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "research_design": {
        "mode": "auto",
        "effort": "med",
        "fast": True,
        "tools": ["read_workspace_file", "search_workspace_text", "fetch_url"],
        "roles": ("scout", "designer", "critic"),
        "note": "Scout (worker) gathers, designer (creative) shapes, critic (inspector) judges.",
    },
    "coding": {
        "mode": "code",
        "effort": "high",
        "fast": False,
        "tools": ["read_workspace_file", "read_workspace_files", "list_workspace_dir",
                  "search_workspace_text", "apply_workspace_edit", "run_workspace_shell"],
        "roles": ("coder", "verifier", "reviewer"),
        "note": "Coder (engineer) builds, verifier (inspector) gates, reviewer (longtask_reviewer) signs off.",
    },
    "testing": {
        "mode": "auto",
        "effort": "med",
        "fast": True,
        "tools": ["run_workspace_shell", "read_workspace_file", "fetch_url"],
        "roles": ("tester", "fixer"),
        "note": "Tester (inspector) runs checks, fixer (debugger) repairs failures.",
    },
    "media": {
        "mode": "image",
        "effort": "med",
        "fast": False,
        "tools": ["generate_image", "read_workspace_file"],
        "roles": ("artist", "eye"),
        "note": "Artist generates, eye (Qwen-VL) judges the result visually.",
    },
    "longtask": {
        "mode": "auto",
        "effort": "xhigh",
        "fast": False,
        "tools": ["read_workspace_file", "apply_workspace_edit", "run_workspace_shell"],
        "roles": ("builder", "reviewer"),
        "note": "Builder (longtask_builder) works milestones, reviewer (longtask_reviewer) verifies each.",
    },
}

VALID_CATEGORIES: Tuple[str, ...] = tuple(TEAM_TEMPLATES.keys())

# --------------------------------------------------------------------- #
# Classifier (cheap, cached, conservative)
# --------------------------------------------------------------------- #

_CLASSIFIER_PROMPT = (
    "Classify this coding-agent request into exactly one category. "
    "Reply with one word only. "
    "research_design = research, design, planning, spec, architecture review; "
    "coding = write, build, fix, refactor, feature, bug, implement code; "
    "testing = test, verify, debug, QA, run checks; "
    "media = image, video, art, generate a picture, 3D asset; "
    "longtask = multi-step project, build a whole game/app, long mission. "
    "Request: "
)

_CATEGORY_RE = re.compile(
    r"\b(research_design|coding|testing|media|longtask)\b", re.IGNORECASE
)

_CLASSIFIER_CACHE: Dict[str, str] = {}
_MAX_CACHE = 512


def _normalize_goal(goal: str) -> str:
    return re.sub(r"\s+", " ", (goal or "").strip().lower())[:400]


def parse_category(reply: str) -> str:
    """Extract a category from a noisy model reply; conservative fallback."""
    if not reply:
        return "coding"
    m = _CATEGORY_RE.search(reply)
    if not m:
        return "coding"
    cat = m.group(1).lower()
    return cat if cat in VALID_CATEGORIES else "coding"


def classify_demand(goal: str, client: Optional[Any] = None) -> str:
    """Map a goal to a category via the cached cheap classifier.

    ``client`` is an OpenRouterClient-compatible object with
    ``chat(model_id=..., messages=..., max_tokens=..., extra_body=None)``
    returning ``{"text": ...}`` (PredictiveRouter-style). When omitted or on
    any failure, the heuristic falls back to "coding".
    """
    key = _normalize_goal(goal)
    if key in _CLASSIFIER_CACHE:
        return _CLASSIFIER_CACHE[key]

    category = "coding"
    if key and client is not None:
        try:
            result = client.chat(
                model_id="deepseek/deepseek-v4-flash",
                messages=[{"role": "user", "content": _CLASSIFIER_PROMPT + key}],
                max_tokens=8,
                extra_body=None,
            )
            category = parse_category(str(result.get("text", "")))
        except Exception as exc:  # noqa: BLE001 - classifier must never break dispatch
            logger.warning("Dispatcher classifier failed; falling back to coding: %s", exc)

    if len(_CLASSIFIER_CACHE) < _MAX_CACHE:
        _CLASSIFIER_CACHE[key] = category
    return category


# --------------------------------------------------------------------- #
# Team builder
# --------------------------------------------------------------------- #

def build_team(category: str, effort: Optional[str] = None) -> Dict[str, Any]:
    """Compile mission params for a category (effort override allowed)."""
    template = TEAM_TEMPLATES.get(category, TEAM_TEMPLATES["coding"])
    params = {
        "mode": template["mode"],
        "effort": effort or template["effort"],
        "fast": template["fast"],
        "tools": list(template["tools"]),
        "agents": [],
    }
    return {"category": category, "params": params, "roles": template["roles"], "note": template["note"]}


def dispatch(goal: str, client: Optional[Any] = None) -> Dict[str, Any]:
    """One entry point: classify -> deploy the matching small team."""
    category = classify_demand(goal, client)
    team = build_team(category)
    team["category"] = category
    team["rationale"] = f"{CATEGORY_LABELS[category]}: {team['note']}"
    return team
