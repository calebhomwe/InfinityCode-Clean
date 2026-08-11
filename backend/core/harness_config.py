"""Versioned harness config (cross-cutting) — the moat is the harness, not the model.

Encodes the K3 playbook schema as a typed, round-trippable config object that
can be stored per task class, versioned, and A/B'd. Defaults mirror the
playbook so a fresh install behaves sensibly out of the box.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ContextCfg:
    prefix_stable: list = field(default_factory=lambda: [
        "rules", "reference_dna", "skills_index", "tool_declare", "effort"])
    one_shot_at_tail: list = field(default_factory=lambda: [
        "mission_delta", "response_format", "tool_choice"])
    compaction: Dict[str, Any] = field(default_factory=lambda: {
        "trigger_tokens": 60000, "summarize": "tool_outputs",
        "keep_verbatim": "references"})
    firewall: str = "subagents_return_summaries_only"


@dataclass
class ToolsCfg:
    per_role_subsets: bool = True
    typed_args: bool = True
    indexed_parallel: bool = True
    dynamic_load: bool = True


@dataclass
class EffortCfg:
    level: str = "high"
    budget_multiplier: float = 1.0
    verbosity_cap: str = "1.3x_baseline"


@dataclass
class VerifierCfg:
    build: bool = True
    tests: bool = True
    lint: bool = True
    pixel_diff_vs_reference: bool = True
    anti_fake: bool = True
    hidden_checks: bool = True


@dataclass
class JudgeCfg:
    protocol: list = field(default_factory=lambda: [
        "read", "rubric", "score", "scorepad"])
    fork_sandbox: bool = True
    submission_cap: int = 3


@dataclass
class LoopCfg:
    repair_with_exact_errors: bool = True
    close_only_on_green: bool = True


@dataclass
class MemoryCfg:
    repo_wiki: bool = True
    references: bool = True
    decisions: bool = True
    accepted_artifacts_as_fewshot: bool = True


@dataclass
class TrainingCfg:
    teachers: list = field(default_factory=lambda: ["cheap APIs"])
    student: str = "hosted_small"
    refresh: str = "weekly_lora"


@dataclass
class HarnessConfig:
    context: ContextCfg = field(default_factory=ContextCfg)
    tools: ToolsCfg = field(default_factory=ToolsCfg)
    effort: EffortCfg = field(default_factory=EffortCfg)
    verifier: VerifierCfg = field(default_factory=VerifierCfg)
    judge: JudgeCfg = field(default_factory=JudgeCfg)
    loop: LoopCfg = field(default_factory=LoopCfg)
    memory: MemoryCfg = field(default_factory=MemoryCfg)
    training: TrainingCfg = field(default_factory=TrainingCfg)


def default_config() -> HarnessConfig:
    return HarnessConfig()


def to_dict(cfg: HarnessConfig) -> Dict[str, Any]:
    return {"harness": asdict(cfg)}


def _build_section(cls, data: Optional[Dict[str, Any]]):
    base = cls()
    if not isinstance(data, dict):
        return base
    for k, v in data.items():
        if hasattr(base, k):
            setattr(base, k, v)
    return base


def from_dict(data: Dict[str, Any]) -> HarnessConfig:
    h = (data or {}).get("harness", data or {})
    return HarnessConfig(
        context=_build_section(ContextCfg, h.get("context")),
        tools=_build_section(ToolsCfg, h.get("tools")),
        effort=_build_section(EffortCfg, h.get("effort")),
        verifier=_build_section(VerifierCfg, h.get("verifier")),
        judge=_build_section(JudgeCfg, h.get("judge")),
        loop=_build_section(LoopCfg, h.get("loop")),
        memory=_build_section(MemoryCfg, h.get("memory")),
        training=_build_section(TrainingCfg, h.get("training")),
    )


def to_yaml(cfg: HarnessConfig) -> str:
    import yaml
    return yaml.safe_dump(to_dict(cfg), sort_keys=False, default_flow_style=False)


def save(cfg: HarnessConfig, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(cfg), encoding="utf-8")
    return path


def load(path) -> HarnessConfig:
    """Load a harness config from YAML; fall back to defaults on any error."""
    try:
        import yaml
        text = Path(path).read_text(encoding="utf-8")
        return from_dict(yaml.safe_load(text) or {})
    except Exception:  # noqa: BLE001 - a broken config never blocks a run
        return default_config()


__all__ = ["HarnessConfig", "default_config", "to_dict", "from_dict",
           "to_yaml", "save", "load"]
