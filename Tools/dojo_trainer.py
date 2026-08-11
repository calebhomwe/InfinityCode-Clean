#!/usr/bin/env python
"""Dojo Trainer — toughen the disciple with the remaining free quota.

A loving-but-serious old-master training loop for the Infinity Code swarm.
Every mistake lands in the 错题本 (cuotiben — the mistake notebook) and is
drilled until it disappears:

  1. MINE   — real mistakes (build-gate failures from premium_swarm runs)
              become notebook entries.
  2. DRILL  — the MASTER (qwen3-max) writes drills targeting those exact
              mistakes. --hard: nightmare tier (planted-bug hunts, constraint
              stacking, generics, perf traps). Pass bar raised 0.7 -> 0.8.
  3. ATTEMPT— the DISCIPLE (qwen3-coder-flash by default, stand-in for the
              local FABLE Qwen) answers each drill with complete code.
  4. GATE   — TSX drills must survive standalone `tsc --strict`; then the
              MASTER grades mercilessly and, on failure, rewrites the answer
              himself.
  5. EXAM   — --exam: one REAL repo exam on src/components/TabBar.tsx, gated
              by the full-project tsc + vite build, with backup/rollback. If
              the disciple passes gate AND master (>= bar), his work ships.
  6. EXPORT — every drill becomes an SFT sample in the app's training-flywheel
              format (backend/core/training/collector.py): chat-message JSONL
              with task_id/goal/fidelity, consumable by the LoRA scaffold.

Zero new dependencies. Only live-quota models are called.

Usage:
  .venv\\Scripts\\python.exe Tools\\dojo_trainer.py --drills 6
  .venv\\Scripts\\python.exe Tools\\dojo_trainer.py --drills 6 --hard --exam
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "Tools"))
from premium_swarm import (  # noqa: E402  (shared plumbing, same venv)
    qwen_chat, parse_json_reply, typecheck, build_frontend, dispatch_chat,
)

DAD = "qwen3-max"
KID = "qwen3-coder-flash"
KID_REF = "dashscope/qwen3-coder-flash"
OUT_DIR = REPO / "backend" / "data" / "training"


def dad_call(system: str, user: str, **kw) -> str:
    return dispatch_chat("dashscope/" + DAD, system, user, **kw)


def kid_call(system: str, user: str, **kw) -> str:
    return dispatch_chat(KID_REF, system, user, **kw)

DAD_PERSONA = (
    "You are the old master of the dojo — strict but loving, raising your "
    "disciple (a small code model) like your own son to be the strongest "
    "engineer in the village. Standards are absolute; praise is earned; "
    "every mistake goes in the cuotiben (mistake notebook) and becomes a "
    "lesson with a corrected example. You never lower the bar, you raise "
    "the disciple.")

HARD_PERSONA_ADD = (
    " Tonight the dojo trains HARD: you set exams that make grown models "
    "cry. Multi-constraint, planted-bug, and refactoring tasks only. "
    "Mediocrity is a sin against the craft.")

CONSTRAINT_BRIEF = """Hard constraints for every answer:
- React 18 + TypeScript STRICT; function components; no implicit any.
- Only import from "react" and existing project packages (no new deps).
- CSS: animate ONLY transform/opacity/filter; entrances cubic-bezier(0.22,1,0.36,1);
  micro 120-200ms; reveals 240-400ms; stagger 40-60ms; include a
  prefers-reduced-motion guard for any animation.
