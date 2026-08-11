"""Smart, opinionated defaults that stay out of the way until they help.

The goal is "top-tier out of the box": the app should pick a sensible lane,
mode, effort, and tool set without asking the user, while still exposing every
knob in Settings for people who want to optimize.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("infinity.smart")

# Subtle keyword detectors. These are intentionally simple and conservative:
# a false positive just routes to a slightly stronger model, never dangerous.
_IMAGE_RE = re.compile(
    r"\b(generate|create|draw|render|make).*?(image|picture|photo|illustration|icon)\b",
    re.IGNORECASE,
)
_VIDEO_RE = re.compile(
    r"\b(generate|create|make|animate|interpolate|transition).*?(video|clip|animation|motion)\b",
    re.IGNORECASE,
)
_3D_RE = re.compile(
    r"\b(blender|3d|three[- ]?d|mesh|scene|camera|lighting|bpy|obj|fbx|glb)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(code|function|script|bug|debug|refactor|api|python|javascript|rust|go|java|sql|html|css|react|component)\b",
    re.IGNORECASE,
)
_WEB_RE = re.compile(
    r"\b(current|latest|today|news|weather|price|stock|market|recent|update|202[5-9]|this week)\b",
    re.IGNORECASE,
)
_VISION_RE = re.compile(
    r"\b(screenshot|screen|image|picture|photo|look at|what.*see|describe this)\b",
    re.IGNORECASE,
)
_HARD_RE = re.compile(
    r"\b(architect|architecture|design|system|large|complex|multi[- ]?(step|file)|from scratch|end[- ]?to[- ]?end)\b",
    re.IGNORECASE,
)
_APP_LAUNCH_RE = re.compile(
    r"\b(open|launch|start|run)\s+(?:the\s+)?(?:app(?:lication)?\s+)?(?:blender|unreal(?:\s+editor)?|unity|godot|vscode|visual\s+studio|chrome|[\w .-]+\.exe)\b",
    re.IGNORECASE,
)
_WORKSPACE_QUERY_RE = re.compile(
    r"\b(find|locate|search|grep|look\s+for|which\s+file|read\s+(?:these|the)\s+files|across\s+the\s+(?:repo|project|workspace))\b",
    re.IGNORECASE,
)

# Tools that are safe and broadly useful when the model decides it needs them.
_SMART_CHAT_TOOLS: List[str] = [
    "calculator",
    "run_python",
    "web_search",
    "fetch_url",
    "critique",
    "see_image",
]

# If a chat history grows beyond this, silently drop oldest non-system messages.
_DEFAULT_TRIM_MESSAGES: int = 30
_DEFAULT_TRIM_CHARS: int = 12_000


class SmartDefaults:
    """Opinionated defaults engine. No LLM calls here — everything is rule-based
    and cheap so it can run on every request without budget impact."""

    @staticmethod
    def classify_goal(goal: str) -> Dict[str, Any]:
        """Pick a mode, effort, lane, and tool hint from a free-text goal."""
        goal_lower = (goal or "").lower()
        if _VIDEO_RE.search(goal) or goal_lower.startswith("video:"):
            mode = "video"
        elif _IMAGE_RE.search(goal) or goal_lower.startswith("image:"):
            mode = "image"
        elif _3D_RE.search(goal) or goal_lower.startswith("3d:"):
            mode = "3d"
        elif _CODE_RE.search(goal) or goal_lower.startswith("code:"):
            mode = "code"
        else:
            mode = "auto"

        effort = "med"
        if _HARD_RE.search(goal) or len(goal) > 800:
            effort = "high"
        elif len(goal) < 120 and mode == "auto":
            effort = "low"

        lane = "cheap"
        if mode in ("3d", "video") or _VISION_RE.search(goal):
            lane = "vision"
        elif mode == "code" or _HARD_RE.search(goal) or len(goal) > 600:
            lane = "smart"

        return {
            "mode": mode,
            "effort": effort,
            "lane": lane,
            "predicted": "complex" if effort in ("high", "xhigh", "max", "ultracode") else "simple",
        }

    @staticmethod
    def suggest_tools(text: str, enabled: Optional[List[str]] = None) -> List[str]:
        """Return a curated list of tools worth exposing for this query."""
        tools = list(_SMART_CHAT_TOOLS)
        if _APP_LAUNCH_RE.search(text or ""):
            tools.append("launch_app")
        if _WORKSPACE_QUERY_RE.search(text or ""):
            tools.extend([
                "find_workspace_files",
                "search_workspace_text",
                "read_workspace_files",
            ])
        # Only offer vision tools if the user mentions images/screenshots.
        if not (_VISION_RE.search(text) or "data:image/" in text):
            tools = [t for t in tools if t != "see_image"]
        if enabled is not None:
            tools = [t for t in tools if t in enabled]
        return list(dict.fromkeys(tools))

    @staticmethod
    def suggest_web_search(text: str) -> bool:
        """Guess whether a query is asking for current facts."""
        return bool(_WEB_RE.search(text or ""))

    @staticmethod
    def trim_history(
        messages: List[Dict[str, Any]],
        max_messages: int = _DEFAULT_TRIM_MESSAGES,
        max_chars: int = _DEFAULT_TRIM_CHARS,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Silently drop oldest non-system turns if history is huge.

        Returns (trimmed_messages, was_trimmed).
        """
        if len(messages) <= max_messages:
            return messages, False

        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)

        if len(other_msgs) <= max_messages and total_chars <= max_chars:
            return messages, False

        keep = max_messages - len(system_msgs)
        keep = max(2, keep)  # always keep at least the last user/assistant pair
        trimmed = system_msgs + other_msgs[-keep:]
        logger.debug("Trimmed chat history from %d to %d turns", len(messages), len(trimmed))
        return trimmed, True

    @staticmethod
    def pick_chat_model(user_text: str, preferred: Optional[str] = None) -> Optional[str]:
        """Suggest a chat model only when the query has a strong shape."""
        if preferred:
            return None
        # Vision is unambiguous — only a vision model can handle it.
        if _VISION_RE.search(user_text):
            return "qwen/qwen3-vl-30b-a3b-thinking"
        # Hard reasoning / architecture / long prompts → strongest general model.
        if _HARD_RE.search(user_text) or len(user_text) > 600:
            return "moonshotai/kimi-k3"
        # Code → code specialist.
        if _CODE_RE.search(user_text):
            return "moonshotai/kimi-k2.7-code"
        return None


__all__ = ["SmartDefaults"]
