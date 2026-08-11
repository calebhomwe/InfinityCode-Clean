# Long-Horizon Agent — Phase 1 (Engine Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the backend core of the Long Task agent — a tool-calling loop that plans, edits a real repo, journals every step, and is drivable over HTTP — with a scripted fake-model test harness.

**Architecture:** New focused package `backend/core/longtask/` (tools / journal / protocol / engine). The engine calls a pluggable `builder` chat client that returns JSON action blocks; tools are jailed to the target repo; every step is journaled to SQLite so later phases can resume. Review gate, checkpoints, UI, and self-learning are P2–P4 per the spec.

**Tech Stack:** Python 3.x, FastAPI (existing `backend/main.py` app), SQLite, pytest (existing `backend/tests/`), no new dependencies.

Spec: `docs/superpowers/specs/2026-08-05-long-horizon-coder-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `backend/core/longtask/__init__.py` | Package exports |
| `backend/core/longtask/tools.py` | Path jail + the 8 tools (`read_file`, `list_dir`, `grep`, `glob`, `write_file`, `edit_file`, `run_command`, `finish` via engine) |
| `backend/core/longtask/journal.py` | SQLite `longtasks` + `longtask_steps` tables, CRUD |
| `backend/core/longtask/protocol.py` | Parse model output into actions (JSON block protocol, defensive) |
| `backend/core/longtask/engine.py` | `LongTaskEngine` loop: plan → act → journal → finish/budget |
| `backend/main.py` (modify) | `POST/GET /api/v1/longtasks`, `GET /api/v1/longtasks/{id}`, cancel |
| `backend/config.yaml` (modify) | `longtask_builder` / `longtask_reviewer` model keys |
| `backend/tests/test_longtask_tools.py` | Tool + jail tests |
| `backend/tests/test_longtask_journal.py` | Journal tests |
| `backend/tests/test_longtask_protocol.py` | Action parser tests |
| `backend/tests/test_longtask_engine.py` | End-to-end with `FakeBuilder` over a scratch git repo |

Builder protocol (duck-typed, matches how `LoopEngine` uses clients):
`builder.chat(messages: list[dict], max_tokens: int) -> dict` returning
`{"text": str, "cost_usd": float}`. `FakeBuilder` implements the same.

---

### Task 1: Path jail + read-only tools

**Files:**
- Create: `backend/core/longtask/__init__.py`
- Create: `backend/core/longtask/tools.py`
- Test: `backend/tests/test_longtask_tools.py`

- [ ] **Step 1: Write failing tests** (`backend/tests/test_longtask_tools.py`)

```python
import pytest
from pathlib import Path
from core.longtask.tools import PathJail, ToolError, t_read_file, t_list_dir, t_grep, t_glob

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
    assert t_glob(jail, "**/*.py") == ["src/a.py"] or t_glob(jail, "**/*.py") == ["src\\a.py"]
```

- [ ] **Step 2: Run to verify failure** — `cd C:\Users\caleb\infinity-code\backend ; ..\venv\Scripts\python.exe -m pytest tests/test_longtask_tools.py -v` → ImportError/ModuleNotFoundError.

- [ ] **Step 3: Implement** `tools.py`:

```python
"""Long Task toolset — all file access jailed to the target repo."""
from __future__ import annotations
import fnmatch, re
from pathlib import Path
from typing import Any, Dict, List

MAX_READ_BYTES = 200_000
MAX_CMD_OUTPUT = 64_000

class ToolError(Exception):
    pass

class PathJail:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ToolError(f"repo root does not exist: {root}")

    def resolve(self, p: str) -> Path:
        cand = Path(p)
        cand = cand if cand.is_absolute() else self.root / cand
        cand = cand.resolve()
        try:
            cand.relative_to(self.root)
        except ValueError:
            raise ToolError(f"path escapes repo: {p}")
        return cand

