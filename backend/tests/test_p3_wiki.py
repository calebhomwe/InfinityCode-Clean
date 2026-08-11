"""P3: wiki generator + store tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.wiki_gen import (  # noqa: E402
    WikiStore, collect_repo_context, generate_wiki_pages)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "README.md").write_text("# Proj\nA test project.")
    (r / "package.json").write_text('{"name": "proj"}')
    (r / "src").mkdir()
    (r / "src" / "app.tsx").write_text("export default function App() {}")
    (r / "node_modules").mkdir()
    (r / "node_modules" / "junk.js").write_text("x")
    (r / ".git").mkdir()
    (r / ".git" / "HEAD").write_text("ref")
    return r


def test_collect_context_skips_vendored(repo):
    ctx = collect_repo_context(repo)
    assert "README.md" in ctx["tree"]
    assert "src/app.tsx" in ctx["tree"]
    assert not any("node_modules" in t for t in ctx["tree"])
    assert not any(t.startswith(".git") for t in ctx["tree"])
    assert "README.md" in ctx["snippets"]
    assert "A test project" in ctx["snippets"]["README.md"]


def test_generate_wiki_pages_parses_wrapped_json(repo):
    def chat_fn(messages, max_tokens=3000):
        return {"text": ('Here you go: {"pages": [{"path": "overview.md", '
                         '"title": "Overview", "body_md": "# O"}, '
                         '{"path": "frontend.md", "title": "FE", '
                         '"body_md": "# F"}, {"path": "", "title": "skip", '
                         '"body_md": "x"}]} done'),
                "cost_usd": 0.001}

    pages = generate_wiki_pages(collect_repo_context(repo), chat_fn,
                                model="fake/model")
    assert [p["path"] for p in pages] == ["overview.md", "frontend.md"]
    assert pages[0]["title"] == "Overview" and pages[0]["model"] == "fake/model"


def test_generate_wiki_pages_bad_json_raises(repo):
    def chat_fn(messages, max_tokens=3000):
        return {"text": "no json here at all", "cost_usd": 0.001}

    with pytest.raises(Exception):
        generate_wiki_pages(collect_repo_context(repo), chat_fn)


def test_wiki_store_upsert_and_get(tmp_path):
    s = WikiStore(tmp_path / "wiki.db")
    n = s.upsert_pages("/r1", [{"path": "overview.md", "title": "O",
                                "body_md": "# one", "model": "m"}])
    assert n == 1
    # Upsert overwrites same (repo, path).
    s.upsert_pages("/r1", [{"path": "overview.md", "title": "O2",
                            "body_md": "# two", "model": "m"}])
    pages = s.list_pages("/r1")
    assert len(pages) == 1 and pages[0]["title"] == "O2"
    full = s.get_page("/r1", "overview.md")
    assert full is not None and full["body_md"] == "# two"
    assert s.get_page("/r1", "nope.md") is None
    assert s.repos() == ["/r1"]
