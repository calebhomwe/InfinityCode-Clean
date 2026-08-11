"""Eyes Module tests: schema validation, capture fallback, diffs, model
prompt contracts (fake chat_fn, no network)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from backend.core.eyes import (  # noqa: E402
    VisualElement, VisualReport, capture, diagnose, verify, diff_reports,
    DIAGNOSE_MODEL, VERIFY_MODEL,
)


def _report(**over):
    data = {
        "summary": "s", "layout": "main: x",
        "elements": [{"id": "b1", "type": "button", "text": "Go",
                      "selector": "#b1", "state": "visible", "box": [0, 0, 10, 5]}],
        "issues": ["button 'Go' is disabled"],
        "consoleErrors": ["boom"], "networkErrors": [],
    }
    data.update(over)
    return VisualReport(**data)


def test_visual_report_schema_roundtrip():
    r = _report()
    back = VisualReport.model_validate(r.model_dump())
    assert back.elements[0].box == [0, 0, 10, 5]


def test_box_must_be_quad():
    with pytest.raises(ValueError):
        VisualElement(id="x", type="div", text="", selector="",
                      state="visible", box=[1, 2, 3])


def test_capture_never_raises(tmp_path):
    result = capture("http://127.0.0.1:1/unreachable", tmp_path)
    assert "report" in result and "screenshot" in result
    assert isinstance(result["report"], dict)


def test_capture_validates_report_schema(tmp_path):
    result = capture("http://127.0.0.1:1/unreachable", tmp_path)
    VisualReport(**result["report"])  # must not raise


def test_diff_reports_tracks_resolution():
    before = _report()
    after = _report(issues=[], consoleErrors=[])
    d = diff_reports(before, after)
    assert d["issues_resolved"] == ["button 'Go' is disabled"]
    assert d["issues_remaining"] == []
    assert d["changed"] is True
    same = diff_reports(before, _report())
    assert same["changed"] is False


def test_diagnose_parses_strict_json():
    def fake_chat(messages):
        return '{"diagnosis": "button hidden", "fix": "show it", "test_plan": "click", "usability": "ok"}'
    out = diagnose(_report(), fake_chat)
    assert out["diagnosis"] == "button hidden"
    assert out["fix"] == "show it"


def test_diagnose_tolerates_code_fences():
    def fake_chat(messages):
        return '```json\\n{"diagnosis": "x", "fix": "y"}\\n```'
    out = diagnose(_report(), fake_chat)
    assert out["diagnosis"] == "x"


def test_diagnose_survives_garbage():
    def fake_chat(messages):
        return "sorry, no json"
    out = diagnose(_report(), fake_chat)
    assert out["diagnosis"] and out["fix"] == ""


def test_verify_parses_verdict():
    def fake_chat(messages):
        return '{"verdict": "improved", "changed": true, "issues_resolved": ["a"], "remaining": [], "summary": "ok"}'
    out = verify(_report(), _report(), fake_chat)
    assert out["verdict"] == "improved" and out["changed"] is True


def test_model_bindings_are_role_locked():
    assert DIAGNOSE_MODEL == "deepseek/deepseek-v4-flash"
    assert VERIFY_MODEL == "dashscope/qwen3.8-max"