def t_read_file(jail: PathJail, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
    f = jail.resolve(path)
    if not f.is_file():
        raise ToolError(f"no such file: {path}")
    data = f.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")

def t_list_dir(jail: PathJail, path: str = ".") -> List[Dict[str, Any]]:
    d = jail.resolve(path)
    if not d.is_dir():
        raise ToolError(f"no such dir: {path}")
    out = []
    for e in sorted(d.iterdir()):
        out.append({"name": e.name, "type": "dir" if e.is_dir() else "file"})
    return out[:500]

def t_grep(jail: PathJail, pattern: str, path: str = ".", max_results: int = 50) -> List[str]:
    rx = re.compile(pattern)
    hits: List[str] = []
    base = jail.resolve(path)
    files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()]
    for f in files:
        if len(hits) >= max_results:
            break
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(jail.root)}:{i}: {line[:200]}")
                    if len(hits) >= max_results:
                        break
        except (OSError, UnicodeDecodeError):
            continue
    return hits

def t_glob(jail: PathJail, pattern: str) -> List[str]:
    return sorted(str(p.relative_to(jail.root)).replace("\\", "/")
                  for p in jail.root.glob(pattern))[:500]
```

Plus `__init__.py`:
```python
from .tools import PathJail, ToolError  # noqa: F401
```

- [ ] **Step 4: Run tests** → all PASS.
- [ ] **Step 5: Commit** — `git add backend/core/longtask backend/tests/test_longtask_tools.py ; git commit -m "feat(longtask): path jail + read-only tools"`

---

### Task 2: Mutating tools + run_command

**Files:**
- Modify: `backend/core/longtask/tools.py`
- Test: `backend/tests/test_longtask_tools.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from core.longtask.tools import t_write_file, t_edit_file, t_run_command

def test_write_and_edit(jail):
    t_write_file(jail, "new.txt", "alpha beta")
    assert t_read_file(jail, "new.txt") == "alpha beta"
    summary = t_edit_file(jail, "new.txt", "alpha", "gamma")
    assert "1" in summary  # one replacement
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
    res = t_run_command(jail, "python -c \"print(1+1)\"")
    assert res["exit_code"] == 0 and "2" in res["stdout"]

def test_run_command_timeout(jail):
    res = t_run_command(jail, "python -c \"import time; time.sleep(5)\"", timeout=1)
    assert res["exit_code"] == -1 and "timeout" in res["stderr"].lower()
```

- [ ] **Step 2: Run** → failures (names missing).
- [ ] **Step 3: Implement** (append to `tools.py`):

```python
import subprocess

def t_write_file(jail: PathJail, path: str, content: str) -> str:
    f = jail.resolve(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"

def t_edit_file(jail: PathJail, path: str, old: str, new: str,
                replace_all: bool = False) -> str:
    f = jail.resolve(path)
    if not f.is_file():
        raise ToolError(f"no such file: {path}")
    text = f.read_text(encoding="utf-8")
    n = text.count(old)
    if n == 0:
        raise ToolError("old text not found")
    if n > 1 and not replace_all:
        raise ToolError(f"old text matches {n} places; add context or replace_all")
    f.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1),
                 encoding="utf-8")
    return f"{n if replace_all else 1} replacement(s) in {path}"

def t_run_command(jail: PathJail, command: str, timeout: int = 120) -> Dict[str, Any]:
    try:
        proc = subprocess.run(command, shell=True, cwd=str(jail.root),
                              capture_output=True, text=True, timeout=timeout)
        return {"exit_code": proc.returncode,
                "stdout": proc.stdout[-MAX_CMD_OUTPUT:],
                "stderr": proc.stderr[-MAX_CMD_OUTPUT:]}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
```

NOTE: `run_command` uses `subprocess` with repo cwd in P1; deeper isolation (WSL2/AppContainer per roadmap) is P5 hardening. The jail still bounds file tools, and autonomy gating bounds shell access in P3.

SECURITY ACK (static scan): `shell=True` is deliberate — a coding agent must run shell syntax, same as Claude Code/Codex. Mitigations now: cwd jailed to repo, hard timeout, output truncation, and Ask-mode approval gate in P3. P5 MUST add real isolation (AppContainer/WSL2 jail or at minimum a dangerous-command denylist + no shell metachar passthrough) before Full Access ships.

- [ ] **Step 4: Run tests** → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(longtask): mutating tools + sandboxed run_command"`

