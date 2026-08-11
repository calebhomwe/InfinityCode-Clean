"""Tests for the Long Task path jail and toolset."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.tools import (  # noqa: E402
    PathJail, ToolError, t_edit_file, t_glob, t_grep, t_list_dir,
    t_read_file, t_run_command, t_write_file,
)


@pytest.fixture
def jail(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('hello')\nprint('world')\n")
    (tmp_path / "README.md").write_text("# T\n")
    return PathJail(tmp_path)


def test_jail_blocks_escape(jail):
    with pytest.raises(ToolError):
        jail.resolve("../outside.txt")
    with pytest.raises(ToolError):
        jail.resolve("C:/Windows/system32/cmd.exe")


def test_read_file(jail):
    assert "hello" in t_read_file(jail, "src/a.py")


def test_read_missing_raises(jail):
    with pytest.raises(ToolError):
        t_read_file(jail, "nope.txt")


def test_list_dir(jail):
    names = [e["name"] for e in t_list_dir(jail, ".")]
    assert "src" in names and "README.md" in names


def test_grep(jail):
    hits = t_grep(jail, "hello")
    assert any("src/a.py" in h for h in hits)


def test_glob(jail):
    assert t_glob(jail, "**/*.py") == ["src/a.py"]


def test_write_and_edit(jail):
    t_write_file(jail, "new.txt", "alpha beta")
    assert t_read_file(jail, "new.txt") == "alpha beta"
    summary = t_edit_file(jail, "new.txt", "alpha", "gamma")
    assert "1" in summary
    assert t_read_file(jail, "new.txt") == "gamma beta"


def test_edit_requires_unique_match(jail):
    t_write_file(jail, "dup.txt", "x x")
    with pytest.raises(ToolError):
        t_edit_file(jail, "dup.txt", "x", "y")  # ambiguous
    with pytest.raises(ToolError):
        t_edit_file(jail, "dup.txt", "zz", "y")  # not found


def test_write_blocked_outside(jail):
    with pytest.raises(ToolError):
        t_write_file(jail, "../evil.txt", "x")


def test_run_command(jail):
    res = t_run_command(jail, 'python -c "print(1+1)"')
    assert res["exit_code"] == 0 and "2" in res["stdout"]


def test_run_command_timeout(jail):
    res = t_run_command(jail, 'python -c "import time; time.sleep(5)"', timeout=1)
    assert res["exit_code"] == -1 and "timeout" in res["stderr"].lower()
