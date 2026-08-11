"""Auto-fix loop for Infinity Code.

Scans the mission database for repeated failure patterns in the last 24 h,
generates a patch via LLM, validates it with `git apply --check`, and either
applies it (if constitution allows auto-approve) or writes it to
`DATA_DIR/patches/` for human review.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.core.constitution import Constitution
    from backend.core.sandbox import SandboxedExecutor, CodeExecutionRequest
except ImportError:  # running with backend/ as the working directory
    from core.constitution import Constitution  # type: ignore[no-redef]
    from core.sandbox import SandboxedExecutor, CodeExecutionRequest  # type: ignore[no-redef]


logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _failure_signature(text: str) -> str:
    """Normalise a failure string into a short signature for grouping."""
    t = text.lower().strip()
    # Strip file paths, line numbers, UUIDs, timestamps
    t = re.sub(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", "<uuid>", t)
    t = re.sub(r"/[\w/.-]+\.(py|yaml|json|txt)", "<file>", t)
    t = re.sub(r"line \d+", "line <n>", t)
    t = re.sub(r"code \d+", "code <n>", t)
    t = re.sub(r"\s+", " ", t)
    return t[:160]


def _today_count(patches_dir: Path) -> int:
    """How many patches already written today?"""
    if not patches_dir.exists():
        return 0
    today = time.strftime("%Y-%m-%d")
    return sum(1 for f in patches_dir.iterdir() if f.name.startswith(today))


def _today_cost(evolution_log: Path) -> float:
    """Rough AUD spent today by self-improvement (evolution + auto-fix)."""
    if not evolution_log.exists():
        return 0.0
    today = time.strftime("%Y-%m-%d")
    total = 0.0
    with evolution_log.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("date", "").startswith(today):
                total += float(entry.get("cost_aud", 0.0))
    return total


# --------------------------------------------------------------------------- #
# AutoFix
# --------------------------------------------------------------------------- #

class AutoFix:
    """Repeated-failure detector + constitution-gated patch generator."""

    def __init__(
        self,
        db_path: Path,
        data_dir: Path,
        constitution: Constitution,
        client: Any,
        model: str = "dashscope/qwen-coder-plus",
    ) -> None:
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.constitution = constitution
        self.client = client
        self.model = model
        self.patches_dir = self.data_dir / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.evolution_log = self.data_dir / "evolution_log.jsonl"
        self.sandbox = SandboxedExecutor(self.data_dir / "sandbox_auto_fix")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def check(self) -> Dict[str, Any]:
        """Entry-point called by the scheduler every hour."""
        result: Dict[str, Any] = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "patterns_found": 0,
            "patches_generated": 0,
            "patches_applied": 0,
            "errors": [],
        }

        # Budget guards
        if self.constitution.daily_patch_limit_exceeded(_today_count(self.patches_dir)):
            result["errors"].append("Daily patch limit exceeded.")
            return result
        if self.constitution.daily_budget_exceeded(_today_cost(self.evolution_log)):
            result["errors"].append("Daily self-improve budget exceeded.")
            return result

        # 1. Find repeated failure patterns
        patterns = self._find_repeated_patterns(hours=24, min_repeats=3)
        result["patterns_found"] = len(patterns)
        if not patterns:
            return result

        for pattern, missions in patterns:
            try:
                patch_info = self._generate_and_validate_patch(pattern, missions)
            except Exception as exc:  # noqa: BLE001
                logger.exception("AutoFix patch generation failed for pattern: %s", pattern[:80])
                result["errors"].append(str(exc))
                continue

            if patch_info is None:
                continue

            result["patches_generated"] += 1

            if self.constitution.auto_approve and patch_info["check_ok"]:
                applied = self._apply_patch(patch_info["diff_path"])
                if applied:
                    result["patches_applied"] += 1
                    self._log_evolution("auto_fix_applied", pattern, patch_info)
                else:
                    result["errors"].append("Patch failed git apply --check unexpectedly.")
            else:
                # Leave in patches_dir for human review
                self._log_evolution("auto_fix_pending", pattern, patch_info)

            # Hard stop after one patch per check cycle to stay under budget.
            break

        return result

    # ------------------------------------------------------------------ #
    # Failure mining
    # ------------------------------------------------------------------ #

    def _find_repeated_patterns(
        self, hours: int = 24, min_repeats: int = 3
    ) -> List[Tuple[str, List[Dict[str, Any]]]]:
        """Return [(failure_signature, [mission_rows, ...]), ...] sorted by repeat count."""
        since = time.time() - (hours * 3600)
        missions: List[Dict[str, Any]] = []
        try:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, goal, evidence_json, completed_at
                    FROM missions
                    WHERE status = 'failed'
                      AND evidence_json IS NOT NULL
                      AND completed_at >= datetime(?, 'unixepoch')
                    ORDER BY completed_at DESC
                    """,
                    (since,),
                ).fetchall()
                missions = [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.error("AutoFix DB query failed: %s", exc)
            return []

        sig_map: Dict[str, List[Dict[str, Any]]] = {}
        for m in missions:
            evidence = self._parse_evidence(m.get("evidence_json", "{}"))
            # Use top-level failure_reason if present, else aggregate attempt details
            failure_text = evidence.get("failure_reason", "")
            if not failure_text and "attempts" in evidence:
                failure_text = " | ".join(
                    str(a.get("failure_detail", ""))
                    for a in evidence["attempts"]
                    if a.get("failure_detail")
                )
            if not failure_text:
                continue
            sig = _failure_signature(failure_text)
            if not sig:
                continue
            sig_map.setdefault(sig, []).append(m)

        # Return only signatures that hit the repeat threshold
        repeated = [
            (sig, ms) for sig, ms in sig_map.items() if len(ms) >= min_repeats
        ]
        repeated.sort(key=lambda x: len(x[1]), reverse=True)
        return repeated

    @staticmethod
    def _parse_evidence(raw: str) -> Dict[str, Any]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ #
    # Patch generation
    # ------------------------------------------------------------------ #

    def _generate_and_validate_patch(
        self, pattern: str, missions: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Ask the LLM for a fix, validate with git apply --check, return patch metadata."""
        # Gather file paths that are likely culprits from the swarm traceback
        candidate_files: List[str] = []
        for m in missions:
            evidence = self._parse_evidence(m.get("evidence_json", "{}"))
            for att in evidence.get("attempts", []):
                stderr = str(att.get("stderr", ""))
                # Extract python file paths from stderr
                for match in re.finditer(r'"(/[^"]+\.py)"', stderr):
                    p = match.group(1)
                    if self.constitution.path_is_allowed(p):
                        candidate_files.append(p)

        # De-duplicate while preserving order
        seen: set = set()
        candidate_files = [f for f in candidate_files if not (f in seen or seen.add(f))]

        # Fallback: if no file identified, target backend/core/swarm.py as the most common source of orchestration bugs
        if not candidate_files:
            candidate_files = ["backend/core/swarm.py"]

        # Build the prompt
        system_prompt = (
            "You are the Infinity Code self-healing engineer. "
            "You receive a repeated failure pattern and must output a unified diff "
            "that fixes the root cause. Only touch files under backend/core/ or skills/. "
            "Never modify config.yaml, .env, or main.py. "
            "Output ONLY the diff in standard `diff -u` format inside a ```diff code block."
        )
        user_prompt = self._build_fix_prompt(pattern, missions[:5], candidate_files[:3])

        # Call LLM with up to 3 retry cycles feeding stderr back
        diff_text = ""
        last_stderr = ""
        for attempt in range(3):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if last_stderr:
                messages.append({
                    "role": "user",
                    "content": f"The previous diff failed validation. Error:\n{last_stderr}\n\nPlease rewrite the diff.",
                })
            try:
                raw = self.client.chat(self.model, messages, max_tokens=4000)
                text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AutoFix LLM call failed (attempt %d): %s", attempt + 1, exc)
                continue

            diff_text = self._extract_diff(text)
            if not diff_text:
                last_stderr = "No diff block found in response."
                continue

            check_ok, stderr = self._git_apply_check(diff_text)
            if check_ok:
                break
            last_stderr = stderr
        else:
            logger.warning("AutoFix could not produce a passing diff after 3 attempts.")
            return None

        # Persist diff
        patch_name = f"{time.strftime('%Y-%m-%d')}_autofix_{hashlib.sha256(pattern.encode()).hexdigest()[:8]}.diff"
        diff_path = self.patches_dir / patch_name
        diff_path.write_text(diff_text, encoding="utf-8")

        return {
            "diff_path": str(diff_path),
            "check_ok": check_ok,
            "pattern": pattern,
            "candidate_files": candidate_files,
        }

    @staticmethod
    def _build_fix_prompt(
        pattern: str, missions: List[Dict[str, Any]], candidate_files: List[str]
    ) -> str:
        lines = [
            "## Repeated Failure Pattern",
            f"Signature: {pattern}",
            f"Occurrences: {len(missions)} missions in last 24h",
            "",
            "## Affected Missions (sample)",
        ]
        for m in missions:
            lines.append(f"- {m.get('id')}: {m.get('goal', '')[:200]}")
        lines += [
            "",
            "## Likely Files to Modify",
        ]
        for f in candidate_files:
            lines.append(f"- {f}")
        lines += [
            "",
            "## Instructions",
            "1. Diagnose the root cause from the failure signature.",
            "2. Produce a minimal, correct unified diff.",
            "3. Do NOT add new dependencies.",
            "4. Do NOT modify test assertions unless the test itself is wrong.",
            "",
            "```diff",
            "--- a/...",
            "+++ b/...",
            "```",
        ]
        return "\n".join(lines)

    @staticmethod
    def _extract_diff(text: str) -> str:
        """Pull out the first ```diff ... ``` block."""
        m = re.search(r"```diff\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Fallback: look for raw diff headers
        start = text.find("--- a/")
        if start != -1:
            return text[start:].strip()
        return ""

    def _git_apply_check(self, diff_text: str) -> Tuple[bool, str]:
        """Run `git apply --check` on the diff. Returns (ok, stderr)."""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".diff", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(diff_text)
                tmp_path = tmp.name
            result = subprocess.run(
                ["git", "apply", "--check", tmp_path],
                capture_output=True,
                text=True,
                cwd=self.data_dir.parent,  # repo root
            )
            return result.returncode == 0, result.stderr
        except FileNotFoundError:
            return False, "git not found on PATH"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _apply_patch(self, diff_path: str) -> bool:
        """Actually apply the diff to the working tree."""
        try:
            result = subprocess.run(
                ["git", "apply", diff_path],
                capture_output=True,
                text=True,
                cwd=self.data_dir.parent,
            )
            if result.returncode == 0:
                logger.info("AutoFix applied patch: %s", diff_path)
                return True
            logger.error("AutoFix git apply failed: %s", result.stderr)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("AutoFix apply exception: %s", exc)
            return False

    def _log_evolution(self, event: str, pattern: str, patch_info: Dict[str, Any]) -> None:
        entry = {
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "pattern_signature": pattern[:120],
            "diff_path": patch_info.get("diff_path"),
            "cost_aud": 0.0,  # placeholder; LLM cost not tracked here yet
        }
        with self.evolution_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


__all__ = ["AutoFix"]
