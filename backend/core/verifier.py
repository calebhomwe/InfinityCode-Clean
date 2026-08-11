"""Deterministic verification stage for the LoopEngine.

Runs generated code through syntax, lint, and execution checks before it is
considered "done". For Blender scripts, renders the output and optionally asks
a vision model to compare the render against the original intent.
"""

from __future__ import annotations

import ast
import base64
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from backend.core.art_bridge import BlenderBridge, BlenderBridgeError
    from backend.core.exec_utils import extract_code, run_python
    from backend.core.router import COUNCIL
except ImportError:  # running with backend/ as the working directory
    from core.art_bridge import BlenderBridge, BlenderBridgeError  # type: ignore
    from core.exec_utils import extract_code, run_python  # type: ignore
    from core.router import COUNCIL  # type: ignore[no-redef]

logger = logging.getLogger("infinity.verifier")

DEFAULT_TIMEOUT: int = 60


@dataclass
class VerificationResult:
    ok: bool
    stage: str  # syntax, lint, tests, render, vision, skipped
    stdout: str = ""
    stderr: str = ""
    reason: str = ""
    cost_usd: float = 0.0
    output_path: Optional[Path] = None


class Verifier:
    """Evidence-first verifier: code isn't done until it actually runs."""

    def __init__(
        self,
        blender_executable: Optional[str] = None,
        vision_client: Any = None,
        vision_model: Optional[str] = None,
    ) -> None:
        self.blender_bridge = BlenderBridge(blender_executable)
        self.vision_client = vision_client
        # Vision judging goes through the council's designated eye role so a
        # config override actually moves the judge; the old hardcoded slug
        # silently kept billing the wrong model.
        self.vision_model = vision_model or COUNCIL["eye"].id

    # ------------------------------------------------------------------ #
    # Python checks
    # ------------------------------------------------------------------ #

    @staticmethod
    def python_syntax(code: str) -> tuple[bool, str]:
        """Return (ok, reason)."""
        cleaned = extract_code(code)
        if not cleaned:
            return False, "no code found"
        try:
            ast.parse(cleaned)
            return True, "syntax ok"
        except SyntaxError as exc:
            return False, f"syntax error: {exc}"

    def python_lint(self, code: str) -> tuple[bool, str]:
        """Run ruff or flake8 if available. Returns (ok, reason)."""
        cleaned = extract_code(code)
        for linter, args in (("ruff", ["check", "-"]), ("flake8", ["-"])):
            exe = shutil.which(linter)
            if not exe:
                continue
            try:
                result = subprocess.run(
                    [exe, *args],
                    input=cleaned,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode == 0:
                    return True, f"{linter} passed"
                stderr = (result.stderr or "")[-800:]
                stdout = (result.stdout or "")[-800:]
                return False, f"{linter} failed:\n{stdout}\n{stderr}"
            except subprocess.TimeoutExpired:
                return False, f"{linter} timed out"
            except OSError as exc:
                return False, f"{linter} could not run: {exc}"
        return True, "no linter installed; skipped"

    def run_python(
        self,
        code: str,
        test_code: Optional[str] = None,
        workdir: Optional[Path] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[bool, str, str, str]:
        """Execute code (with optional test_code appended) in a subprocess.

        Returns (ok, stdout, stderr, reason).
        """
        cleaned = extract_code(code)
        if not cleaned:
            return False, "", "", "no code found"
        source = cleaned
        if test_code:
            source += "\n\n# --- tests ---\n" + test_code
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "generated.py"
            code_path.write_text(source, encoding="utf-8")
            result = run_python(code_path, workdir or Path(tmpdir), timeout=timeout)
            ok = result["returncode"] == 0 and not result["timed_out"]
            reason = ""
            if result["timed_out"]:
                reason = f"timed out after {timeout}s"
            elif result["returncode"] != 0:
                reason = f"exit code {result['returncode']}"
            return ok, result["stdout"], result["stderr"], reason

    def verify_code(
        self,
        code: str,
        test_code: Optional[str] = None,
        run: bool = True,
        lint: bool = True,
    ) -> VerificationResult:
        """Run syntax → lint → execution checks, stopping on first failure."""
        ok, reason = self.python_syntax(code)
        if not ok:
            return VerificationResult(ok=False, stage="syntax", reason=reason)

        if lint:
            ok, reason = self.python_lint(code)
            if not ok:
                return VerificationResult(ok=False, stage="lint", reason=reason)

        if run:
            ok, stdout, stderr, reason = self.run_python(code, test_code=test_code)
            return VerificationResult(
                ok=ok,
                stage="tests",
                stdout=stdout,
                stderr=stderr,
                reason=reason or "execution passed",
            )

        return VerificationResult(ok=True, stage="syntax", reason="syntax ok (run disabled)")

    # ------------------------------------------------------------------ #
    # Blender checks
    # ------------------------------------------------------------------ #

    def verify_blender_script(
        self,
        script: str,
        output_image: Optional[Path] = None,
    ) -> VerificationResult:
        """Run a Blender script and verify it produces an output image."""
        cleaned = extract_code(script)
        if not cleaned:
            return VerificationResult(ok=False, stage="syntax", reason="no script found")
        out_path = output_image or Path(tempfile.gettempdir()) / "infinity_verify_render.png"
        try:
            self.blender_bridge.run_script(cleaned, out_path)
            return VerificationResult(
                ok=True,
                stage="render",
                reason=f"render saved to {out_path}",
                output_path=out_path,
            )
        except BlenderBridgeError as exc:
            return VerificationResult(ok=False, stage="render", reason=str(exc))

    def render_feedback(
        self,
        script: str,
        intent: str,
        output_image: Optional[Path] = None,
    ) -> VerificationResult:
        """Render a Blender script and ask the vision model to critique it."""
        render_result = self.verify_blender_script(script, output_image)
        if not render_result.ok:
            return render_result
        if self.vision_client is None:
            return VerificationResult(
                ok=True,
                stage="render",
                reason=render_result.reason + " (no vision client configured)",
                output_path=render_result.output_path,
            )

        image_path = render_result.output_path
        if image_path is None:
            return VerificationResult(ok=False, stage="vision", reason="no render path")

        try:
            # Encoding a local render must not require importing the cloud
            # client (or its optional OpenAI dependency).
            raw = image_path.read_bytes()
            if len(raw) > 1_000_000:
                try:
                    from backend.tools.openrouter_client import downscale_image_bytes
                except ImportError:  # running with backend/ as the working directory
                    from tools.openrouter_client import downscale_image_bytes  # type: ignore[no-redef]
                raw = downscale_image_bytes(raw)
            b64 = base64.b64encode(raw).decode("ascii")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            f"Original intent: {intent}\n\n"
                            "Critique the rendered image. List exactly one concrete mismatch "
                            "if the render does not match the intent, or say 'matches intent' "
                            "if it does."
                        )},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ]
            result = self.vision_client.chat_with_vision(
                model_id=self.vision_model,
                messages_with_images=messages,
                max_tokens=800,
            )
            critique = str(result.get("text") if isinstance(result, dict) else getattr(result, "text", ""))
            ok = "matches intent" in critique.lower()
            return VerificationResult(
                ok=ok,
                stage="vision",
                reason=critique,
                output_path=image_path,
            )
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                ok=False,
                stage="vision",
                reason=f"vision critique failed: {exc}",
                output_path=image_path,
            )
