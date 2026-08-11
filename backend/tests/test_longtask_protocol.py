"""Tests for the Long Task action protocol parser."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.protocol import ProtocolError, parse_actions  # noqa: E402


def test_parses_fenced_json():
    txt = 'Sure.\n```action\n{"action":"read_file","args":{"path":"a.py"}}\n```\n'
    acts = parse_actions(txt)
    assert acts == [{"action": "read_file", "args": {"path": "a.py"}}]


def test_parses_multiple_nested_args():
    txt = ('{"action":"write_file","args":{"path":"b.py","content":"x"}}\n'
           '{"action":"finish","args":{"summary":"done"}}')
    acts = parse_actions(txt)
    assert len(acts) == 2 and acts[1]["action"] == "finish"
    assert acts[0]["args"]["content"] == "x"


def test_parses_bare_json_object():
    acts = parse_actions('{"action":"list_dir","args":{}}')
    assert acts[0]["action"] == "list_dir"


def test_ignores_unknown_actions_but_keeps_known():
    txt = ('{"action":"dance","args":{}} '
           '{"action":"finish","args":{"summary":"ok"}}')
    acts = parse_actions(txt)
    assert len(acts) == 1 and acts[0]["action"] == "finish"


def test_rejects_garbage():
    with pytest.raises(ProtocolError):
        parse_actions("I will now edit the files.")
