"""Router integration: ascension form policy constrains lane chains."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.router import (  # noqa: E402
    LaneRouter,
    apply_ascension,
    LANE_CHEAP,
    LANE_SMART,
)

FREE = ("dashscope/qwen-turbo",)
SS1_APPROVED = ("dashscope/qwen-turbo", "dashscope/qwen3.7-flash")
BLUE_APPROVED = (
    "dashscope/qwen-turbo", "dashscope/qwen3.7-flash",
    "dashscope/qwen-plus", "dashscope/qwen3.8-max",
)


def test_ss1_demotes_smart_lane_to_cheap():
    router = LaneRouter()
    lane, specs = router.route_chain(
        complexity="complex", ascension_state="SS1", approved=SS1_APPROVED)
    assert lane == LANE_CHEAP  # smart lane is locked out of SS1
    assert specs and specs[0].id == "dashscope/qwen3.7-flash"


def test_x_code_never_selects_qwen():
    router = LaneRouter()
    lane, specs = router.route_chain(
        ascension_state="X Code", approved=FREE)
    assert lane == LANE_CHEAP
    assert all(s.id != "dashscope/qwen3.8-max" for s in specs)


def test_blue_filters_locked_models_and_qwen():
    router = LaneRouter()
    lane, specs = router.route_chain(
        complexity="complex", ascension_state="Blue", approved=BLUE_APPROVED,
        available=lambda mid: mid != "dashscope/qwen-max")
    assert lane == LANE_SMART
    ids = [s.id for s in specs]
    assert "dashscope/qwen3.8-max" not in ids   # role lock: oracle never drafts
    assert "dashscope/qwen-max" not in ids      # locked model filtered
    assert ids[0] == "dashscope/qwen-plus"


def test_unknown_form_falls_back_to_x_code_policy():
    router = LaneRouter()
    lane, specs = router.route_chain(
        complexity="complex", ascension_state="???", approved=FREE)
    assert lane == LANE_CHEAP
    assert specs[0].id == "dashscope/qwen-turbo"


def test_x_code_coding_task_falls_back_to_free_router():
    router = LaneRouter()
    lane, specs = router.route_chain(
        task_type="debug", complexity="complex",
        ascension_state="X Code", approved=FREE)
    assert lane == LANE_CHEAP
    assert specs[0].id == "dashscope/qwen-turbo"


def test_qwen_removed_even_when_approved():
    from backend.core.router import _spec_or_default
    specs = apply_ascension(
        "Blue", LANE_CHEAP,
        (_spec_or_default("dashscope/qwen3.8-max"),),
        approved=("dashscope/qwen3.8-max",))
    assert specs[1][0].id == "dashscope/qwen3.7-flash"  # default coder
