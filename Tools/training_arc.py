#!/usr/bin/env python
"""Training Arc — the 1-hour test → measure → improve loop.

Every cycle:
  1. The MASTER (qwen3-max) writes 3 HARD drills from the accumulated
     cuotiben (mistake notebook).
  2. Two disciple lineages (deepseek-v4-flash vs qwen3-coder-flash) each sit
     the drills: attempt -> standalone tsc --strict -> master grades (bar
     ramps).
  3. Mistakes from every grade feed the next cycle's curriculum (the disciple
     is drilled on exactly what he keeps getting wrong).
  4. The BEST disciple of the cycle becomes the drafter for one premium-swarm
     iteration, so the app itself improves with the strongest performer.
  5. Metrics (per-model pass rate, fidelity, rubric scores) land in
     metrics.jsonl; a scoreboard prints each cycle so improvement is visible.

Stops at the wall-clock budget (default 60 min). Multi-LLM by design:
DeepSeek + Qwen today; Kimi slots back in automatically when funded.

Usage:
  .venv\\Scripts\\python.exe Tools\\training_arc.py --minutes 60
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "Tools"))
import premium_swarm  # noqa: E402
import dojo_trainer as trainer  # noqa: E402

premium_swarm.MAX_CALLS = 500

KIDS = ["deepseek/v4-flash", "dashscope/qwen3-coder-flash"]
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"


def latest_rubric() -> float | None:
    runs = sorted((REPO / "Tools" / "premium_runs").glob("*"), reverse=True)
    for r in runs:
        jl = r / "swarm.jsonl"
        if not jl.is_file():
            continue
        scores = []
        for line in jl.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") in ("judge_before", "judge_after"):
                scores.append(float(e.get("score", 0)))
        if scores:
            return scores[-1]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--drills", type=int, default=3,
                    help="hard drills per cycle (fewer = cheaper)")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = REPO / "Tools" / "training_arc" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.minutes * 60
    print(f"[arc] Training Arc started — {args.minutes} min budget, kids={KIDS}")

    sins = list(trainer.KNOWN_SINS) + trainer.mine_more_sins()
    board: dict[str, list[float]] = {k: [] for k in KIDS}
    cycle = 0

    while time.time() < end:
        cycle += 1
        bar = min(0.9, 0.78 + 0.03 * cycle)
        print(f"\n[arc] === CYCLE {cycle} | bar={bar} | "
              f"{int((end - time.time()) // 60)} min left ===")
        drills = trainer.dad_writes_drills(sins, args.drills, run_dir, hard=True)
        if not drills:
            print("[arc] master wrote no drills — skipping cycle")
            time.sleep(20)
            continue
        (run_dir / f"drills_cycle{cycle}.json").write_text(
            json.dumps(drills, indent=1, ensure_ascii=False), encoding="utf-8")

        cycle_score: dict[str, tuple[int, float]] = {}
        best_kid, best_key = KIDS[0], (-1, -1.0)
        for kid in KIDS:
            trainer.KID_REF = kid
            passes, fids = 0, []
            for drill in drills:
                if time.time() > end:
                    break
                kind = drill.get("kind", "tsx")
                code = trainer.kid_attempt(drill, run_dir)
                gate_ok, gate_out = (True, "") if kind == "css" else \
                    trainer.tsc_gate(code, run_dir)
                grade = trainer.dad_grades(drill, code, gate_ok, gate_out,
                                           run_dir, hard=True, bar=bar)
                fid = float(grade.get("fidelity", 0.0))
                passed = bool(grade.get("pass")) and fid >= bar
                passes += 1 if passed else 0
                fids.append(fid)
                for s in grade.get("sins", [])[:3]:
                    sins.append({"sin": f"{kid.split('/')[-1]}: {str(s)[:140]}",
                                 "evidence": f"cycle {cycle} drill "
                                             f"{drill.get('id')}"})
                print(f"[arc]   {kid.split('/')[-1]} d{drill.get('id')}: "
                      f"fid={fid} {'PASS' if passed else 'fail'} "
                      f"— {str(grade.get('remark', ''))[:70]}")
            avg = sum(fids) / len(fids) if fids else 0.0
            board[kid].append(avg)
            cycle_score[kid] = (passes, avg)
            if (passes, avg) > best_key:
                best_key, best_kid = (passes, avg), kid

        print(f"[arc] cycle scoreboard: " + " | ".join(
            f"{k.split('/')[-1]}={cycle_score[k][0]}/{len(drills)} "
            f"avg={cycle_score[k][1]:.2f}" for k in KIDS))

        # app improves with the cycle's strongest kid
        if time.time() < end - 240:
            print(f"[arc] premium-swarm iteration with drafter={best_kid}")
            try:
                r = subprocess.run(
                    [str(VENV_PY), "-u", "Tools/premium_swarm.py",
                     "--iterations", "1", "--provider", "auto",
                     "--drafter", best_kid],
                    cwd=REPO, capture_output=True, text=True, timeout=560,
                    encoding="utf-8", errors="replace")
                tail = [l for l in (r.stdout or "").splitlines()
                        if "iteration_done" in l or "SUMMARY" in l]
                for l in tail[-3:]:
                    print(f"[arc]   {l}")
            except Exception as e:
                print(f"[arc] swarm subprocess issue: {str(e)[:120]}")

        rubric = latest_rubric()
        with (run_dir / "metrics.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "cycle": cycle, "bar": bar,
                "per_kid": {k: {"passes": cycle_score[k][0],
                                "avg_fidelity": cycle_score[k][1]}
                            for k in KIDS},
                "best_kid": best_kid, "rubric": rubric,
                "ts": time.time()}, default=str) + "\n")
        print(f"[arc] rubric now: {rubric}")

    # final scoreboard
    print(f"\n[arc] === FINAL SCOREBOARD ({cycle} cycles) ===")
    for k in KIDS:
        vals = board[k]
        if vals:
            trend = " -> ".join(f"{v:.2f}" for v in vals)
            print(f"[arc] {k}: avg/trial {trend}")
    print(f"[arc] metrics: {run_dir / 'metrics.jsonl'}")


if __name__ == "__main__":
    main()
