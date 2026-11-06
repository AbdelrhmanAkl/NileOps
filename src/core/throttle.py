"""In-memory login throttling (brute-force protection).

Two independent counters:
* per (username, client address): 5 failures in 15 minutes lock that pair for 15 minutes,
  so an attacker cannot lock a real employee out from another address;
* per client address: 20 failures in 15 minutes lock that address.

State lives in this process only. With several workers or servers each keeps its
own counters (still useful, but weaker); use a shared store such as Redis at scale.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_TRACKED_KEYS = 10_000


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginThrottle:
    def __init__(
        self,
        pair_limit: int = 5,
        address_limit: int = 20,
        window_seconds: int = 900,
        lock_seconds: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pair_limit = pair_limit
        self.address_limit = address_limit
        self.window = window_seconds
        self.lock = lock_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._mutex = threading.Lock()

    @staticmethod
    def keys(username: str, address: str) -> tuple[str, str]:
        return f"pair:{username.strip().lower()}|{address}", f"addr:{address}"

    def retry_after(self, username: str, address: str) -> int:
        """Seconds until a new attempt is allowed (0 = allowed now)."""
        now = self._clock()
        with self._mutex:
            remaining = [
                self._entries[k].locked_until - now
                for k in self.keys(username, address)
                if k in self._entries
            ]
        return max(0, int(max(remaining, default=0)) + 1) if any(r > 0 for r in remaining) else 0

    def register_failure(self, username: str, address: str) -> bool:
        """Record a failed attempt. Returns True when this failure caused a new lock."""
        now = self._clock()
        newly_locked = False
        pair_key, addr_key = self.keys(username, address)
        with self._mutex:
            if len(self._entries) > MAX_TRACKED_KEYS:
                self._entries = {k: e for k, e in self._entries.items() if e.locked_until > now}
            for key, limit in ((pair_key, self.pair_limit), (addr_key, self.address_limit)):
                entry = self._entries.setdefault(key, _Entry())
                entry.failures = [t for t in entry.failures if now - t < self.window] + [now]
                if len(entry.failures) >= limit and entry.locked_until <= now:
                    entry.locked_until = now + self.lock
                    entry.failures.clear()
                    newly_locked = newly_locked or key == pair_key
        return newly_locked

    def register_success(self, username: str, address: str) -> None:
        with self._mutex:
            self._entries.pop(self.keys(username, address)[0], None)

    def reset(self) -> None:
        with self._mutex:
            self._entries.clear()


login_throttle = LoginThrottle()


# Self-registration: at most 5 new customer accounts per client address per hour.
register_throttle = LoginThrottle(pair_limit=5, address_limit=5, window_seconds=3600, lock_seconds=3600)