- Read a component's ACTUAL prop contract before passing props. Never invent props.
- When a build error is quoted, fix the exact offending line first."""

KNOWN_SINS = [
    {"sin": "hallucinated prop",
     "evidence": "src/App.tsx(898): TS2322 property 'open' does not exist on "
                 "{ onClose } — passed a prop the component never declared, twice."},
    {"sin": "ignoring the build error",
     "evidence": "retry after gate failure produced the same TS2322 — the error "
                 "line was not actually read."},
    {"sin": "layout-property animation",
     "evidence": "premium brief violation class: animating width/height/top/left/"
                 "margin/box-shadow instead of transform/opacity."},
    {"sin": "missing reduced-motion guard",
     "evidence": "premium brief violation class: new keyframes without a "
                 "prefers-reduced-motion disable."},
]


def mine_more_sins() -> list[dict]:
    sins = []
    runs = REPO / "Tools" / "premium_runs"
    if runs.is_dir():
        for jl in sorted(runs.glob("*/swarm.jsonl")):
            try:
                for line in jl.read_text(encoding="utf-8").splitlines():
                    e = json.loads(line)
                    if e.get("event") == "reviewer_veto":
                        sins.append({"sin": "reviewer veto",
                                     "evidence": "; ".join(
                                         e.get("reasons", [])[:2])[:220]})
            except Exception:
                continue
    return sins


def dad_writes_drills(sins: list[dict], n: int, run_dir: Path,
                      hard: bool) -> list[dict]:
    hard_block = ""
    if hard:
        hard_block = """
HARD MODE — every drill must be one of:
- "bug hunt": a code snippet you embed with 2-3 PLANTED sins (hallucinated prop,
  layout animation, missing reduced-motion, implicit any, wrong easing) that
  the student must find AND fix in one pass;
- "stack": 4+ constraints that must ALL hold simultaneously (strict generics +
  transform-only animation + reduced-motion + a11y roles);
- "refactor": take a bad pattern and rebuild it correctly, preserving exports.
Embed any snippet inside a "snippet" field. These must be genuinely difficult."""
    prompt = f"""Your disciple's mistake notebook (cuotiben) holds these
recurring errors (evidence attached):
{json.dumps(sins, indent=1, ensure_ascii=False)}
{hard_block}

Write {n} training drills that target these mistakes and the constraint brief.
Mix kinds: "tsx" drills (one self-contained React+TS component file that must
compile under tsc --strict) and "css" drills (one CSS block).
Each drill must be specific enough to grade objectively.

Reply STRICT JSON only:
[{{"id": "d1", "kind": "tsx"|"css", "goal": "...", "trap": "the sin this drill hunts", "must_include": ["..."], "snippet": "optional embedded buggy code"}}]
"""
    persona = DAD_PERSONA + (HARD_PERSONA_ADD if hard else "")
    out = dad_call(persona, prompt, max_tokens=6000, temperature=0.8,
                   run_dir=run_dir)
    data = parse_json_reply(out)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and d.get("goal")][:n]
    print(f"[dojo] drill authoring failed: {out[:200]}")
    return []


KID_PERSONA = (
    "You are a careful senior frontend engineer sitting a serious exam. "
    "Answer the drill with ONE complete file. No placeholders. It must "
    "compile under tsc --strict. If a buggy snippet is given, find EVERY "
    "planted defect and fix them all.")


def kid_attempt(drill: dict, run_dir: Path) -> str:
    snippet = drill.get("snippet") or ""
    snippet_block = f"\nBUGGY SNIPPET TO FIX (find ALL planted defects):\n{snippet}" \
        if snippet else ""
    prompt = f"""DRILL ({drill.get('kind')}): {drill.get('goal')}
