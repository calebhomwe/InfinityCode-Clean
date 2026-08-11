"""Tests for the Design Module (backend/core/design_module.py).

Verifies the detector's recall/precision and that the playbook actually
contains the Claude-grade rules cheap models need. The injection wiring is
covered indirectly: chats.py and longtask/engine.py both import the module,
so an import-time break here fails loudly.
"""

import pytest

try:
    from backend.core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT
except ImportError:  # running with backend/ as the working directory
    from core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT


# --- 1. detector recall: design-shaped asks must trigger ------------------------- #

@pytest.mark.parametrize("text", [
    "design me a landing page",
    "make this dashboard look better",
    "build a card component with hover states",
    "styling for the settings panel",
    "dark mode theme for the app",
    "polish the navbar and hero section",
    "give the UI a visual redesign",
    "layout for a mobile settings screen",
    "make it look premium",
])
def test_design_detector_fires_on_design_asks(text: str) -> None:
    assert DESIGN_REQUEST_RE.search(text), f"should fire on: {text!r}"


# --- 2. detector precision: non-design asks must NOT trigger ---------------------- #

@pytest.mark.parametrize("text", [
    "how do i fix this bug",
    "what is 2+2",
    "refactor the api client",
    "write a python script to parse csv",
    "explain how async works",
])
def test_design_detector_stays_silent_on_code_asks(text: str) -> None:
    assert DESIGN_REQUEST_RE.search(text) is None, f"should NOT fire on: {text!r}"


# --- 3. playbook substance: the Claude-grade rules must be present ----------------- #

def test_playbook_covers_tokens() -> None:
    assert "CSS custom properties" in DESIGN_SYSTEM_PROMPT
    assert "--space" in DESIGN_SYSTEM_PROMPT


def test_playbook_covers_typography() -> None:
    assert "Modular scale" in DESIGN_SYSTEM_PROMPT
    assert "letter-spacing" in DESIGN_SYSTEM_PROMPT


def test_playbook_covers_color_contrast() -> None:
    assert "4.5:1" in DESIGN_SYSTEM_PROMPT


def test_playbook_covers_motion_and_reduced_motion() -> None:
    assert "prefers-reduced-motion" in DESIGN_SYSTEM_PROMPT
    assert "transform and opacity" in DESIGN_SYSTEM_PROMPT


def test_playbook_covers_anti_slop() -> None:
    assert "ANTI-SLOP" in DESIGN_SYSTEM_PROMPT
    assert "Three equal feature cards" in DESIGN_SYSTEM_PROMPT
    assert "emoji" in DESIGN_SYSTEM_PROMPT.lower()


def test_playbook_covers_accessibility() -> None:
    assert "semantic landmarks" in DESIGN_SYSTEM_PROMPT.lower()
    assert "focus" in DESIGN_SYSTEM_PROMPT.lower()


def test_playbook_is_lean() -> None:
    # Must stay cheap: well under 2K words keeps injection cost negligible.
    assert len(DESIGN_SYSTEM_PROMPT.split()) < 2000
