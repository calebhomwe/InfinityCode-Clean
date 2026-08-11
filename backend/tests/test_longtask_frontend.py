"""W3 tests: frontend playbook injection + vision downscale."""
import json
from pathlib import Path

import pytest

try:
    from backend.tools.openrouter_client import (
        downscale_image_bytes,
        encode_image_base64,
    )
except ImportError:  # tests run with backend/ as cwd
    from tools.openrouter_client import (  # type: ignore
        downscale_image_bytes,
        encode_image_base64,
    )

import main

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class _FakeSkill:
    """Stands in for SkillEngine: only serves the seeded playbook."""

    def __init__(self, data):
        self.data = data
        self.recorded = []

    def get_skill(self, name):
        return self.data if name == "frontend-vibe" else None

    def record_result(self, name, success):
        self.recorded.append((name, success))


def _seed_playbook():
    return json.loads((SKILLS_DIR / "frontend-vibe.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Playbook condensation + gating
# --------------------------------------------------------------------------- #

def test_playbook_loads_and_condenses_under_1500_chars(monkeypatch):
    monkeypatch.setattr(main.app.state, "skill", _FakeSkill(_seed_playbook()),
                        raising=False)
    text, name = main._frontend_playbook("build a landing page with a hero UI")
    assert name == "frontend-vibe"
    assert text.startswith("FRONTEND PLAYBOOK")
    assert len(text) <= 1500
    assert "design tokens" in text.lower()


def test_playbook_only_injected_for_frontend_goals(monkeypatch):
    monkeypatch.setattr(main.app.state, "skill", _FakeSkill(_seed_playbook()),
                        raising=False)
    text, name = main._frontend_playbook("refactor the csv parser to stream rows")
    assert text == ""
    assert name is None


def test_playbook_prefers_learned_over_seeded(monkeypatch):
    learned = {"name": "frontend-vibe-learned",
               "steps": [{"order": 1, "instruction": "learned rule"}]}

    class _Both(_FakeSkill):
        def get_skill(self, name):
            if name == "frontend-vibe-learned":
                return learned
            return self.data

    monkeypatch.setattr(main.app.state, "skill", _Both(_seed_playbook()),
                        raising=False)
    text, name = main._frontend_playbook("style the dashboard page")
    assert name == "frontend-vibe-learned"
    assert "learned rule" in text


# --------------------------------------------------------------------------- #
# Vision downscale
# --------------------------------------------------------------------------- #

def test_downscale_shrinks_oversized_image():
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    import io

    # true random noise -> incompressible, comfortably over the 1MB threshold
    img = Image.effect_noise((3000, 3000), 128).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) > 1_000_000

    out = downscale_image_bytes(raw)
    assert len(out) < len(raw)
    resized = Image.open(io.BytesIO(out))
    assert max(resized.size) <= 1280


def test_downscale_returns_original_on_garbage():
    data = b"not an image at all"
    assert downscale_image_bytes(data) is data


def test_encode_image_base64_downsizes_large_file(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    import base64
    import io

    img = Image.effect_noise((2500, 2500), 128).convert("RGB")
    p = tmp_path / "big.png"
    img.save(p, format="PNG")
    if p.stat().st_size <= 1_000_000:
        pytest.skip("could not generate an oversized image")

    b64 = encode_image_base64(p)
    decoded = base64.b64decode(b64)
    assert len(decoded) < p.stat().st_size
    assert max(Image.open(io.BytesIO(decoded)).size) <= 1280