Must include: {json.dumps(drill.get('must_include', []))}{snippet_block}
Constraints:
{CONSTRAINT_BRIEF}
Output ONLY the file content in one fenced code block (```tsx or ```css)."""
    out = kid_call(KID_PERSONA, prompt, max_tokens=8000, temperature=0.3,
                   run_dir=run_dir)
    m = re.search(r"```[a-z]*\r?\n(.*?)```", out, re.DOTALL)
    return m.group(1) if m else out


def tsc_gate(code: str, run_dir: Path) -> tuple[bool, str]:
    d = run_dir / "drills"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"drill_{int(time.time() * 1000)}.tsx"
    f.write_text(code, encoding="utf-8", newline="")
    try:
        r = subprocess.run(
            ["node", r"node_modules\typescript\bin\tsc", "--noEmit", "--strict",
             "--jsx", "react-jsx", "--target", "es2020", "--module", "esnext",
             "--moduleResolution", "bundler", "--skipLibCheck", str(f)],
            cwd=REPO, capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace")
        return r.returncode == 0, (r.stdout + r.stderr)[:1500]
    except Exception as e:
        return False, str(e)[:300]


CSS_SIN_RE = re.compile(
    r"transition[^;]*\b(width|height|top|left|right|bottom|margin|padding|"
    r"box-shadow)\b|animation[^;]*\b(width|height|top|left)\b|linear\b|"
    r"ease\s*;|ease\s*$", re.I | re.M)


def dad_grades(drill: dict, code: str, gate_ok: bool, gate_out: str,
               run_dir: Path, hard: bool, bar: float) -> dict:
    if drill.get("kind") == "css":
        css_sins = CSS_SIN_RE.findall(code)
        gate_note = f"static scan found layout/linear animation sins: {css_sins}" \
            if css_sins else "static scan clean"
        gate_ok = gate_ok and not css_sins
        gate_out = gate_note
    snippet = drill.get("snippet") or ""
    snippet_block = f"\nORIGINAL BUGGY SNIPPET (did he fix EVERY planted defect?):\n{snippet}" \
        if snippet else ""
    prompt = f"""Your disciple's answer to this {'HARD ' if hard else ''}drill:
DRILL: {json.dumps({k: v for k, v in drill.items() if k != 'snippet'}, ensure_ascii=False)}{snippet_block}
TSC GATE: {"PASS" if gate_ok else "FAIL"} {gate_out[:800]}
CODE:
{code[:9000]}

Grade him. fidelity = how close this is to excellence (0-1). The pass bar is
{bar}. If fidelity < {bar} you MUST rewrite the answer yourself in full
(field "corrected"). List his mistakes concretely; in bug-hunt drills, list
any planted defect he MISSED as a mistake. One line of masterly remark allowed.

Reply STRICT JSON only:
{{"fidelity": 0.0-1.0, "pass": bool, "sins": ["..."], "remark": "...", "corrected": "full corrected file content or empty string"}}
"""
    out = dad_call(DAD_PERSONA, prompt, max_tokens=9000, temperature=0.2,
                   run_dir=run_dir)
    data = parse_json_reply(out)
    if isinstance(data, dict) and "fidelity" in data:
        return data
    return {"fidelity": 0.0, "pass": False,
            "sins": [f"grade parse failure: {out[:160]}"], "remark": "",
            "corrected": ""}


# --------------------------------------------------------------------------
# REAL REPO EXAM
# --------------------------------------------------------------------------

EXAM_REL = "src/components/TabBar.tsx"


def run_exam(run_dir: Path, bar: float, samples: list[dict], ts: str) -> dict:
    """One real-repo exam. Kid's work ships to disk only if the full-project
    gate AND dad both pass; otherwise rollback + dad's corrected version gets
    the same treatment."""
    target = REPO / EXAM_REL
    current = target.read_text(encoding="utf-8")
    print(f"[dojo] EXAM: real repo task on {EXAM_REL}")

    task = dad_call(
        DAD_PERSONA + HARD_PERSONA_ADD,
        f"""Here is the current {EXAM_REL} of my disciple's app:

{current}

Set ONE hard, concrete exam task for it: a premium motion upgrade that keeps
every existing prop, behaviour and a11y attribute intact and must compile
under the project's strict tsc. Transform/opacity/filter animation only.
Reply STRICT JSON only: {{"goal": "...", "must_include": ["..."]}}""",
        max_tokens=1200, temperature=0.7, run_dir=run_dir)
    exam = parse_json_reply(task) or {}
    goal = str(exam.get("goal") or "Add a gliding active-tab indicator using "
               "transform-only animation, preserving all props and a11y.")
    must = exam.get("must_include") or []
    print(f"[dojo]   exam goal: {goal[:110]}")

    def kid_write(feedback: str) -> str:
        fb = f"\nPREVIOUS ATTEMPT FAILED. ERRORS:\n{feedback}\nFIX EXACTLY THESE." \
            if feedback else ""
        prompt = f"""EXAM on the real file {EXAM_REL}. Current file content:

