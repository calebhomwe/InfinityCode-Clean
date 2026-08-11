"""Eyes Module for Infinity Code X.

Capture live UI state into a strict VisualReport JSON (spec schema), send it
to DeepSeek Flash 1731 for diagnosis (structured text — never raw screenshots),
and let Qwen 3.8 Max verify before/after reports. Playwright drives the system
Chrome (channel="chrome") when installed; otherwise capture degrades to a
valid empty-state report so the API never 500s.

Rules: never invent elements or selectors; prefer a11y tree + DOM selectors;
bounding boxes only as fallback; include broken/hidden/overflow/error states.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

MAX_ELEMENTS = 40            # DOM subset cap (credit: never ship whole pages)
MAX_CONSOLE = 25

# Role-locked model bindings (ascension MODEL_ROLES):
DIAGNOSE_MODEL = "deepseek/deepseek-v4-flash"   # DeepSeek Flash 1731 = coder
VERIFY_MODEL = "dashscope/qwen3.8-max"          # Qwen 3.8 Max = oracle only

_DOM_PICKER = (
    "button, a, input, textarea, select, [role=button], [role=link], "
    "[role=textbox], [data-testid], [aria-label], img, h1, h2, h3"
)


class VisualElement(BaseModel):
    id: str = ""
    type: str = "unknown"
    text: str = ""
    selector: str = ""
    state: str = "visible"
    box: List[float] = Field(default_factory=list)  # [x, y, w, h]

    @field_validator("box")
    @classmethod
    def _box_is_quad(cls, v: List[float]) -> List[float]:
        if len(v) != 4:
            raise ValueError("box must be [x, y, w, h]")
        return v


class VisualReport(BaseModel):
    summary: str = ""
    layout: str = ""
    elements: List[VisualElement] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    consoleErrors: List[str] = Field(default_factory=list)
    networkErrors: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def _box_of(locator: Any) -> List[float]:
    try:
        b = locator.bounding_box()
        if b:
            return [round(float(b["x"]), 1), round(float(b["y"]), 1),
                    round(float(b["width"]), 1), round(float(b["height"]), 1)]
    except Exception:  # noqa: BLE001 - bounding boxes are best-effort
        pass
    return []


def _selector_of(el: Any) -> str:
    """Best REAL selector: data-testid > aria-label > id > tag:nth-of-type.
    Never hallucinated — every branch reads the live DOM."""
    try:
        tid = el.get_attribute("data-testid")
        if tid:
            return f'[data-testid="{tid}"]'
        label = el.get_attribute("aria-label")
        if label:
            return f'[aria-label="{label}"]'
        el_id = el.get_attribute("id")
        if el_id:
            return f"#{el_id}"
        tag = str(el.evaluate("e => e.tagName.toLowerCase()"))
        nth = int(el.evaluate(
            "e => { const p = e.parentElement; const kids = p ? p.children : [];"
            " let n = 1; for (const c of kids) { if (c === e) return n;"
            " if (c.tagName === e.tagName) n += 1; } return n; }"))
        return f"{tag}:nth-of-type({nth})"
    except Exception:  # noqa: BLE001
        return ""


def _capture_playwright(url: str, out_dir: Path) -> Dict[str, Any]:
    """Screenshot + a11y tree + DOM subset + console/network errors + boxes."""
    from playwright.sync_api import sync_playwright

    console_errors: List[str] = []
    network_errors: List[str] = []
    ts = time.strftime("%Y%m%d-%H%M%S")
    shot = out_dir / f"eyes-{ts}.png"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text[:300])
                if m.type == "error" else None)
        page.on("requestfailed",
                lambda r: network_errors.append(f"{r.url[:200]} -> {r.failure}"))
        try:
            page.goto(url, wait_until="load", timeout=20000)
        except Exception as exc:  # noqa: BLE001 - capture continues on errors
            logger.warning("eyes: goto %s failed: %s", url, exc)
        page.wait_for_timeout(800)
        page.screenshot(path=str(shot), full_page=False)

        a11y: Optional[Dict[str, Any]] = None
        try:
            a11y = page.accessibility.snapshot()
        except Exception as exc:  # noqa: BLE001
            logger.warning("eyes: a11y snapshot failed: %s", exc)

        elements: List[Dict[str, Any]] = []
        locators = page.locator(_DOM_PICKER)
        count = locators.count()
        for i in range(min(count, MAX_ELEMENTS * 3)):
            el = locators.nth(i)
            try:
                if not el.is_visible():
                    continue
            except Exception:  # noqa: BLE001 - detached nodes are skipped
                continue
            tag = ""
            text = ""
            state = "visible"
            try:
                tag = str(el.evaluate("e => e.tagName.toLowerCase()"))
                text = (el.inner_text() or "").strip()[:120]
                if el.is_disabled():
                    state = "disabled"
                elif el.get_attribute("aria-hidden") == "true":
                    state = "hidden"
            except Exception:  # noqa: BLE001
                pass
            if not text and tag in ("button", "a"):
                continue  # empty interactive nodes carry no information
            elements.append({
                "id": el.get_attribute("id") or "",
                "type": tag,
                "text": text,
                "selector": _selector_of(el),
                "state": state,
                "box": _box_of(el),
            })
            if len(elements) >= MAX_ELEMENTS:
                break

        overflow = ""
        try:
            overflow = str(page.evaluate(
                "document.documentElement.scrollWidth > "
                "document.documentElement.clientWidth ? 'horizontal overflow' : ''"))
        except Exception:  # noqa: BLE001
            pass
        browser.close()

    issues = [f"{e['type']} {e['text'][:40]!r} is {e['state']}"
              for e in elements if e["state"] in ("disabled", "hidden")]
    if overflow:
        issues.append(overflow)
    return {
        "screenshot": str(shot),
        "layout": _layout_summary(a11y, elements),
        "elements": elements,
        "issues": issues,
        "consoleErrors": console_errors[:MAX_CONSOLE],
        "networkErrors": network_errors[:MAX_CONSOLE],
    }


def _layout_summary(a11y: Optional[Dict[str, Any]],
                    elements: List[Dict[str, Any]]) -> str:
    """Compact structural layout from the a11y tree (preferred) or DOM."""
    parts: List[str] = []

    def walk(node: Dict[str, Any], depth: int) -> None:
        role = str(node.get("role") or "?")
        name = str(node.get("name") or "")[:60]
        if depth <= 2 and (name or role in ("banner", "main", "navigation")):
            parts.append(f"{'  ' * depth}{role}: {name}".strip())
        for child in (node.get("children") or [])[:12]:
            walk(child, depth + 1)

    if a11y:
        walk(a11y, 0)
    if not parts:
        parts = [f"{e['type']} {e['text'][:40]}" for e in elements[:8]]
    return " | ".join(parts[:10])


def capture(url: str, out_dir) -> Dict[str, Any]:
    """Return {"report": VisualReport dict, "screenshot": path}. Never raises."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import importlib.util
        if importlib.util.find_spec("playwright") is None:
            raise RuntimeError("playwright is not installed")
        data = _capture_playwright(url, out_dir)
        report = VisualReport(
            summary=(f"Captured {url} - {len(data['elements'])} elements, "
                     f"{len(data['consoleErrors'])} console errors, "
                     f"{len(data['networkErrors'])} network failures"),
            layout=data["layout"],
            elements=[VisualElement(**e) for e in data["elements"]],
            issues=data["issues"],
            consoleErrors=data["consoleErrors"],
            networkErrors=data["networkErrors"],
        )
        return {"report": report.model_dump(), "screenshot": data["screenshot"]}
    except Exception as exc:  # noqa: BLE001 - the API never 500s on capture
        logger.warning("eyes: capture failed for %s: %s", url, exc)
        empty = VisualReport(summary=f"Capture unavailable for {url}: {exc}",
                             layout="unknown")
        return {"report": empty.model_dump(), "screenshot": ""}


