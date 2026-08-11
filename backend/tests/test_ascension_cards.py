"""Model cards for the Council UI: roster shape, labels, form membership.

Roster is the dashscope-centric council (ascension.py _MODEL_META). If the
council changes, update _MODEL_META AND these tests together.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.ascension import AscensionEngine  # noqa: E402


def _engine():
    return AscensionEngine(log_path=str(Path(__file__).parent / "noop.jsonl"))


def test_model_cards_cover_full_roster():
    cards = {c["id"]: c for c in _engine().model_cards()}
    assert set(cards) == {
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
        "dashscope/qwen-coder-plus",
        "dashscope/qwen-plus",
        "dashscope/qwen-max",
        "dashscope/qwen3.8-max",
        "local/fable-max-llamacpp",
    }


def test_model_cards_have_labels():
    cards = {c["id"]: c for c in _engine().model_cards()}
    assert cards["dashscope/qwen3.7-flash"]["label"] == "Qwen 3.7 Flash"
    assert cards["dashscope/qwen3.8-max"]["label"] == "Qwen 3.8 Max"
    assert cards["dashscope/qwen-turbo"]["label"] == "Qwen Turbo"
    assert cards["local/fable-max-llamacpp"]["label"] == "Local FABLE"


def test_model_cards_form_membership_and_min_form():
    cards = {c["id"]: c for c in _engine().model_cards()}
    assert cards["dashscope/qwen-turbo"]["min_form"] == "X Code"
    assert cards["dashscope/qwen3.7-flash"]["min_form"] == "SS1"
    assert cards["dashscope/qwen-coder-plus"]["min_form"] == "SS2"
    assert "Mr X Final" in cards["local/fable-max-llamacpp"]["forms"]
    # Qwen 3.8 Max is an oracle, not a builder -- its forms are council-only.
    assert "X Code" not in cards["dashscope/qwen3.8-max"]["forms"]


def test_model_cards_shape():
    card = _engine().model_cards()[0]
    for key in ("id", "label", "role", "tier", "available", "locked",
                "forms", "min_form"):
        assert key in card
    assert isinstance(card["available"], bool)