---

### Task 3: Journal (SQLite)

**Files:**
- Create: `backend/core/longtask/journal.py`
- Test: `backend/tests/test_longtask_journal.py`

- [ ] **Step 1: Failing test**

```python
from core.longtask.journal import LongTaskJournal

def test_journal_roundtrip(tmp_path):
    j = LongTaskJournal(tmp_path / "lt.db")
    tid = j.create_task(goal="add feature", repo_path=str(tmp_path),
                        model_builder="fake", model_reviewer="fake")
    j.append_step(tid, seq=1, kind="tool", tool="write_file",
                  args={"path": "a.py"}, result={"ok": True},
                  model="fake", cost_aud=0.01, duration_ms=12)
    j.set_status(tid, "completed", result="done")
    t = j.get_task(tid)
    assert t["status"] == "completed" and t["goal"] == "add feature"
    steps = j.steps_for(tid)
    assert steps[0]["tool"] == "write_file" and steps[0]["seq"] == 1
    assert j.list_tasks()[0]["id"] == tid
```

- [ ] **Step 2: Run** → ModuleNotFoundError.
- [ ] **Step 3: Implement** `journal.py`:

```python
"""SQLite journal for Long Tasks — the substrate for resume (P2)."""
from __future__ import annotations
import json, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS longtasks(
  id TEXT PRIMARY KEY, goal TEXT NOT NULL, repo_path TEXT NOT NULL,
  branch TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
  autonomy TEXT NOT NULL DEFAULT 'ask', model_builder TEXT, model_reviewer TEXT,
  budget_json TEXT DEFAULT '{}', cost_aud REAL DEFAULT 0, steps INTEGER DEFAULT 0,
  result TEXT DEFAULT '', created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS longtask_steps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
  kind TEXT NOT NULL, tool TEXT DEFAULT '', args_json TEXT DEFAULT '{}',
  result_json TEXT DEFAULT '{}', model TEXT DEFAULT '', cost_aud REAL DEFAULT 0,
  duration_ms INTEGER DEFAULT 0, ts REAL);
CREATE INDEX IF NOT EXISTS idx_steps_task ON longtask_steps(task_id, seq);
"""

class LongTaskJournal:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def create_task(self, goal: str, repo_path: str, model_builder: str = "",
                    model_reviewer: str = "", budget: Optional[Dict] = None,
                    autonomy: str = "ask") -> str:
        tid = uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute(
            "INSERT INTO longtasks(id,goal,repo_path,status,autonomy,model_builder,"
            "model_reviewer,budget_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, goal, repo_path, "running", autonomy, model_builder,
             model_reviewer, json.dumps(budget or {}), now, now))
        self.conn.commit()
        return tid

    def append_step(self, task_id: str, seq: int, kind: str, tool: str = "",
                    args: Optional[Dict] = None, result: Any = None,
                    model: str = "", cost_aud: float = 0.0,
                    duration_ms: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO longtask_steps(task_id,seq,kind,tool,args_json,result_json,"
            "model,cost_aud,duration_ms,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, seq, kind, tool, json.dumps(args or {}),
             json.dumps(result, default=str), model, cost_aud, duration_ms, time.time()))
        self.conn.execute(
            "UPDATE longtasks SET steps=?, cost_aud=cost_aud+?, updated_at=? WHERE id=?",
            (seq, cost_aud, time.time(), task_id))
        self.conn.commit()

    def set_status(self, task_id: str, status: str, result: str = "") -> None:
        self.conn.execute("UPDATE longtasks SET status=?, result=?, updated_at=? WHERE id=?",
                          (status, result, time.time(), task_id))
        self.conn.commit()

    def get_task(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute("SELECT * FROM longtasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def steps_for(self, task_id: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtask_steps WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_tasks(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(longtask): SQLite journal (tasks + steps)"`

---

### Task 4: Action protocol parser

**Files:**
- Create: `backend/core/longtask/protocol.py`
- Test: `backend/tests/test_longtask_protocol.py`

- [ ] **Step 1: Failing tests**

