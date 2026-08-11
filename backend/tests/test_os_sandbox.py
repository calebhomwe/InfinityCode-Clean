"""OS-level sandbox escape tests (Stage 2.1).

Every "must fail" case asserts the hardened child COULD NOT do the thing.
Requires Windows + pywin32; skipped elsewhere.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core import os_sandbox  # noqa: E402
from backend.core.sandbox import run_sandboxed  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os_sandbox.can_harden(),
    reason="OS hardening requires Windows + pywin32",
)


def _write(tmp_path: Path, code: str) -> Path:
    p = tmp_path / "probe.py"
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return p


def test_hardened_child_can_write_to_workdir(tmp_path):
    """The labeled scratch dir remains writable for generated code."""
    script = _write(tmp_path, """
        from pathlib import Path
        Path("output.txt").write_text("hello", encoding="utf-8")
        print("ok")
    """)
    res = run_sandboxed(sys.executable, script, tmp_path, timeout=30)
    assert res["returncode"] == 0, res
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "hello"
    assert res.get("hardened") is True


def test_hardened_child_cannot_write_to_documents(tmp_path):
    """Low integrity must block writes to the medium-integrity profile."""
    docs = Path(os.environ.get("USERPROFILE", ".")) / "Documents"
    target = docs / "infinity_sandbox_escape_probe.txt"
    if target.exists():
        target.unlink()
    script = _write(tmp_path, f"""
        from pathlib import Path
        p = Path(r"{target}")
        p.write_text("escaped", encoding="utf-8")
        print("BAD: wrote", p)
    """)
    res = run_sandboxed(sys.executable, script, tmp_path, timeout=30)
    assert res["returncode"] != 0, res
    assert not target.exists(), "SANDBOX ESCAPE: write to Documents succeeded"
    assert res.get("hardened") is True


def test_hardened_child_cannot_write_to_userprofile_root(tmp_path):
    target = Path(os.environ.get("USERPROFILE", ".")) / "infinity_sandbox_probe2.txt"
    if target.exists():
        target.unlink()
    script = _write(tmp_path, f"""
        from pathlib import Path
        Path(r"{target}").write_text("escaped", encoding="utf-8")
    """)
    res = run_sandboxed(sys.executable, script, tmp_path, timeout=30)
    assert res["returncode"] != 0, res
    assert not target.exists(), "SANDBOX ESCAPE: write to profile succeeded"


def test_hardened_timeout_kills_child(tmp_path):
    script = _write(tmp_path, """
        import time
        time.sleep(30)
        print("never")
    """)
    res = run_sandboxed(sys.executable, script, tmp_path, timeout=3)
    assert res["timed_out"] is True
    assert res["returncode"] == -1


def test_hardened_child_sees_scrubbed_env(tmp_path):
    """API keys must not leak into the sandboxed environment."""
    os.environ["INFINITY_TEST_SECRET"] = "should-not-leak"
    try:
        script = _write(tmp_path, """
            import os
            print(os.environ.get("INFINITY_TEST_SECRET", "<absent>"))
        """)
        res = run_sandboxed(sys.executable, script, tmp_path, timeout=30)
        assert res["returncode"] == 0, res
        assert "should-not-leak" not in res["stdout"]
    finally:
        del os.environ["INFINITY_TEST_SECRET"]


def test_fallback_runs_when_hardening_skipped(tmp_path, monkeypatch):
    """can_harden()==False must still execute code via the plain path."""
    import backend.core.sandbox as sb
    monkeypatch.setattr(
        sb, "__name__", sb.__name__)  # keep module identity stable
    import backend.core.os_sandbox as osb
    monkeypatch.setattr(osb, "can_harden", lambda: False)
    script = _write(tmp_path, "print('plain-ok')")
    res = run_sandboxed(sys.executable, script, tmp_path, timeout=30)
    assert res["returncode"] == 0
    assert "plain-ok" in res["stdout"]
