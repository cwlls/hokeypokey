"""Tests for the per-client token-bucket rate limiter."""

from __future__ import annotations

import pytest

from hokeypokey.ratelimit import RateLimiter


def test_allows_up_to_capacity():
    limiter = RateLimiter(requests=3, window=60)
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is True
    allowed, retry_after = limiter.check("client")
    assert allowed is False
    assert retry_after > 0


def test_clients_are_independent():
    limiter = RateLimiter(requests=1, window=60)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False
    assert limiter.check("b")[0] is True


def test_bucket_refills_over_time(monkeypatch):
    limiter = RateLimiter(requests=1, window=10)  # 1 token per 10s

    fake_now = 1000.0
    monkeypatch.setattr("hokeypokey.ratelimit.time.monotonic", lambda: fake_now)

    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is False

    fake_now += 10.0  # a full window passes — bucket refills one token
    assert limiter.check("client")[0] is True


def test_tracked_clients_are_bounded():
    limiter = RateLimiter(requests=1, window=60, max_clients=2)
    limiter.check("a")
    limiter.check("b")
    limiter.check("c")  # evicts "a"
    assert len(limiter._buckets) == 2
    assert "a" not in limiter._buckets


def test_invalid_parameters_rejected():
    with pytest.raises(ValueError):
        RateLimiter(requests=0, window=60)
    with pytest.raises(ValueError):
        RateLimiter(requests=10, window=0)


# ---------------------------------------------------------------------------
# client_key
# ---------------------------------------------------------------------------


def test_client_key_ignores_forwarded_for_by_default():
    limiter = RateLimiter(requests=1, window=60)
    assert limiter.client_key("10.0.0.1", "1.2.3.4") == "10.0.0.1"


def test_client_key_uses_forwarded_for_when_trusted():
    limiter = RateLimiter(requests=1, window=60, trust_forwarded_for=True)
    assert limiter.client_key("10.0.0.1", "1.2.3.4, 10.0.0.1") == "1.2.3.4"


def test_client_key_falls_back_to_unknown():
    limiter = RateLimiter(requests=1, window=60)
    assert limiter.client_key(None, None) == "unknown"