```python
from core.longtask.protocol import parse_actions, ProtocolError

def test_parses_fenced_json():
    txt = 'Sure.\n```action\n{"action":"read_file","args":{"path":"a.py"}}\n```\n'
    acts = parse_actions(txt)
    assert acts == [{"action": "read_file", "args": {"path": "a.py"}}]

def test_parses_multiple_and_finish():
    txt = ('{"action":"write_file","args":{"path":"b.py","content":"x"}}\n'
           '{"action":"finish","args":{"summary":"done"}}')
    acts = parse_actions(txt)
    assert len(acts) == 2 and acts[1]["action"] == "finish"

def test_parses_bare_json_object():
    acts = parse_actions('{"action":"list_dir","args":{}}')
    assert acts[0]["action"] == "list_dir"

def test_rejects_garbage():
    import pytest
    with pytest.raises(ProtocolError):
        parse_actions("I will now edit the files.")
```

- [ ] **Step 2: Run** → ModuleNotFoundError.
- [ ] **Step 3: Implement** `protocol.py`:

```python
"""Parse builder output into tool actions.

Protocol: one or more JSON objects with {"action": str, "args": {...}},
optionally inside ```action fences. Anything else -> ProtocolError (engine
re-asks once, then fails the step)."""
from __future__ import annotations
import json, re
from typing import Any, Dict, List

KNOWN = {"read_file", "list_dir", "grep", "glob", "write_file", "edit_file",
         "run_command", "update_plan", "finish"}

class ProtocolError(Exception):
    pass

def parse_actions(text: str) -> List[Dict[str, Any]]:
    candidates: List[str] = []
    for m in re.finditer(r"```(?:action|json)?\s*(\{.*?\})\s*```", text, re.S):
        candidates.append(m.group(1))
    if not candidates:
        # bare JSON objects, possibly several per line
        for m in re.finditer(r"\{[^{}]*\"action\"[^{}]*\}", text):
            candidates.append(m.group(0))
    actions: List[Dict[str, Any]] = []
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action") in KNOWN:
            actions.append({"action": obj["action"],
                            "args": obj.get("args") or {}})
    if not actions:
        raise ProtocolError("no valid action block found")
    return actions
```

- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(longtask): defensive action protocol parser"`

---

### Task 5: Engine loop + FakeBuilder harness

**Files:**
- Create: `backend/core/longtask/engine.py`
- Create: `backend/tests/test_longtask_engine.py`
- Modify: `backend/core/longtask/__init__.py` (export engine)

- [ ] **Step 1: Failing end-to-end test**

```python
import pytest
from pathlib import Path
from core.longtask.engine import LongTaskEngine, Budget
from core.longtask.journal import LongTaskJournal

class FakeBuilder:
    """Scripted builder: each chat() pops the next reply."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
    def chat(self, messages, max_tokens=3000, **kw):
        self.calls += 1
        return {"text": self.replies.pop(0), "cost_usd": 0.001}

def script(repo: Path) -> list:
    return [
        # turn 1: plan
        '{"action":"update_plan","args":{"plan":"- [ ] add hello.py"}}',
        # turn 2: write file
        '{"action":"write_file","args":{"path":"hello.py","content":"print(\'hi\')\\n"}}',
        # turn 3: verify it runs
        '{"action":"run_command","args":{"command":"python hello.py"}}',
        # turn 4: finish
        '{"action":"finish","args":{"summary":"added hello.py, runs clean"}}',
    ]

@pytest.fixture
def repo(tmp_path):
    (tmp_path / "base.txt").write_text("x")
    return tmp_path

def test_full_loop(repo, tmp_path):
    b = FakeBuilder(script(repo))
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="add hello.py", repo_path=str(repo), budget=Budget(max_steps=10))
    assert res["status"] == "completed"
    assert (repo / "hello.py").read_text().startswith("print")
    t = j.get_task(res["task_id"])
    assert t["status"] == "completed" and t["steps"] >= 4
    assert any(s["tool"] == "run_command" for s in j.steps_for(res["task_id"]))

def test_step_budget_trips(repo, tmp_path):
    forever = ['{"action":"list_dir","args":{}}'] * 50
    b = FakeBuilder(forever)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="spin", repo_path=str(repo), budget=Budget(max_steps=3))
    assert res["status"] == "budget_exceeded"

