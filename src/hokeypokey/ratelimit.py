"""Per-client token-bucket rate limiting for the HKP lookup endpoint.

Every cache-miss lookup fans out to upstream sources (LDAP, the GitHub API),
so an unauthenticated client could otherwise burn upstream rate limits or
hammer a corporate directory.  This module provides a small in-memory token
bucket keyed by client address.

Like :mod:`hokeypokey.cache`, this module is intentionally single-threaded
(asyncio event loop only) — there is no locking.
"""

from __future__ import annotations

import time
from collections import OrderedDict

# Upper bound on tracked clients; least-recently-seen buckets are evicted
# first, so an address-spoofing flood cannot grow memory without limit.
_MAX_TRACKED_CLIENTS = 10_000


class RateLimiter:
    """Token-bucket rate limiter keyed by client identifier.

    Each client starts with a full bucket of *requests* tokens which refills
    continuously over *window* seconds.  A request consumes one token; a
    client with an empty bucket is rejected and told how long to wait.
    """

    def __init__(
        self,
        requests: int,
        window: float,
        trust_forwarded_for: bool = False,
        max_clients: int = _MAX_TRACKED_CLIENTS,
    ) -> None:
        if requests <= 0:
            raise ValueError("requests must be a positive integer")
        if window <= 0:
            raise ValueError("window must be positive")
        self._capacity = float(requests)
        self._refill_rate = requests / window  # tokens per second
        self._trust_forwarded_for = trust_forwarded_for
        self._max_clients = max_clients
        # client key → (tokens remaining, monotonic timestamp of last update)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def client_key(self, remote_addr: str | None, forwarded_for: str | None) -> str:
        """Derive the rate-limit key for a request.

        ``X-Forwarded-For`` is only honoured when *trust_forwarded_for* is
        enabled (i.e. the server sits behind a trusted reverse proxy) —
        otherwise clients could spoof the header to bypass their limit.
        """
        if self._trust_forwarded_for and forwarded_for:
            first_hop = forwarded_for.split(",")[0].strip()
            if first_hop:
                return first_hop
        return remote_addr or "unknown"

    def check(self, client_key: str) -> tuple[bool, float]:
        """Consume one token for *client_key*.

        Returns:
            ``(allowed, retry_after_seconds)`` — *retry_after_seconds* is 0.0
            when the request is allowed.
        """
        now = time.monotonic()
        tokens, last = self._buckets.get(client_key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill_rate)

        if tokens >= 1.0:
            allowed = True
            tokens -= 1.0
            retry_after = 0.0
        else:
            allowed = False
            retry_after = (1.0 - tokens) / self._refill_rate

        self._buckets[client_key] = (tokens, now)
        self._buckets.move_to_end(client_key)
        while len(self._buckets) > self._max_clients:
            self._buckets.popitem(last=False)

        return allowed, retry_after
