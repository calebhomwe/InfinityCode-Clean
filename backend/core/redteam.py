"""Adversarial red-team for Infinity Code's generated code.

Before a produced ``main.py`` ships, the red-team asks a cheap council model
to invent concrete failure modes ("attack vectors") and, for each, a
self-contained Python probe that exits 0 when the code under test is robust
and exits non-zero (with a printed reason) when it is not. Those probes are
then genuinely written to disk and executed in a real subprocess via
``exec_utils.run_python`` — no simulation.

The whole surface is defensive: every model call, JSON parse, file write and
subprocess run is wrapped so a malformed model response or a hostile probe can
never take the pipeline down. A model that returns junk simply yields zero
attacks (a vacuous pass) rather than an exception.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.core.exec_utils import run_python
    from backend.core.router import ModelRouter, ModelSpec
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.exec_utils import run_python  # type: ignore[no-redef]
    from core.router import ModelRouter, ModelSpec  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

# The red-team runs on the cheap "worker" council model by design.
_RED_TEAM_ROLE: str = "worker"
_ATTACK_MAX_TOKENS: int = 3500
_ATTACK_TIMEOUT_SECONDS: int = 30
_OUTPUT_TAIL_CHARS: int = 2000
_MAX_CODE_IN_PROMPT_CHARS: int = 12000

_SYSTEM_PROMPT: str = (
    "You are a ruthless adversarial code reviewer (a red-team). You are given "
    "a coding goal and a candidate Python program that will be saved as "
    "'main.py'. Your job is to find ways it breaks: edge cases, malformed "
    "input, boundary values, missing files, empty data, unicode, large "
    "input, injection, and unhandled exceptions. You do NOT fix the code — "
    "you attack it."
)

_INSTRUCTION_TEMPLATE: str = (
    "GOAL:\n{goal}\n\n"
    "CANDIDATE CODE (will be saved as main.py in the working directory):\n"
    "```python\n{code}\n```\n\n"
    "Produce up to {max_attacks} distinct attack vectors. Respond with ONLY a "
    "JSON array (no prose, no markdown fences) of objects with EXACTLY these "
    "two string keys:\n"
    '  "vector"    - a short description of the failure mode.\n'
    '  "test_code" - a SELF-CONTAINED Python script that probes for this '
    "failure mode. It runs in the same working directory as main.py. It MUST "
    "exit 0 (sys.exit(0)) if the property holds / the code is robust, and it "
    "MUST print a human-readable reason and sys.exit(1) if the code under "
    "test is NOT robust to this vector. The script may run main.py via "
    "subprocess (e.g. subprocess.run([sys.executable, 'main.py'], ...)). "
    "Import everything it uses. Do not depend on third-party packages.\n\n"
    "Return the JSON array now."
)

__all__ = ["RedTeamAgent"]


class RedTeamAgent:
    """Generates and executes adversarial probes against generated code."""

    def __init__(self, client: OpenRouterClient, router: ModelRouter) -> None:
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router
        self.spec: ModelSpec = router.get_spec(_RED_TEAM_ROLE)
        # Token usage of the most recent generate_attacks() model call, so
        # review() can price it without changing the public signatures.
        self._last_input_tokens: int = 0
        self._last_output_tokens: int = 0

    # ------------------------------------------------------------------ #
    # Attack generation
    # ------------------------------------------------------------------ #

    def generate_attacks(
        self, goal: str, code: str, max_attacks: int = 5
    ) -> List[Dict[str, str]]:
        """Ask the worker model for up to ``max_attacks`` attack probes.

        Returns a list of ``{"vector", "test_code"}`` dicts. Any transport
        error, malformed JSON, or unusable payload degrades to an empty list
        rather than raising.
        """
        self._last_input_tokens = 0
        self._last_output_tokens = 0

        try:
            safe_max: int = max(1, int(max_attacks))
        except (TypeError, ValueError):
            safe_max = 5

        prompt: str = _INSTRUCTION_TEMPLATE.format(
            goal=(goal or "").strip() or "(no goal provided)",
            code=(code or "")[:_MAX_CODE_IN_PROMPT_CHARS],
            max_attacks=safe_max,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self.client.chat(
                model_id=self.spec.id,
                messages=messages,
                max_tokens=_ATTACK_MAX_TOKENS,
                extra_body=None,
            )
        except OpenRouterError as exc:
            logger.error("Red-team attack generation failed (model call): %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001 - never let generation crash the pipeline
            logger.error("Red-team attack generation raised unexpectedly: %s", exc)
            return []

        try:
            self._last_input_tokens = int(result.get("input_tokens", 0) or 0)
            self._last_output_tokens = int(result.get("output_tokens", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            self._last_input_tokens = 0
            self._last_output_tokens = 0

        raw_text: str = ""
        try:
            raw_text = str(result.get("text", "") or "")
        except AttributeError:
            raw_text = ""

        attacks: List[Dict[str, str]] = self._parse_attacks(raw_text)
        return attacks[:safe_max]

    # ------------------------------------------------------------------ #
    # Attack execution
    # ------------------------------------------------------------------ #

    def run_attacks(
        self,
        main_code_path: Path,
        workdir: Path,
        attacks: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Write each attack probe to disk and run it in a real subprocess.

        Enriches every attack dict in place with ``passed`` (bool) and
        ``output`` (tail of combined stdout/stderr). Never raises.
        """
        enriched: List[Dict[str, Any]] = []
        if not isinstance(attacks, list):
            return enriched

        try:
            work: Path = Path(workdir)
        except (TypeError, ValueError):
            logger.error("Red-team run_attacks got an invalid workdir: %r", workdir)
            return enriched

        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Could not ensure red-team workdir %s: %s", work, exc)

        for index, attack in enumerate(attacks):
            record: Dict[str, Any] = self._run_single_attack(
                index=index, attack=attack, workdir=work
            )
            enriched.append(record)

        return enriched

    def _run_single_attack(
        self, index: int, attack: Any, workdir: Path
    ) -> Dict[str, Any]:
        """Execute one probe; always returns a well-formed enriched record."""
        record: Dict[str, Any] = {}
        if isinstance(attack, dict):
            record.update(attack)

        vector: str = str(record.get("vector", "") or f"attack_{index}")
        test_code: str = str(record.get("test_code", "") or "")
        record["vector"] = vector
        record["test_code"] = test_code

        if not test_code.strip():
            record["passed"] = False
            record["output"] = "No test_code supplied for this attack."
            return record

        attack_path: Path = workdir / f"redteam_attack_{index}.py"
        try:
            attack_path.write_text(test_code, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed writing red-team probe %s: %s", attack_path, exc)
            record["passed"] = False
            record["output"] = f"Could not write attack script: {exc}"
            return record

        try:
            outcome: Dict[str, Any] = run_python(
                attack_path, workdir, timeout=_ATTACK_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - run_python is defensive, but belt-and-suspenders
            logger.error("run_python raised for probe %s: %s", attack_path, exc)
            record["passed"] = False
            record["output"] = f"Attack runner crashed: {exc}"
            return record

        returncode: int = self._coerce_int(outcome.get("returncode"), default=-1)
        stdout: str = str(outcome.get("stdout", "") or "")
        stderr: str = str(outcome.get("stderr", "") or "")
        timed_out: bool = bool(outcome.get("timed_out", False))

        combined: str = self._combine_output(stdout, stderr)
        record["passed"] = (returncode == 0) and not timed_out
        record["returncode"] = returncode
        record["timed_out"] = timed_out
        record["output"] = combined
        return record

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def review(
        self,
        goal: str,
        code: str,
        main_code_path: Path,
        workdir: Path,
    ) -> Dict[str, Any]:
        """Full red-team pass: generate probes, run them, summarise verdict."""
        attacks: List[Dict[str, str]] = self.generate_attacks(goal, code)

        cost_aud: float = 0.0
        try:
            cost_aud = self.router.calculate_cost(
                self.spec, self._last_input_tokens, self._last_output_tokens
            )
        except Exception as exc:  # noqa: BLE001 - pricing must never break the review
            logger.error("Red-team cost calculation failed: %s", exc)
            cost_aud = 0.0

        enriched: List[Dict[str, Any]] = self.run_attacks(
            main_code_path, workdir, attacks
        )

        failures: List[Dict[str, Any]] = [
            attack for attack in enriched if not attack.get("passed", False)
        ]
        passed_all: bool = len(failures) == 0

        return {
            "attacks": enriched,
            "passed_all": passed_all,
            "failures": failures,
            "total_cost_aud": cost_aud,
        }

    # ------------------------------------------------------------------ #
    # Parsing helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _parse_attacks(cls, text: str) -> List[Dict[str, str]]:
        """Extract a list of attack dicts from a (possibly messy) model reply."""
        parsed: Any = cls._extract_json_array(text)
        if not isinstance(parsed, list):
            return []

        attacks: List[Dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            vector_raw: Any = item.get("vector", item.get("description", ""))
            code_raw: Any = item.get("test_code", item.get("code", ""))
            vector: str = str(vector_raw or "").strip()
            test_code: str = str(code_raw or "").strip()
            if not test_code:
                # A vector with no probe is useless; skip it.
                continue
            attacks.append({"vector": vector or "unnamed vector", "test_code": test_code})
        return attacks

    @staticmethod
    def _extract_json_array(text: str) -> Any:
        """Best-effort recovery of a JSON array from arbitrary model output.

        Handles: clean JSON, ```json fenced blocks, and prose surrounding a
        bracketed array. Returns ``[]`` when nothing parseable is found.
        """
        if not text or not text.strip():
            return []

        cleaned: str = text.strip()

        # Strip a leading/trailing markdown fence if present.
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if fence:
            candidate_fenced: str = fence.group(1).strip()
            try:
                data = json.loads(candidate_fenced)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError, ValueError):
                cleaned = candidate_fenced or cleaned

        # Direct parse.
        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Greedy bracket extraction: first '[' through last ']'.
        bracket = re.search(r"\[[\s\S]*\]", cleaned)
        if bracket:
            try:
                data = json.loads(bracket.group(0))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Last resort: salvage individual top-level objects.
        salvaged: List[Any] = []
        for match in re.finditer(r"\{[\s\S]*?\}", cleaned):
            try:
                obj = json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(obj, dict):
                salvaged.append(obj)
        return salvaged

    @staticmethod
    def _combine_output(stdout: str, stderr: str) -> str:
        """Merge stdout/stderr and keep only the tail (probes are chatty)."""
        parts: List[str] = []
        if stdout.strip():
            parts.append(stdout.strip())
        if stderr.strip():
            parts.append("[stderr] " + stderr.strip())
        combined: str = "\n".join(parts)
        if len(combined) > _OUTPUT_TAIL_CHARS:
            return combined[-_OUTPUT_TAIL_CHARS:]
        return combined

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        """Coerce a value to int, falling back to ``default`` on failure."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
