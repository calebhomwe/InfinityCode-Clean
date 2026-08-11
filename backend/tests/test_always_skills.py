"""Owner-pinned plugin skills load every session (always: true)."""
import json
from pathlib import Path

try:
    from backend.core.skill_engine import SkillEngine
except ImportError:  # backend/ on path
    from core.skill_engine import SkillEngine  # type: ignore[no-redef]


def _write(tmp_path: Path, name: str, always: bool) -> None:
    (tmp_path / f"{name}.json").write_text(json.dumps({
        "name": name, "topic": "t", "description": f"{name} summary",
        "always": always,
        "steps": [{"order": 1, "instruction": "do the thing"}],
    }), encoding="utf-8")


def test_always_on_returns_only_pinned_skills(tmp_path):
    _write(tmp_path, "qwencloud-text", True)
    _write(tmp_path, "autonomous-coder-loop", True)
    _write(tmp_path, "random-note", False)
    eng = SkillEngine(skills_dir=tmp_path, db_path=tmp_path / "skills.db")

    names = {h["name"] for h in eng.always_on()}
    assert names == {"qwencloud-text", "autonomous-coder-loop"}


def test_always_on_hits_have_search_shape(tmp_path):
    _write(tmp_path, "qwencloud-vision", True)
    eng = SkillEngine(skills_dir=tmp_path, db_path=tmp_path / "skills.db")

    hits = eng.always_on()
    assert len(hits) == 1
    hit = hits[0]
    assert hit["name"] == "qwencloud-vision"
    assert "description" in hit and "step_hints" in hit
    assert hit["step_hints"] == ["do the thing"]


def test_always_on_loads_even_when_query_does_not_match(tmp_path):
    _write(tmp_path, "qwencloud-video-generation", True)
    eng = SkillEngine(skills_dir=tmp_path, db_path=tmp_path / "skills.db")

    # A query with zero keyword overlap still sees the pinned skill.
    merged = eng.always_on() + [
        h for h in eng.search("totally unrelated query", top_k=3)
    ]
    assert {h["name"] for h in merged} == {"qwencloud-video-generation"}


def test_seed_skills_ship_the_owner_plugin_set():
    seed_dir = Path(__file__).resolve().parents[1] / "data" / "seed_skills"
    expected = {
        "qwencloud-model-selector", "qwencloud-text", "qwencloud-vision",
        "qwencloud-video-generation", "autonomous-coder-loop",
    }
    for name in expected:
        path = seed_dir / f"{name}.json"
        assert path.is_file(), f"missing seed skill {name}"
        skill = json.loads(path.read_text(encoding="utf-8"))
        assert skill.get("always") is True
        assert skill.get("description"), "summary prompt must not be empty"
        assert len(skill["description"]) <= 280


def test_skill_file_with_utf8_bom_still_loads(tmp_path):
    # Notepad-style BOM must not make a skill unreadable (owner's vision.json
    # hit exactly this 2026-08-11).
    body = json.dumps({"name": "bom-skill", "topic": "t",
                       "description": "bom summary",
                       "steps": [{"order": 1, "instruction": "x"}]})
    (tmp_path / "bom-skill.json").write_bytes(
        b"\xef\xbb\xbf" + body.encode("utf-8"))
    eng = SkillEngine(skills_dir=tmp_path, db_path=tmp_path / "skills.db")

    skill = eng.get_skill("bom-skill")
    assert skill is not None and skill["name"] == "bom-skill"
