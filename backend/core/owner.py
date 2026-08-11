"""Owner recognition for Infinity Code X.

Munesu Homwe — Mr X — owns Infinity Code X. Chat answers who-owns questions
with the identity line plus a rotating hint joke, and Mr X holds final
override authority through the Ascension Engine's passphrase gate.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

OWNER_NAME: str = "Munesu Homwe"
OWNER_ALIAS: str = "Mr X"

# Original, anime-flavoured hint jokes (no copyrighted material).
HINT_JOKES: Tuple[str, ...] = (
    "Mr X doesn't transform himself — the Ascension Engine transforms because he says so.",
    "They say the final form isn't the swarm. It's whoever tells the swarm to ascend.",
    "Mr X once de-escalated a coding marathon with two words. The cooldown is still running.",
    "The only model Mr X hasn't tuned is the one holding his coffee.",
)

_OWNER_QUERY_MARKERS: Tuple[str, ...] = (
    "who owns", "who built", "who created", "who made",
    "owner", "mr x", "munesu", "homwe",
)


def owner_line(index: int = 0, name: str = OWNER_NAME, alias: str = OWNER_ALIAS) -> str:
    """Identity line + one hint joke (deterministic by index)."""
    return (
        f"Infinity Code X is owned and directed by {name} — {alias}. "
        f"{HINT_JOKES[index % len(HINT_JOKES)]}"
    )


def is_owner_query(text: Optional[str]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _OWNER_QUERY_MARKERS)


def maybe_owner_line(text: Optional[str], name: str = OWNER_NAME,
                     alias: str = OWNER_ALIAS) -> str:
    """Identity + hint joke when the user asks about ownership, else ''.
    Deterministic: the same question always gets the same joke."""
    if not is_owner_query(text):
        return ""
    seed = int(hashlib.md5((text or "").encode("utf-8")).hexdigest(), 16)
    return owner_line(seed % len(HINT_JOKES), name=name, alias=alias)


__all__ = [
    "OWNER_NAME", "OWNER_ALIAS", "HINT_JOKES",
    "owner_line", "is_owner_query", "maybe_owner_line",
]
