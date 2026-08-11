"""Weekly LoRA refresh scaffold (P10).

This does NOT run a fine-tune — it generates the configuration for one. The
actual training runs on Caleb's chosen free/local stack; here we just emit a
reproducible config object from the collected dataset so the refresh is a
single command away.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass
class LoRAConfig:
    student_model: str = "local/fable-small"
    base_checkpoint: str = ""
    dataset_path: str = "data/training/dataset.jsonl"
    rank: int = 8
    lora_alpha: int = 16
    dropout: float = 0.05
    learning_rate: float = 1e-4
    epochs: int = 3
    batch_size: int = 4
    refresh: str = "weekly"
    teachers: list = field(default_factory=lambda: ["builder", "reviewer", "judge"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_yaml(self) -> str:
        """Hand-rolled YAML (no external dependency)."""
        lines = ["lora:"]
        for k, v in self.to_dict().items():
            if k == "teachers":
                lines.append("  teachers:")
                lines.extend(f"    - {t}" for t in v)
            elif isinstance(v, str):
                lines.append(f'  {k}: "{v}"')
            else:
                lines.append(f"  {k}: {v}")
        return "\n".join(lines) + "\n"


__all__ = ["LoRAConfig"]
