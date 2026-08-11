"""Repo Wiki generator — repo context -> markdown pages (Knowledge surface).

Pure logic: collect_repo_context() walks the tree, generate_wiki_pages()
makes ONE chat call asking for a JSON page list. WikiStore persists pages
to SQLite (data/wiki.db), one row per (repo, path).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build",
              "__pycache__", ".next", "target", ".pytest_cache"}
_KEY_NAMES = {"readme.md", "package.json", "pyproject.toml", "tauri.conf.json",
              "cargo.toml", "requirements.txt", "index.html", "main.py",
              "app.tsx", "main.tsx"}
_MAX_FILES = 200
_MAX_SNIPPET = 1200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages(
  repo TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
  body_md TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
  refreshed_at REAL, PRIMARY KEY (repo, path));
"""


def collect_repo_context(root: Path, max_files: int = _MAX_FILES) -> Dict[str, Any]:
    """File tree + snippets of key files, skipping vendored dirs."""
    root = Path(root)
    tree: List[str] = []
    snippets: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if len(tree) >= max_files:
            break
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.is_dir():
            continue
        rel_s = str(rel).replace("\\", "/")
        tree.append(rel_s)
        if p.name.lower() in _KEY_NAMES and len(snippets) < 8:
            try:
                snippets[rel_s] = p.read_text(
                    encoding="utf-8", errors="replace")[:_MAX_SNIPPET]
            except OSError:
                pass
    return {"root": str(root), "tree": tree, "snippets": snippets}


_WIKI_PROMPT = (
    "You are writing a concise repository wiki. Using the file tree and key "
    "file excerpts, reply with ONLY JSON: {\"pages\": [{\"path\": \"overview.md\", "
    "\"title\": \"...\", \"body_md\": \"...\"}]}. Produce 2-4 pages: an overview "
    "(architecture, stack, entry points) plus one page per major area you can "
    "see. Markdown bodies, tight and factual, no filler."
)


def generate_wiki_pages(context: Dict[str, Any], chat_fn,
                        model: str = "") -> List[Dict[str, Any]]:
    """One LLM call -> parsed page list. Concatenation (never .format):
    the JSON example carries literal braces."""
    prompt = (_WIKI_PROMPT
              + "\n\nFILE TREE:\n" + "\n".join(context.get("tree", [])[:200])
              + "\n\nKEY FILE EXCERPTS:\n"
              + "\n\n".join(f"### {k}\n{v}"
                            for k, v in (context.get("snippets") or {}).items()))
    reply = chat_fn([{"role": "user", "content": prompt}], max_tokens=3000)
    text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    pages: List[Dict[str, Any]] = []
    for pg in (data.get("pages") or [])[:8]:
        path = str(pg.get("path", "")).strip()
        if not path:
            continue
        pages.append({"path": path, "title": str(pg.get("title", path)),
                      "body_md": str(pg.get("body_md", "")), "model": model})
    return pages


class WikiStore:
    """SQLite home of generated wiki pages, keyed by (repo, path)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def upsert_pages(self, repo: str, pages: List[Dict[str, Any]]) -> int:
        n = 0
        for pg in pages:
            self.conn.execute(
                "INSERT INTO wiki_pages(repo,path,title,body_md,model,refreshed_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(repo,path) DO UPDATE SET "
                "title=excluded.title, body_md=excluded.body_md, "
                "model=excluded.model, refreshed_at=excluded.refreshed_at",
                (repo, pg["path"], pg["title"], pg["body_md"],
                 pg.get("model", ""), time.time()))
            n += 1
        self.conn.commit()
        return n

    def list_pages(self, repo: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT repo, path, title, model, refreshed_at FROM wiki_pages "
            "WHERE repo=? ORDER BY path", (repo,)).fetchall()
        return [dict(r) for r in rows]

    def get_page(self, repo: str, path: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM wiki_pages WHERE repo=? AND path=?",
            (repo, path)).fetchone()
        return dict(row) if row else None

    def repos(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT repo FROM wiki_pages ORDER BY repo").fetchall()
        return [r["repo"] for r in rows]
