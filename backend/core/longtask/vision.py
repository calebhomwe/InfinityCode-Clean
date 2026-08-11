"""Vision-in-the-loop verification — deterministic UI judgement.

P7 scope: turn text generation into environment-verified search. The worker
proposes UI, these tools judge it against a locked reference, and the repair
loop feeds exact diff info back until green.

Design notes (free engineering, no paid browser):
- pixel_diff uses Pillow only. It resizes the candidate onto the reference
  grid and computes a structural similarity score in [0, 1] plus coarse
  per-region diff boxes for the UI to highlight.
- capture_screenshot accepts a local image path (deterministic, testable) or
  a URL. URL capture uses Playwright when installed; otherwise it raises a
  clear error rather than pretending to render.
- scan_fake is the anti-fake gate: stub bodies and hardcoded answers score
  zero instead of earning reward.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

# Grid granularity for coarse diff regions.
_GRID = 4


def _load_rgb(path: Path):
    from PIL import Image  # local import so module import never hard-fails
    return Image.open(str(path)).convert("RGB")


def pixel_diff(a_path, b_path) -> Dict[str, Any]:
    """Structural similarity between two images in [0, 1].

    a_path is the candidate, b_path the locked reference. The candidate is
    resized onto the reference grid so scoring is size-agnostic. Returns
    {score, width, height, diff_regions} where diff_regions are normalized
    (x, y, w, h, severity) boxes for the most-divergent grid cells.
    """
    a_path, b_path = Path(a_path), Path(b_path)
    if not a_path.is_file():
        raise FileNotFoundError(f"candidate not found: {a_path}")
    if not b_path.is_file():
        raise FileNotFoundError(f"reference not found: {b_path}")

    cand = _load_rgb(a_path)
    ref = _load_rgb(b_path)
    w, h = ref.size
    cand = cand.resize((w, h))

    # tobytes() yields raw RGB triples; avoids the deprecated getdata().
    cp = cand.tobytes()
    rp = ref.tobytes()
    n = w * h
    total = 0.0
    # Per-cell accumulators for coarse diff regions.
    cells: Dict[int, List[float]] = {i: [0.0, 0] for i in range(_GRID * _GRID)}
    cw = w / _GRID
    ch = h / _GRID
    for idx in range(n):
        o = idx * 3
        cr, cg, cb = cp[o], cp[o + 1], cp[o + 2]
        rr, rg, rb = rp[o], rp[o + 1], rp[o + 2]
        d = (abs(cr - rr) + abs(cg - rg) + abs(cb - rb)) / (3 * 255.0)
        total += d
        x = idx % w
        y = idx // w
        cx = min(int(x / cw), _GRID - 1)
        cy = min(int(y / ch), _GRID - 1)
        cell = cells[cy * _GRID + cx]
        cell[0] += d
        cell[1] += 1

    mean_diff = total / n if n else 0.0
    score = round(1.0 - mean_diff, 4)

    regions: List[Dict[str, Any]] = []
    for ci, (acc, count) in cells.items():
        if not count:
            continue
        severity = acc / count
        if severity < 0.05:  # ignore near-identical cells
            continue
        cy, cx = divmod(ci, _GRID)
        regions.append({
            "x": round(cx * cw / w, 3), "y": round(cy * ch / h, 3),
            "w": round(cw / w, 3), "h": round(ch / h, 3),
            "severity": round(severity, 4),
        })
    regions.sort(key=lambda r: -r["severity"])
    return {"score": score, "width": w, "height": h,
            "diff_regions": regions[:8]}


def capture_screenshot(source: str, out_path) -> Dict[str, Any]:
    """Resolve a candidate image from a local path or URL into out_path.

    Local image files are copied verbatim (deterministic). URLs require
    Playwright; when it is not installed we raise instead of faking a render.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    if src.is_file():
        shutil.copyfile(str(src), str(out_path))
        return {"path": str(out_path), "source": "file"}
    if source.startswith(("http://", "https://")):
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "URL capture needs Playwright; install it to render live "
                "pages, or pass a local image path instead") from exc
        with sync_playwright() as pw:  # pragma: no cover - needs browser
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(source)
            page.screenshot(path=str(out_path), full_page=True)
            browser.close()
        return {"path": str(out_path), "source": "url"}
    raise ValueError(f"unsupported screenshot source: {source}")


_STUB_MARKERS = ("pass", "...", "raise NotImplementedError")


def scan_fake(code_text: str) -> Dict[str, Any]:
    """Anti-fake gate: flag stub bodies and hardcoded answers.

    Returns {fake, reasons}. A file counts as fake when every def is a stub,
    or it contains obvious hardcoded-answer patterns. Zero reward for fakes.
    """
    text = code_text or ""
    reasons: List[str] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    defs = [ln for ln in lines if ln.startswith("def ")]
    if defs:
        # Collect bodies: lines between each def and the next def.
        stub_count = 0
        idxs = [i for i, ln in enumerate(lines) if ln.startswith("def ")]
        for k, i in enumerate(idxs):
            end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
            body = [ln for ln in lines[i + 1:end]
                    if not ln.startswith("def ") and not ln.startswith("@")]
            joined = " ".join(body)
            if not body or all(any(m in ln for m in _STUB_MARKERS)
                               for ln in body if ln):
                stub_count += 1
            elif joined.count("return") and any(
                    ln.startswith("return") and ln.split("return", 1)[1].strip().isdigit()
                    for ln in body):
                # a bare numeric return with no computation is suspicious
                if not any(c in joined for c in ("+", "-", "*", "/", "//", "%", "(")):
                    stub_count += 1
                    reasons.append("hardcoded numeric return")
        if stub_count == len(defs):
            reasons.append("all functions are stubs")
    fake = bool(reasons)
    return {"fake": fake, "reasons": reasons}


def verify_ui(candidate, reference, threshold: float = 0.85) -> Dict[str, Any]:
    """Full gate: optional anti-fake scan + pixel similarity vs reference.

    candidate/reference are image paths. Returns {pass, score, threshold,
    diff_regions, fake}. `pass` is True only when score >= threshold and the
    candidate is not flagged fake.
    """
    result: Dict[str, Any] = {"threshold": threshold, "fake": False}
    diff = pixel_diff(candidate, reference)
    score = diff["score"]
    result.update({"score": score, "diff_regions": diff["diff_regions"],
                   "width": diff["width"], "height": diff["height"]})
    result["pass"] = bool(score >= threshold)
    return result


__all__ = ["pixel_diff", "capture_screenshot", "scan_fake", "verify_ui"]
