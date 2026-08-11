"""Skill composition for Infinity Code.

Auto-fuses two proven skills into a single coherent workflow. A cheap council
worker judges whether two skills are compatible; when they are, their steps are
concatenated (A then B), re-numbered, and de-duplicated into one new skill JSON
written into the skill library for the caller to time-stamp and verify.

This module never fabricates a fusion: an incompatible verdict, a missing skill,
a failed model call, or a write error all return ``None`` rather than a bogus
skill. Malformed model JSON is tolerated by regex-extracting the object and
falling back to a safe "not compatible" default.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.core.router import ModelRouter
    from backend.core.skill_engine import SkillEngine
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.router import ModelRouter  # type: ignore[no-redef]
    from core.skill_engine import SkillEngine  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

_COMPOSE_ROLE: str = "worker"
_COMPOSE_MAX_TOKENS: int = 200
# Safe default when the model's compatibility verdict cannot be parsed.
_SAFE_VERDICT: Dict[str, Any] = {"compatible": False, "fused_topic": ""}


def _slugify(value: str) -> str:
    """Lowercase and collapse every non-alphanumeric run to a single dash."""
    try:
        slug: str = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
        return slug or "skill"
    except (TypeError, ValueError) as exc:
        logger.error("Could not slugify %r: %s", value, exc)
        return "skill"


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Parse a JSON object from a possibly-noisy model reply.

    Tries a direct decode first, then regex-extracts the first ``{...}`` block.
    Returns a copy of the safe default verdict if nothing usable is found.
    """
    if not text or not isinstance(text, str):
        return dict(_SAFE_VERDICT)

    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Malformed compatibility JSON, using safe default: %s", exc)

    return dict(_SAFE_VERDICT)


