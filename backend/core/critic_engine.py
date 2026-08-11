"""Vision critique loop for Infinity Code.

The Critic sends the work-in-progress screenshot and the reference image to
Qwen3-VL over OpenRouter and gets back structured scores plus concrete fixes.
It never fabricates a score: missing images or unparseable model output yield
an explicit zero-score result with the failure reason in `fixes`.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from backend.tools.openrouter_client import (
        OpenRouterClient,
        OpenRouterError,
        encode_image_base64,
    )
except ImportError:  # running with backend/ as the working directory
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
        encode_image_base64,
    )

logger = logging.getLogger(__name__)

CRITIC_MODEL_ID: str = "qwen/qwen3-vl-30b-a3b-thinking"

_MIME_BY_SUFFIX: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

_CRITIQUE_PROMPT_TEMPLATE: str = (
    "You are a ruthless art director reviewing a '{task_type}' task.\n"
    "The FIRST image is the WORK produced by an agent. The SECOND image is the "
    "REFERENCE it must match.\n\n"
    "Score the work against the reference. Respond with ONLY a JSON object, no "
    "prose, no markdown, exactly these fields:\n"
    "{{\n"
    '  "overall": <float 0.0-1.0>,\n'
    '  "composition": <float 0.0-1.0>,\n'
    '  "color": <float 0.0-1.0>,\n'
    '  "style_match": <float 0.0-1.0>,\n'
    '  "technical_execution": <float 0.0-1.0>,\n'
    '  "fixes": ["<specific fix 1>", "<specific fix 2>", "<specific fix 3>"]\n'
    "}}\n"
    "Be harsh. A 1.0 means indistinguishable from the reference."
)


class CritiqueResult(BaseModel):
    """Structured critique scores, all in the range 0.0-1.0."""

    overall: float = 0.0
    composition: float = 0.0
    color: float = 0.0
    style_match: float = 0.0
    technical_execution: float = 0.0
    fixes: List[str] = Field(default_factory=list)
    raw_response: str = ""


class CriticEngine:
    """Compares a work image against a reference image using Qwen3-VL."""

    def __init__(
        self,
        client: Optional[OpenRouterClient] = None,
        model_id: str = CRITIC_MODEL_ID,
    ) -> None:
        self.client: OpenRouterClient = client if client is not None else OpenRouterClient()
        self.model_id: str = model_id

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _zero_result(reason: str) -> CritiqueResult:
        logger.warning("Critique returned zero-score result: %s", reason)
        return CritiqueResult(fixes=[reason], raw_response="")

    @staticmethod
    def _mime_for(path: Path) -> str:
        return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")

    @staticmethod
    def _clamp(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _image_part(self, path: Path) -> Dict[str, Any]:
        encoded: str = encode_image_base64(path)
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self._mime_for(path)};base64,{encoded}"},
        }

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from raw model output, tolerating markdown fences."""
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
            if fenced:
                parsed = json.loads(fenced.group(1))
                return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            brace = re.search(r"\{[\s\S]*\}", text)
            if brace:
                parsed = json.loads(brace.group(0))
                return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def critique(
        self, work_path: Path, ref_path: Path, task_type: str
    ) -> CritiqueResult:
        """Score `work_path` against `ref_path` for the given task type."""
        work_path = Path(work_path)
        ref_path = Path(ref_path)

        if not work_path.is_file():
            return self._zero_result(f"Work image missing: {work_path}")
        if not ref_path.is_file():
            return self._zero_result(f"Reference image missing: {ref_path}")

        try:
            work_part: Dict[str, Any] = self._image_part(work_path)
            ref_part: Dict[str, Any] = self._image_part(ref_path)
        except OpenRouterError as exc:
            return self._zero_result(f"Could not encode images: {exc}")

        prompt: str = _CRITIQUE_PROMPT_TEMPLATE.format(task_type=task_type)
        messages: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    work_part,
                    ref_part,
                ],
            }
        ]

        try:
            response = self.client.chat_with_vision(
                model_id=self.model_id,
                messages_with_images=messages,
                max_tokens=2000,
                extra_body={"enable_thinking": True},
            )
        except OpenRouterError as exc:
            return self._zero_result(f"Vision model call failed: {exc}")

        raw_text: str = response["text"]
        parsed: Optional[Dict[str, Any]] = self._parse_json(raw_text)
        if parsed is None:
            return self._zero_result(
                f"Could not parse critique JSON from model output: {raw_text[:200]}"
            )

        fixes_raw: Any = parsed.get("fixes", [])
        fixes: List[str] = (
            [str(f) for f in fixes_raw][:3] if isinstance(fixes_raw, list) else []
        )

        return CritiqueResult(
            overall=self._clamp(parsed.get("overall")),
            composition=self._clamp(parsed.get("composition")),
            color=self._clamp(parsed.get("color")),
            style_match=self._clamp(parsed.get("style_match")),
            technical_execution=self._clamp(parsed.get("technical_execution")),
            fixes=fixes,
            raw_response=raw_text,
        )


__all__ = ["CriticEngine", "CritiqueResult", "CRITIC_MODEL_ID"]
