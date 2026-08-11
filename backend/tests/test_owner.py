"""Owner recognition tests: query detection, identity line, jokes."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.owner import (  # noqa: E402
    HINT_JOKES, OWNER_ALIAS, OWNER_NAME,
    is_owner_query, maybe_owner_line, owner_line,
)


def test_owner_query_detection():
    assert is_owner_query("who owns this app?")
    assert is_owner_query("Who built Infinity Code?")
    assert is_owner_query("are you Mr X's swarm?")
    assert is_owner_query("munesu homwe made this")
    assert not is_owner_query("how do I deploy the app")
    assert not is_owner_query("")
    assert not is_owner_query(None)


def test_owner_line_mentions_identity_and_joke():
    line = owner_line(0)
    assert OWNER_NAME in line and OWNER_ALIAS in line
    assert any(joke in line for joke in HINT_JOKES)


def test_maybe_owner_line_empty_for_normal_text():
    assert maybe_owner_line("please fix the login bug") == ""


def test_maybe_owner_line_deterministic():
    a = maybe_owner_line("who owns this?")
    b = maybe_owner_line("who owns this?")
    assert a == b and a != ""


def test_maybe_owner_line_respects_custom_identity():
    line = maybe_owner_line("who owns this?", name="Alice", alias="Ace")
    assert "Alice" in line and "Ace" in line
    assert OWNER_NAME not in line