class SkillComposer:
    """Fuses pairs of proven skills into new composite skills."""

    def __init__(
        self,
        skill_engine: SkillEngine,
        client: OpenRouterClient,
        router: ModelRouter,
    ) -> None:
        self.skill_engine: SkillEngine = skill_engine
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router

    # ------------------------------------------------------------------ #
    # Pairing
    # ------------------------------------------------------------------ #

    def composable_pairs(self, limit: int = 20) -> List[Tuple[str, str]]:
        """Unordered, unique ``(a, b)`` skill-name pairs, capped at ``limit``."""
        if limit <= 0:
            return []
        try:
            names: List[str] = list(self.skill_engine.list_skills())
        except Exception as exc:  # noqa: BLE001 - defensive: never crash pairing
            logger.error("Could not list skills for pairing: %s", exc)
            return []

        pairs: List[Tuple[str, str]] = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if names[i] == names[j]:
                    continue
                pairs.append((names[i], names[j]))
                if len(pairs) >= limit:
                    return pairs
        return pairs

    # ------------------------------------------------------------------ #
    # Fusion helpers
    # ------------------------------------------------------------------ #

    def _fused_name(self, name_a: str, name_b: str) -> str:
        return _slugify(f"{name_a}-{name_b}-fusion")

    def _merge_steps(
        self,
        steps_a: Any,
        steps_b: Any,
    ) -> List[Dict[str, Any]]:
        """Concatenate A's then B's steps, re-numbering ``order`` and dropping
        steps whose instruction text exactly repeats an earlier one."""
        merged: List[Dict[str, Any]] = []
        seen_instructions: set[str] = set()
        order: int = 0

        for source in (steps_a, steps_b):
            if not isinstance(source, list):
                continue
            for step in source:
                if isinstance(step, dict):
                    new_step: Dict[str, Any] = dict(step)
                    instruction: str = str(new_step.get("instruction", "")).strip()
                else:
                    instruction = str(step).strip()
                    new_step = {"instruction": instruction}

                if not instruction or instruction in seen_instructions:
                    continue
                seen_instructions.add(instruction)
                order += 1
                new_step["order"] = order
                merged.append(new_step)

        return merged

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    def compose(self, name_a: str, name_b: str) -> Optional[Dict[str, Any]]:
        """Fuse two skills into a new skill dict (written to disk), or None.

        Returns None when either skill is missing, the worker judges them
        incompatible, the model call fails, or the fused file cannot be written.
        """
        skill_a: Optional[Dict[str, Any]] = self.skill_engine.get_skill(name_a)
        skill_b: Optional[Dict[str, Any]] = self.skill_engine.get_skill(name_b)
        if skill_a is None or skill_b is None:
            logger.info(
                "Cannot compose %r + %r: one or both skills missing.", name_a, name_b
            )
            return None

        topic_a: str = str(skill_a.get("topic") or name_a)
        topic_b: str = str(skill_b.get("topic") or name_b)

        prompt: str = (
            f'Can skill A ("{topic_a}") and skill B ("{topic_b}") be combined '
            "into one coherent workflow? Reply ONLY JSON: "
            '{"compatible": true|false, "fused_topic": "<short name>"}'
        )

        try:
            spec = self.router.get_spec(_COMPOSE_ROLE)
            model_id: str = spec.id
        except Exception as exc:  # noqa: BLE001 - router should not crash us
            logger.error("Could not resolve compose model spec: %s", exc)
            return None

        try:
            result = self.client.chat(
                model_id=model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=_COMPOSE_MAX_TOKENS,
                extra_body=None,
            )
            reply_text: str = str(result.get("text", "")) if result else ""
        except OpenRouterError as exc:
            logger.error("Compose model call failed for %r+%r: %s", name_a, name_b, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - never propagate model failures
            logger.error(
                "Unexpected compose failure for %r+%r: %s", name_a, name_b, exc
            )
            return None

        verdict: Dict[str, Any] = _extract_json_object(reply_text)
        if not bool(verdict.get("compatible")):
            logger.info("Worker judged %r + %r incompatible.", name_a, name_b)
            return None

        fused_topic_raw: Any = verdict.get("fused_topic")
        fused_topic: str = (
            str(fused_topic_raw).strip()
            if isinstance(fused_topic_raw, str) and fused_topic_raw.strip()
            else f"{topic_a} + {topic_b}"
        )

        fused_name: str = self._fused_name(name_a, name_b)
        merged_steps: List[Dict[str, Any]] = self._merge_steps(
            skill_a.get("steps", []), skill_b.get("steps", [])
        )
        verification_command: str = str(
            skill_a.get("verification_command")
            or skill_b.get("verification_command")
            or ""
        )

        fused_skill: Dict[str, Any] = {
            "name": fused_name,
            "topic": fused_topic,
            "source_url": "",
            "source_type": "composition",
            "steps": merged_steps,
            "verification_command": verification_command,
            "verified": False,
            "composed_from": [name_a, name_b],
            "created_at": "",  # caller stamps the real time
        }

        target_path: Path = self.skill_engine.skills_dir / f"{fused_name}.json"
        try:
            target_path.write_text(
                json.dumps(fused_skill, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not write fused skill %s: %s", target_path, exc)
            return None

        logger.info(
            "Composed %r from %r + %r (%d steps).",
            fused_name,
            name_a,
            name_b,
            len(merged_steps),
        )
        return fused_skill

    def compose_all(self, max_new: int = 3) -> List[Dict[str, Any]]:
        """Attempt fusions across composable pairs, collecting up to ``max_new``.

        Pairs whose fused file already exists on disk are skipped without a
        model call.
        """
        composed: List[Dict[str, Any]] = []
        if max_new <= 0:
            return composed

        for name_a, name_b in self.composable_pairs():
            if len(composed) >= max_new:
                break

            fused_name: str = self._fused_name(name_a, name_b)
            target_path: Path = self.skill_engine.skills_dir / f"{fused_name}.json"
            try:
                if target_path.exists():
                    logger.debug("Skipping %s: fused file already exists.", fused_name)
                    continue
            except OSError as exc:
                logger.warning("Could not stat %s: %s", target_path, exc)
                continue

            fused: Optional[Dict[str, Any]] = self.compose(name_a, name_b)
            if fused is not None:
                composed.append(fused)

        return composed


__all__ = ["SkillComposer"]