# --------------------------------------------------------------------------
# Diagnosis + verification (model calls)
# --------------------------------------------------------------------------

def _parse_json(text: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Parse strict JSON out of a model reply; merge over defaults."""
    out = dict(defaults)
    raw = (text or "").strip()
    if raw.count("```") >= 2:
        # Strip fences and any optional language label (```json ... ```).
        raw = raw.split("```", 2)[1].strip()
        for label in ("json", "jsonl", "JSON"):
            if raw.startswith(label + "\\n") or raw.startswith(label + "\n") or raw.startswith(label + " "):
                raw = raw[len(label):].strip()
                break
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            out.update({k: v for k, v in parsed.items() if k in defaults})
    except ValueError:
        # Tolerate literal \n/\t escapes around the JSON (common LLM habit).
        # Safe as a fallback: valid JSON with real escapes already parsed above.
        try:
            parsed = json.loads(raw.replace("\\n", "\n").replace("\\t", "\t"))
            if isinstance(parsed, dict):
                out.update({k: v for k, v in parsed.items() if k in defaults})
        except ValueError:
            out["diagnosis"] = (out.get("diagnosis") or "") + (raw[:500] if raw else "")
    return out


_DIAGNOSE_PROMPT = """You are DeepSeek Flash 1731, the primary coder of Infinity
Code X. A vision model captured the app and returned this strict VisualReport.
Use ONLY the report below - never invent elements, selectors, or errors.

VisualReport JSON:
{report}

Return STRICT JSON only with keys:
diagnosis, fix, test_plan, usability
Fix must be concrete (component, selector, code sketch); test_plan must be
runnable steps."""


def diagnose(report: VisualReport, chat_fn: Callable[[List[Dict[str, str]]], str]) -> Dict[str, Any]:
    """DeepSeek Flash 1731: diagnosis / fix / test plan / usability notes."""
    prompt = _DIAGNOSE_PROMPT.format(
        report=json.dumps(report.model_dump(), ensure_ascii=False))
    try:
        text = chat_fn([
            {"role": "system", "content": "You produce strict JSON only."},
            {"role": "user", "content": prompt},
        ])
    except Exception as exc:  # noqa: BLE001
        return {"diagnosis": f"diagnose call failed: {exc}",
                "fix": "", "test_plan": "", "usability": ""}
    return _parse_json(text, {"diagnosis": "", "fix": "", "test_plan": "", "usability": ""})


_VERIFY_PROMPT = """You are Qwen 3.8 Max, the oracle of Infinity Code X. You SEE
and ADVISE only. Compare the before and after VisualReports of the same app.

BEFORE: {before}

AFTER: {after}

Return STRICT JSON only with keys:
verdict ("improved"|"unchanged"|"regressed"), changed (bool),
issues_resolved (list), remaining (list), summary (string)"""


def verify(before: VisualReport, after: VisualReport,
           chat_fn: Callable[[List[Dict[str, str]]], str]) -> Dict[str, Any]:
    """Qwen 3.8 Max verdict on the before/after pair (advisory only)."""
    prompt = _VERIFY_PROMPT.format(
        before=json.dumps(before.model_dump(), ensure_ascii=False),
        after=json.dumps(after.model_dump(), ensure_ascii=False))
    try:
        text = chat_fn([
            {"role": "system", "content": "You produce strict JSON only."},
            {"role": "user", "content": prompt},
        ])
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "unknown", "changed": False,
                "issues_resolved": [], "remaining": [],
                "summary": f"verify call failed: {exc}"}
    return _parse_json(text, {"verdict": "unknown", "changed": False,
                              "issues_resolved": [], "remaining": [],
                              "summary": ""})


def diff_reports(before: VisualReport, after: VisualReport) -> Dict[str, Any]:
    """Deterministic before/after diff (no model call)."""
    b_issues = set(before.issues)
    a_issues = set(after.issues)
    b_cons = set(before.consoleErrors)
    a_cons = set(after.consoleErrors)
    return {
        "issues_resolved": sorted(b_issues - a_issues),
        "issues_remaining": sorted(a_issues & b_issues),
        "console_errors_resolved": len(b_cons - a_cons),
        "console_errors_new": len(a_cons - b_cons),
        "element_count_before": len(before.elements),
        "element_count_after": len(after.elements),
        "changed": bool((b_issues ^ a_issues) or (b_cons ^ a_cons)),
    }


__all__ = [
    "VisualElement", "VisualReport", "capture", "diagnose", "verify",
    "diff_reports", "DIAGNOSE_MODEL", "VERIFY_MODEL",
]