{current}

TASK: {goal}
Must include: {json.dumps(must)}
Constraints:
{CONSTRAINT_BRIEF}
Output the FULL new file content in one fenced ```tsx block. Nothing else.{fb}"""
        out = kid_call(KID_PERSONA, prompt, max_tokens=12000,
                       temperature=0.3, run_dir=run_dir)
        m = re.search(r"```tsx\r?\n(.*?)```", out, re.DOTALL)
        return m.group(1) if m else ""

    backup = current
    def try_candidate(code: str, label: str) -> tuple[bool, str]:
        if not code.strip():
            return False, "empty answer"
        target.write_text(code, encoding="utf-8", newline="")
        ok, out = typecheck()
        if ok:
            ok, out = build_frontend()
        if not ok:
            print(f"[dojo]   {label} gate FAIL: {out[-220:].strip()[:220]}")
        return ok, out

    kept_code, status = "", "failed"
    code = kid_write("")
    ok, out = try_candidate(code, "kid")
    if not ok:
        code2 = kid_write(out[-2500:])
        ok, out = try_candidate(code2, "kid retry")
        code = code2 or code
    if ok:
        grade = dad_grades({"id": "exam", "kind": "tsx", "goal": goal,
                            "must_include": must}, code, True, "", run_dir,
                           hard=True, bar=bar)
        fid = float(grade.get("fidelity", 0.0))
        print(f"[dojo]   disciple exam fidelity={fid} — {grade.get('remark', '')[:100]}")
        if fid >= bar:
            kept_code, status = code, "disciple_passed"
        else:
            target.write_text(backup, encoding="utf-8", newline="")
    if not kept_code:
        # the master demonstrates: his corrected version must also survive
        # the gate
        corrected = ""
        if not ok:
            corrected = code  # let dad fix from the failing attempt
        dad_out = dad_call(
            DAD_PERSONA,
            f"The exam answer failed ({'gate: ' + out[-1200:] if not ok else 'fidelity below bar'}). "
            f"Here is the attempt:\n{code[:8000]}\n\nRewrite the FULL file "
            f"correctly. TASK: {goal}\nConstraints:\n{CONSTRAINT_BRIEF}\n"
            "One fenced ```tsx block, nothing else.",
            max_tokens=12000, temperature=0.2, run_dir=run_dir)
        m = re.search(r"```tsx\r?\n(.*?)```", dad_out, re.DOTALL)
        corrected = m.group(1) if m else ""
        ok2, out2 = try_candidate(corrected, "master")
        if ok2 and corrected:
            kept_code, status = corrected, "master_corrected"
        else:
            target.write_text(backup, encoding="utf-8", newline="")
            status = "rolled_back"

    samples.append({
        "task_id": f"dojo-exam-{ts}",
        "goal": goal,
        "fidelity": 1.0 if status == "disciple_passed" else (0.8 if status == "master_corrected" else 0.0),
        "messages": [
            {"role": "system", "content": "Goal: " + goal + "\n" + CONSTRAINT_BRIEF},
            {"role": "user", "content":
             f"EXAM on {EXAM_REL}. Base file:\n{current[:6000]}\nTASK: {goal}"},
            {"role": "assistant", "content": kept_code or backup},
        ],
        "source": status,
        "sins": [],
    })
    print(f"[dojo]   exam status: {status}")
    return {"exam": status, "goal": goal}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drills", type=int, default=6)
    ap.add_argument("--hard", action="store_true",
                    help="nightmare drills + pass bar 0.8")
    ap.add_argument("--exam", action="store_true",
                    help="also run one real-repo exam on TabBar.tsx")
    ap.add_argument("--kid", default=None,
                    help="provider ref for the kid, e.g. deepseek/v4-flash")
    args = ap.parse_args()
    global KID_REF
    if args.kid:
        KID_REF = args.kid
    bar = 0.8 if args.hard else 0.7

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = REPO / "Tools" / "dojo_runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[dojo] run dir: {run_dir} | hard={args.hard} bar={bar}")

    sins = KNOWN_SINS + mine_more_sins()
    drills = dad_writes_drills(sins, args.drills, run_dir, args.hard)
    (run_dir / "drills.json").write_text(json.dumps(drills, indent=1,
                                       ensure_ascii=False), encoding="utf-8")
    print(f"[dojo] wrote {len(drills)} {'HARD ' if args.hard else ''}drills")

    samples, report = [], []
    for i, drill in enumerate(drills):
        kind = drill.get("kind", "tsx")
        print(f"[dojo] drill {i + 1}/{len(drills)} ({kind}): "
              f"{str(drill.get('goal'))[:70]}")
        code = kid_attempt(drill, run_dir)
        gate_ok, gate_out = (True, "") if kind == "css" else tsc_gate(code, run_dir)
        if kind == "tsx":
            print(f"[dojo]   tsc gate: {'PASS' if gate_ok else 'FAIL'}")
        grade = dad_grades(drill, code, gate_ok, gate_out, run_dir,
                           args.hard, bar)
        fid = float(grade.get("fidelity", 0.0))
        passed = bool(grade.get("pass")) and fid >= bar
        teacher = code if passed else (grade.get("corrected") or code)
        if not passed and not grade.get("corrected"):
            print("[dojo]   no corrected answer — sample skipped")
            continue
        samples.append({
            "task_id": f"dojo-{ts}-{drill.get('id', i)}",
            "goal": str(drill.get("goal", "")),
            "fidelity": fid,
            "messages": [
                {"role": "system",
                 "content": "Goal: " + str(drill.get("goal", "")) + "\n"
                            + CONSTRAINT_BRIEF},
                {"role": "user", "content":
                    f"DRILL ({kind}): {drill.get('goal')}\nMust include: "
                    + json.dumps(drill.get("must_include", []))},
                {"role": "assistant", "content": teacher},
            ],
            "source": "disciple_passed" if passed else "master_corrected",
            "sins": grade.get("sins", []),
        })
        report.append({"drill": drill.get("id"), "kind": kind,
                       "fidelity": fid, "pass": passed,
                       "sins": grade.get("sins", []),
                       "remark": grade.get("remark", "")})
        verdict = "acceptable." if passed else "rewritten by the master."
        print(f"[dojo]   fidelity={fid} pass={passed} — {verdict} "
              f"{grade.get('remark', '')[:90]}")

    exam_res = None
    if args.exam:
        exam_res = run_exam(run_dir, bar, samples, ts)
    if exam_res:
        exam_passed = exam_res["exam"] == "disciple_passed"
        report.append({"drill": "EXAM", "kind": "repo",
                       "fidelity": 1.0 if exam_passed else 0.0,
                       "pass": exam_passed,
                       "sins": [], "remark": exam_res["exam"]})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_out = OUT_DIR / f"dojo_drills_{ts}.jsonl"
    latest = OUT_DIR / "dojo_drills_latest.jsonl"
    for p in (run_out, latest):
        with p.open("w", encoding="utf-8") as fh:
            for s in samples:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    (run_dir / "report.json").write_text(json.dumps(report, indent=1,
                                       ensure_ascii=False), encoding="utf-8")

    passed_n = sum(1 for r in report if r["pass"])
    print(f"\n[dojo] === REPORT CARD ===")
    print(f"[dojo] drills={len(report)} passed={passed_n} "
          f"corrected={len(report) - passed_n} sft_samples={len(samples)}")
    if exam_res:
        print(f"[dojo] exam: {exam_res['exam']} — {exam_res['goal'][:100]}")
    print(f"[dojo] dataset: {run_out}")
    for r in report:
        for s in r["sins"][:2]:
            print(f"[dojo]   mistake({r['drill']}): {s}")


if __name__ == "__main__":
    main()
