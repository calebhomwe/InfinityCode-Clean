"""List Kimi Code CLI configured model ids, then probe one tiny prompt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

cfg = Path.home() / ".kimi-code" / "config.toml"
ids: list[str] = []
if cfg.is_file():
    for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith('[models."') and line.endswith('"]'):
            ids.append(line[len('[models."'):-2])
print("models in kimi-code config:")
for i in ids:
    print(" -", i)

pick = None
for want in ("kimi-k2.6", "k2.6"):
    hits = [i for i in ids if want in i.lower()]
    if hits:
        pick = sorted(hits)[-1]
        break
if pick is None and ids:
    pick = sorted(ids)[-1]
print("probe model:", pick)
if not pick:
    raise SystemExit(0)

try:
    r = subprocess.run(
        ["kimi", "-p", "Reply with exactly: KIMI CLI ONLINE", "-m", pick,
         "--output-format", "stream-json"],
        capture_output=True, text=True, timeout=120, shell=True,
        encoding="utf-8", errors="replace")
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("role") == "assistant" and d.get("content"):
                out.append(d["content"])
    txt = "".join(out).strip()
    print("STDOUT content:", txt[:200] if txt else "(empty)")
    if r.returncode != 0 or not txt:
        print("rc:", r.returncode)
        print("stderr:", (r.stderr or "")[-300:])
except Exception as e:
    print("ERR:", str(e)[:300])
