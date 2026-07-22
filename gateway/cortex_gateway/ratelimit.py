"""Per-caller token-bucket rate limiting.

In-process (single Gateway process behind the tunnel), keyed by bearer token
when present, else client IP. Two buckets: an authenticated bucket (generous)
and an anonymous bucket (tight - protects the OAuth/discovery endpoints that
run before a token exists).

Returns (allowed, retry_after_seconds). The middleware in app.py turns a
disallowed result into HTTP 429 with a Retry-After header.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float = field(default=0.0)
    last: float = field(default_factory=time.monotonic)

    def take(self, n: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        deficit = n - self.tokens
        return False, deficit / self.refill_per_sec


class RateLimiter:
    def __init__(self, *, auth_rpm: int = 120, anon_rpm: int = 60,
                 burst_factor: float = 2.0) -> None:
        self._auth_rate = auth_rpm / 60.0
        self._anon_rate = anon_rpm / 60.0
        self._auth_cap = auth_rpm * burst_factor / 60.0 * 10  # ~burst headroom
        self._anon_cap = max(5.0, anon_rpm * burst_factor / 60.0 * 10)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, authenticated: bool) -> tuple[bool, float]:
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                if authenticated:
                    b = _Bucket(self._auth_cap, self._auth_rate, tokens=self._auth_cap)
                else:
                    b = _Bucket(self._anon_cap, self._anon_rate, tokens=self._anon_cap)
                self._buckets[key] = b
            return b.take(1.0)


# Module-level default limiter.
limiter = RateLimiter()