def test_malformed_reply_reasks_then_fails_step(repo, tmp_path):
    b = FakeBuilder(["let me think...",
                     '{"action":"finish","args":{"summary":"ok"}}'])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5))
    assert res["status"] == "completed"  # re-ask recovered
```

- [ ] **Step 2: Run** → ModuleNotFoundError.
- [ ] **Step 3: Implement** `engine.py` (core loop; review gate deferred to P2):

```python
"""LongTaskEngine — plan -> act -> journal -> finish/budget. P1 core loop."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .journal import LongTaskJournal
from .protocol import parse_actions, ProtocolError
from .tools import (PathJail, ToolError, t_read_file, t_list_dir, t_grep, t_glob,
                    t_write_file, t_edit_file, t_run_command)

@dataclass
class Budget:
    max_steps: int = 40
    max_cost_aud: float = 2.0
    max_wall_min: int = 120

SYSTEM = (
    "You are Infinity Code's long-horizon coding agent. You work ONLY inside "
    "the given repo. Each reply: a short thought (optional) then one or more "
    "JSON action blocks: {\"action\": <name>, \"args\": {...}}. Actions: "
    "read_file{path}, list_dir{path}, grep{pattern,path}, glob{pattern}, "
    "write_file{path,content}, edit_file{path,old,new,replace_all}, "
    "run_command{command}, update_plan{plan}, finish{summary}. "
    "First action of a task should be update_plan with a checklist. "
    "Never invent file contents you have not read. Call finish when done."
)

class LongTaskEngine:
    def __init__(self, builder: Any, journal: LongTaskJournal,
                 cost_tracker: Any = None, event_cb=None) -> None:
        self.builder = builder
        self.journal = journal
        self.cost_tracker = cost_tracker
        self.event_cb = event_cb  # P3: WS bridge; P1: optional callback

    def _emit(self, task_id: str, kind: str, payload: Dict) -> None:
        if self.event_cb:
            try:
                self.event_cb(task_id, kind, payload)
            except Exception:  # noqa: BLE001
                pass

    def _exec(self, jail: PathJail, action: Dict) -> Dict[str, Any]:
        name, a = action["action"], action.get("args") or {}
        if name == "read_file":
            return {"content": t_read_file(jail, a["path"])}
        if name == "list_dir":
            return {"entries": t_list_dir(jail, a.get("path", "."))}
        if name == "grep":
            return {"hits": t_grep(jail, a["pattern"], a.get("path", "."))}
        if name == "glob":
            return {"matches": t_glob(jail, a["pattern"])}
        if name == "write_file":
            return {"ok": True, "detail": t_write_file(jail, a["path"], a["content"])}
        if name == "edit_file":
            return {"ok": True, "detail": t_edit_file(
                jail, a["path"], a["old"], a["new"], bool(a.get("replace_all")))}
        if name == "run_command":
            return t_run_command(jail, a["command"], int(a.get("timeout", 120)))
        raise ToolError(f"unhandled action: {name}")  # update_plan/finish handled by loop

    def run(self, goal: str, repo_path: str, budget: Budget = Budget(),
            autonomy: str = "ask", model_builder: str = "builder") -> Dict[str, Any]:
        jail = PathJail(Path(repo_path))
        task_id = self.journal.create_task(goal=goal, repo_path=repo_path,
                                           model_builder=model_builder,
                                           budget=budget.__dict__, autonomy=autonomy)
        seq = 0
        start = time.time()
        history: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM + f"\n\nREPO: {jail.root}\nGOAL: {goal}"}]
        plan = ""

        while seq < budget.max_steps * 4:  # actions may exceed turns; cap below
            if time.time() - start > budget.max_wall_min * 60:
                return self._end(task_id, "budget_exceeded", "wall-clock limit")
            t0 = time.time()
            try:
                reply = self.builder.chat(history, max_tokens=3000)
            except Exception as exc:  # noqa: BLE001
                return self._end(task_id, "error", f"builder failed: {exc}")
            text = reply["text"] if isinstance(reply, dict) else str(reply)
            cost = float(reply.get("cost_usd", 0.0)) if isinstance(reply, dict) else 0.0
            history.append({"role": "assistant", "content": text})

            try:
                actions = parse_actions(text)
            except ProtocolError:
                # one automatic re-ask (spec 11)
                history.append({"role": "user", "content":
                    "Reply with valid JSON action blocks only."})
                try:
                    reply = self.builder.chat(history, max_tokens=3000)
                    text = reply["text"] if isinstance(reply, dict) else str(reply)
                    actions = parse_actions(text)
                    history.append({"role": "assistant", "content": text})
                except (ProtocolError, Exception) as exc:  # noqa: BLE001
                    seq += 1
                    self.journal.append_step(task_id, seq, "protocol_error",
                                             result={"error": str(exc)[:200]})
                    return self._end(task_id, "error", "builder not following protocol")

            results_txt: List[str] = []
            for action in actions:
                seq += 1
                if seq > budget.max_steps:
                    return self._end(task_id, "budget_exceeded", "step limit")
                name = action["action"]
                if name == "update_plan":
                    plan = str(action["args"].get("plan", ""))
                    self.journal.append_step(task_id, seq, "plan", tool="update_plan",
                                             args=action["args"], result={"ok": True})
                    self._emit(task_id, "plan_update", {"plan": plan})
                    results_txt.append("plan updated")
                    continue
                if name == "finish":
                    summary = str(action["args"].get("summary", ""))
                    self.journal.append_step(task_id, seq, "finish", tool="finish",
                                             args=action["args"], result={"ok": True})
                    return self._end(task_id, "completed", summary)
                a0 = time.time()
                try:
                    res = self._exec(jail, action)
                    kind = "tool"
                except ToolError as exc:
                    res = {"error": str(exc)}
                    kind = "tool_error"
                self.journal.append_step(
                    task_id, seq, kind, tool=name, args=action["args"], result=res,
                    model=model_builder, duration_ms=int((time.time() - a0) * 1000))
                self._emit(task_id, "step", {"seq": seq, "tool": name, "result": res})
                results_txt.append(f"{name} -> {str(res)[:2000]}")
            history.append({"role": "user", "content": "\n".join(results_txt)})
        return self._end(task_id, "budget_exceeded", "step limit")

    def _end(self, task_id: str, status: str, result: str) -> Dict[str, Any]:
        self.journal.set_status(task_id, status, result)
        self._emit(task_id, "done", {"status": status, "result": result})
        return {"task_id": task_id, "status": status, "result": result}
```

Update `__init__.py`:
```python
from .tools import PathJail, ToolError  # noqa: F401
from .journal import LongTaskJournal  # noqa: F401
from .engine import LongTaskEngine, Budget  # noqa: F401
```

- [ ] **Step 4: Run tests** → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(longtask): engine core loop + fake-model harness"`

---

### Task 6: API endpoints in main.py

**Files:**
- Modify: `backend/main.py` (add near missions endpoints, ~line 2550+)
- Test: `backend/tests/test_longtask_api.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.longtask.journal import LongTaskJournal
    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "lt.db"))
    def fake_run(self, goal, repo_path, **kw):
        tid = main.LONGTASK_JOURNAL.create_task(goal=goal, repo_path=repo_path)
        main.LONGTASK_JOURNAL.set_status(tid, "completed", "ok")
        return {"task_id": tid, "status": "completed", "result": "ok"}
    from core.longtask import engine as eng_mod
    monkeypatch.setattr(eng_mod.LongTaskEngine, "run", fake_run)
    return TestClient(main.app)

def test_create_and_get(client, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    r = client.post("/api/v1/longtasks",
                    json={"goal": "x", "repo_path": str(repo)})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    tid = r.json()["task_id"]
    assert client.get(f"/api/v1/longtasks/{tid}").json()["id"] == tid
    assert isinstance(client.get("/api/v1/longtasks").json(), list)

def test_rejects_bad_repo(client):
    r = client.post("/api/v1/longtasks",
                    json={"goal": "x", "repo_path": "C:/does/not/exist"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run** → 404/AttributeError.
- [ ] **Step 3: Implement** in `main.py` (module-level near other singletons):

```python
from core.longtask import LongTaskEngine, LongTaskJournal, Budget

LONGTASK_JOURNAL = LongTaskJournal(DATA_DIR / "longtasks.db")
```

Endpoints:

```python
class LongTaskCreate(BaseModel):
    goal: str
    repo_path: str
    autonomy: str = "ask"
    max_steps: int = 40
    max_cost_aud: float = 2.0
    max_wall_min: int = 120

@app.post("/api/v1/longtasks")
def create_longtask(req: LongTaskCreate):
    if not Path(req.repo_path).is_dir():
        raise HTTPException(400, "repo_path is not a directory")
    engine = LongTaskEngine(builder=_longtask_builder(), journal=LONGTASK_JOURNAL)
    # P1 runs synchronously; P2 moves to a worker thread with pause/resume.
    res = engine.run(goal=req.goal, repo_path=req.repo_path,
                     budget=Budget(req.max_steps, req.max_cost_aud, req.max_wall_min),
                     autonomy=req.autonomy)
    return res

@app.get("/api/v1/longtasks")
def list_longtasks():
    return LONGTASK_JOURNAL.list_tasks()

@app.get("/api/v1/longtasks/{task_id}")
def get_longtask(task_id: str):
    t = LONGTASK_JOURNAL.get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t["steps"] = LONGTASK_JOURNAL.steps_for(task_id)
    return t
```

`_longtask_builder()` returns the DashScope/OpenRouter chat client wired to
`config.yaml longtask_builder`; in tests it is monkeypatched away. Match the
existing builder construction used by LoopEngine (`_spec_or_default` +
`OpenRouterClient`) — read the current LoopEngine wiring in `main.py` before
implementing and copy that pattern exactly.

- [ ] **Step 4: Run tests** → PASS. Also start server smoke:
`cd backend ; ..\venv\Scripts\python.exe -m uvicorn main:app --port 8000`
then `GET /api/v1/longtasks` returns `[]`.
- [ ] **Step 5: Commit** — `git commit -m "feat(longtask): HTTP API (create/list/get)"`

---

### Task 7: Config keys + model wiring + final gate

**Files:**
- Modify: `backend/config.yaml` (`models:` section)
- Modify: `backend/core/router.py` (COUNCIL entry) — per HANDOVER gotcha both must change

- [ ] **Step 1: Add to `config.yaml` models:**

```yaml
longtask_builder: "qwen/qwen3.8-max"
longtask_builder_fallback: "qwen/qwen3-coder-480b-a35b-instruct"
longtask_reviewer: "local/fable"
```

- [ ] **Step 2: Add matching COUNCIL role in `router.py`** following the existing
role pattern (read the COUNCIL dict first; add `longtask_builder` /
`longtask_reviewer` keys with the same ModelSpec shape used for `engineer`).
- [ ] **Step 3: Full gate:**

```powershell
cd C:\Users\caleb\infinity-code\backend
..\venv\Scripts\python.exe -m pytest tests/test_longtask_tools.py tests/test_longtask_journal.py tests/test_longtask_protocol.py tests/test_longtask_engine.py tests/test_longtask_api.py -v
```
Expected: all PASS.
- [ ] **Step 4: Commit** — `git commit -m "feat(longtask): model routing keys + council entries"`

---

## Out of scope (P2+, per spec §9)

Review-and-continue gate, git branch checkpoints, pause/resume/restore, WS
events, UI, approval gates, self-learning (lessons/playbooks/benchmarks),
context compaction.

## Self-review notes

- Spec §3.1 cycle: plan/act/verify(partial via run_command)/finish in P1; review gate P2 ✔
- §3.2 toolset: all eight actions present (update_plan, finish handled in loop) ✔
- §4 journal tables match spec schema ✔ (branch column reserved, unused until P2)
- §7 model keys added both in config.yaml AND router COUNCIL (HANDOVER gotcha) ✔
- §10 fake-model harness + budget + protocol tests present ✔; resume/security
  suites belong to P2/P5 tasks that own those features
