"""Tests for core.repo_context — deterministic, no network, no credits."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.repo_context import (  # noqa: E402
    build_repo_map,
    get_repo_map,
    search_repo_map,
)


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "app.py").write_text(
        "class Greeter:\n    pass\n\ndef hello():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "sub" / "util.ts").write_text(
        "export function computeArea(w: number): number { return w * w; }\n",
        encoding="utf-8",
    )
    (root / "notes.md").write_text("# notes\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    (root / "node_modules" / "junk.js").parent.mkdir(parents=True)
    (root / "node_modules" / "junk.js").write_text("var x = 1;\n", encoding="utf-8")
    return root


def test_build_map_collects_code_and_text(tmp_path):
    repo_map = build_repo_map(_make_repo(tmp_path))
    paths = [f["path"] for f in repo_map["files"]]
    assert "app.py" in paths
    assert "sub/util.ts" in paths
    assert "notes.md" in paths
    assert "blob.bin" not in paths  # non-text suffix skipped
    assert all("node_modules" not in p for p in paths)  # skipped dirs
    assert repo_map["file_count"] == len(repo_map["files"])


def test_python_symbols_extracted(tmp_path):
    repo_map = build_repo_map(_make_repo(tmp_path))
    app = next(f for f in repo_map["files"] if f["path"] == "app.py")
    assert "class Greeter" in app["symbols"]
    assert "def hello" in app["symbols"]


def test_js_symbols_extracted(tmp_path):
    repo_map = build_repo_map(_make_repo(tmp_path))
    util = next(f for f in repo_map["files"] if f["path"] == "sub/util.ts")
    assert any("computeArea" in s for s in util["symbols"])


def test_cache_roundtrip_and_invalidation(tmp_path):
    root = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    first = get_repo_map(root, data_dir)
    assert first["cached"] is False
    assert (data_dir / "repo_map.json").is_file()
    second = get_repo_map(root, data_dir)
    assert second["cached"] is True
    # Touching a top-level source invalidates the cache.
    (root / "app.py").write_text("def changed():\n    pass\n", encoding="utf-8")
    import time as _t
    _t.sleep(0.02)
    third = get_repo_map(root, data_dir)
    assert third["cached"] is False
    forced = get_repo_map(root, data_dir, force=True)
    assert forced["cached"] is False


def test_cache_scoped_to_root(tmp_path):
    root = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    get_repo_map(root, data_dir)
    other = tmp_path / "elsewhere"
    other.mkdir()
    moved = get_repo_map(other, data_dir)
    assert moved["cached"] is False
    assert moved["root"] == str(other.resolve())


def test_search_scores_paths_and_symbols(tmp_path):
    repo_map = build_repo_map(_make_repo(tmp_path))
    hits = search_repo_map(repo_map, "hello greeter")
    assert hits, "expected at least one hit"
    assert hits[0]["path"] == "app.py"  # matches both needles
    assert search_repo_map(repo_map, "") == []
    assert search_repo_map(repo_map, "zzz-nothing") == []


def test_search_caps_results(tmp_path):
    repo_map = build_repo_map(_make_repo(tmp_path))
    hits = search_repo_map(repo_map, "a")
    assert len(hits) <= 20
