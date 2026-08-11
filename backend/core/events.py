"""Mission event bus — the live nervous system of a run.

Why this exists
---------------
The mission WebSocket used to be a poll loop: every N seconds it re-sent the
same four scalars (status, cost, agents_active, screenshot). Everything
expressive the swarm computes — a phase starting, an attempt being scored, a
tournament candidate finishing, a red-team verdict — was invisible until the
mission ended and the user opened the evidence panel. The swarm's most
interesting moments were only ever visible in the past tense.

This bus lets the swarm *announce* things as they happen. Publishers call
`publish()` from wherever the work happens; subscribers get an async iterator.

Threading
---------
The swarm runs blocking LLM and subprocess work through `asyncio.to_thread`,
so `publish()` can be called from a worker thread OR from the event loop.
Delivery therefore always hops through `loop.call_soon_threadsafe`, which is
safe from both. The loop is bound once at startup via `bind_loop()`.

Backpressure
------------
Each subscriber has a bounded queue. A consumer too slow to keep up drops
events rather than growing memory without limit — a live view that has fallen
behind is better served by the next `sync` than by a backlog. Every event
carries a monotonic `seq`, so a client can detect that it missed some.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, Iterator, List, Optional, Set

logger = logging.getLogger(__name__)

# Per-mission replay buffer. Enough to reconstruct a run's phase timeline for a
# client that connects late, without holding a whole mission's chatter forever.
HISTORY_PER_MISSION = 250
# Per-subscriber queue depth before we start dropping for that consumer only.
SUBSCRIBER_QUEUE_MAX = 500


class MissionEventBus:
    """Fan-out of typed mission events to any number of live subscribers."""

    def __init__(
        self,
        history_per_mission: int = HISTORY_PER_MISSION,
        queue_max: int = SUBSCRIBER_QUEUE_MAX,
    ) -> None:
        self._history_per_mission: int = int(history_per_mission)
        self._queue_max: int = int(queue_max)
        self._subscribers: Dict[str, Set["asyncio.Queue[Dict[str, Any]]"]] = {}
        self._history: Dict[str, Deque[Dict[str, Any]]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._dropped: int = 0

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Remember the loop that subscriber queues live on.

        Called once during app startup. Publishers may run on worker threads,
        which have no running loop of their own, so the target loop has to be
        captured here rather than discovered at publish time.
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("bind_loop() called with no running loop; events will not stream.")
                return
        self._loop = loop

    # ------------------------------------------------------------------ #
    # Publishing
    # ------------------------------------------------------------------ #

    def publish(self, mission_id: str, event_type: str, **data: Any) -> Dict[str, Any]:
        """Announce one thing that just happened. Safe from any thread.

        Never raises: a telemetry failure must not be able to kill a mission.
        """
        event: Dict[str, Any] = {
            "type": event_type,
            "mission_id": mission_id,
            "seq": next(self._seq),
            "at": time.time(),
        }
        event.update(data)

        try:
            with self._lock:
                history = self._history.get(mission_id)
                if history is None:
                    history = deque(maxlen=self._history_per_mission)
                    self._history[mission_id] = history
                history.append(event)
                targets = list(self._subscribers.get(mission_id, ()))
        except Exception as exc:  # noqa: BLE001 - telemetry must not break the run
            logger.error("Event bookkeeping failed for %s: %s", mission_id, exc)
            return event

        for queue in targets:
            self._deliver(queue, event)
        return event

    def _deliver(self, queue: "asyncio.Queue[Dict[str, Any]]", event: Dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._put_nowait, queue, event)
        except RuntimeError:
            # Loop shut down between the check and the call — the subscriber is
            # going away anyway.
            pass

    def _put_nowait(
        self, queue: "asyncio.Queue[Dict[str, Any]]", event: Dict[str, Any]
    ) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning(
                    "Dropped %d event(s) for slow subscriber(s); client should resync.",
                    self._dropped,
                )

    # ------------------------------------------------------------------ #
    # Subscribing
    # ------------------------------------------------------------------ #

    def history(self, mission_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        """Replay buffer for a client that connected late or missed events."""
        with self._lock:
            events = list(self._history.get(mission_id, ()))
        return [e for e in events if e.get("seq", 0) > after_seq]

    @contextlib.contextmanager
    def subscription(self, mission_id: str) -> Iterator["asyncio.Queue[Dict[str, Any]]"]:
        """Lend out a live queue of events for a mission, cleaned up on exit.

        A plain queue rather than an async generator on purpose: callers need to
        read with a timeout, and `wait_for(agen.__anext__())` leaves an async
        generator in a broken state when the timeout cancels it. A queue can be
        polled with `wait_for` safely as many times as you like.
        """
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=self._queue_max)
        with self._lock:
            self._subscribers.setdefault(mission_id, set()).add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                subs = self._subscribers.get(mission_id)
                if subs is not None:
                    subs.discard(queue)
                    if not subs:
                        self._subscribers.pop(mission_id, None)

    async def subscribe(self, mission_id: str) -> AsyncIterator[Dict[str, Any]]:
        """Yield events for a mission until the consumer stops iterating."""
        with self.subscription(mission_id) as queue:
            while True:
                yield await queue.get()

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #

    def forget(self, mission_id: str) -> None:
        """Drop the replay buffer for a mission (called when it is deleted)."""
        with self._lock:
            self._history.pop(mission_id, None)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "missions_buffered": len(self._history),
                "live_subscribers": sum(len(s) for s in self._subscribers.values()),
                "events_dropped": self._dropped,
            }


__all__ = ["MissionEventBus", "HISTORY_PER_MISSION", "SUBSCRIBER_QUEUE_MAX"]
