"""In-memory key cache with multi-field search indexes and optional LRU eviction.

This module is intentionally single-threaded (asyncio event loop only).
Do not call cache methods from threads — there is no locking.
"""

from __future__ import annotations

import time
from collections import OrderedDict, defaultdict

from hokeypokey.models import CachedKey, SourceKey


class KeyCache:
    """In-memory store for cached GPG keys with priority-aware deduplication.

    Keys are stored by fingerprint (uppercase hex, no ``0x`` prefix) and
    indexed by long key ID, short key ID, email address, and arbitrary
    custom metadata fields.

    Priority semantics
    ------------------
    Each :class:`~hokeypokey.models.SourceKey` carries a ``source_priority``
    integer.  Lower numbers are *more* authoritative.  When :meth:`put` is
    called with a fingerprint that is already cached:

    - If the existing entry has a *lower* priority number (higher authority),
      the new entry is silently ignored.
    - If the existing entry has the *same or higher* priority number, the new
      entry replaces it.

    This means a key from ``priority=10`` (LDAP) will never be overwritten by
    a key from ``priority=50`` (GitHub), but the reverse replacement is allowed.

    LRU eviction
    ------------
    When *max_size* is set, the cache evicts the least-recently-used entry
    whenever a new entry would push the size over the limit.  Access via
    :meth:`get_by_fingerprint`, :meth:`get_by_key_id`, and :meth:`search`
    all count as "use" and refresh the LRU order.  When *max_size* is ``None``
    (the default), the cache grows without bound.
    """

    def __init__(self, max_size: int | None = None) -> None:
        self._max_size = max_size

        # Primary store: fingerprint → CachedKey (OrderedDict for LRU ordering)
        self._store: OrderedDict[str, CachedKey] = OrderedDict()

        # Index: last-16-hex → set of fingerprints
        self._by_long_id: dict[str, set[str]] = defaultdict(set)

        # Index: last-8-hex → set of fingerprints
        self._by_short_id: dict[str, set[str]] = defaultdict(set)

        # Index: lowercase email → set of fingerprints
        self._by_email: dict[str, set[str]] = defaultdict(set)

        # Index: field_name → { value → set of fingerprints }
        self._by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

        # Approximate hit/miss counters (see stats()).
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_fp(fp: str) -> str:
        """Return *fp* as uppercase hex without any ``0x`` prefix."""
        fp = fp.strip()
        if fp.upper().startswith("0X"):
            fp = fp[2:]
        return fp.upper()

    def _index_key(self, fp: str, key: SourceKey) -> None:
        """Add *fp* to all secondary indexes based on *key*'s metadata."""
        # Key-ID indexes
        if len(fp) >= 16:
            self._by_long_id[fp[-16:]].add(fp)
        if len(fp) >= 8:
            self._by_short_id[fp[-8:]].add(fp)

        # Email index
        email = key.metadata.get("email", "").strip().lower()
        if email:
            self._by_email[email].add(fp)

        # Generic metadata field index
        for field_name, value in key.metadata.items():
            if field_name == "email":
                continue  # already handled above
            if value:
                self._by_field[field_name][value.lower()].add(fp)

    def _deindex_key(self, fp: str, key: SourceKey) -> None:
        """Remove *fp* from all secondary indexes.

        Index entries whose fingerprint set becomes empty are deleted outright
        — otherwise LRU churn would leak empty sets indefinitely.
        """
        # Key-ID indexes
        if len(fp) >= 16:
            self._discard_index_entry(self._by_long_id, fp[-16:], fp)
        if len(fp) >= 8:
            self._discard_index_entry(self._by_short_id, fp[-8:], fp)

        # Email index
        email = key.metadata.get("email", "").strip().lower()
        if email:
            self._discard_index_entry(self._by_email, email, fp)

        # Generic metadata field index
        for field_name, value in key.metadata.items():
            if field_name == "email":
                continue
            if value and field_name in self._by_field:
                field_index = self._by_field[field_name]
                self._discard_index_entry(field_index, value.lower(), fp)
                if not field_index:
                    del self._by_field[field_name]

    @staticmethod
    def _discard_index_entry(index: dict[str, set[str]], key: str, fp: str) -> None:
        """Discard *fp* from ``index[key]``, deleting the set if it empties."""
        fps = index.get(key)
        if fps is None:
            return
        fps.discard(fp)
        if not fps:
            del index[key]

    def _evict_lru(self) -> None:
        """Evict the least-recently-used entry if the cache is over capacity."""
        if self._max_size is None:
            return
        while len(self._store) > self._max_size:
            evicted_fp, evicted_entry = self._store.popitem(last=False)
            self._deindex_key(evicted_fp, evicted_entry.source_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(self, key: SourceKey, ttl: float) -> None:
        """Store *key* in the cache with the given *ttl* (seconds).

        If a key with the same fingerprint is already cached from a
        *more authoritative* source (lower priority number), this call
        is a no-op.  Otherwise the existing entry is replaced.

        When *max_size* is set, the least-recently-used entry is evicted
        after insertion if the cache exceeds capacity.

        Args:
            key: The key to cache.
            ttl: Time-to-live in seconds before the entry should be revalidated.
        """
        fp = self._normalize_fp(key.fingerprint)

        existing = self._store.get(fp)
        if existing is not None:
            if existing.source_key.source_priority < key.source_priority:
                # Existing entry is from a more authoritative source — keep it.
                return
            # Remove old index entries before overwriting.
            self._deindex_key(fp, existing.source_key)

        cached = CachedKey(source_key=key, cached_at=time.time(), ttl=ttl)
        self._store[fp] = cached
        self._store.move_to_end(fp)  # mark as most-recently used
        self._index_key(fp, key)
        self._evict_lru()

    def get_by_fingerprint(self, fp: str) -> CachedKey | None:
        """Return the cached entry for *fp*, or ``None`` if not present.

        Marks the entry as recently used (refreshes LRU order).

        Args:
            fp: Fingerprint in any case, with or without ``0x`` prefix.
        """
        fp = self._normalize_fp(fp)
        entry = self._store.get(fp)
        if entry is not None:
            self._hits += 1
            self._store.move_to_end(fp)
        else:
            self._misses += 1
        return entry

    def get_by_key_id(self, key_id: str) -> list[CachedKey]:
        """Return cached entries matching a long (16-char) or short (8-char) key ID.

        Marks matched entries as recently used (refreshes LRU order).

        Args:
            key_id: Hex key ID (8 or 16 chars), with or without ``0x`` prefix.
        """
        kid = self._normalize_fp(key_id)  # strips 0x, uppercases
        if len(kid) == 16:
            fps = self._by_long_id.get(kid, set())
        elif len(kid) == 8:
            fps = self._by_short_id.get(kid, set())
        else:
            return []
        results = []
        for fp in fps:
            if fp in self._store:
                self._store.move_to_end(fp)
                results.append(self._store[fp])
        self._count_lookup(bool(results))
        return results

    def search(self, query: str, field: str) -> list[CachedKey]:
        """Return cached entries where *field* matches *query*.

        The match is case-insensitive.  For the special field ``"email"``,
        the dedicated email index is used.  For all other fields, the generic
        metadata index is used.

        Marks matched entries as recently used (refreshes LRU order).

        Args:
            query: The search value.
            field: The logical field name (e.g. ``"email"``, ``"github_username"``).
        """
        q = query.strip().lower()
        if field == "email":
            fps = self._by_email.get(q, set())
        else:
            fps = self._by_field.get(field, {}).get(q, set())
        results = []
        for fp in fps:
            if fp in self._store:
                self._store.move_to_end(fp)
                results.append(self._store[fp])
        self._count_lookup(bool(results))
        return results

    def _count_lookup(self, hit: bool) -> None:
        """Record a lookup outcome in the hit/miss counters."""
        if hit:
            self._hits += 1
        else:
            self._misses += 1

    def stats(self) -> dict[str, int | None]:
        """Return approximate cache statistics.

        Hit/miss counts include the orchestrator's internal read-backs after a
        fan-out, so treat them as indicative rather than exact.
        """
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
        }

    def is_fresh(self, fp: str) -> bool:
        """Return ``True`` if the entry for *fp* exists and is within its TTL.

        Args:
            fp: Fingerprint in any case, with or without ``0x`` prefix.
        """
        entry = self._store.get(self._normalize_fp(fp))
        if entry is None:
            return False
        return entry.is_fresh

    def remove(self, fp: str) -> None:
        """Remove the entry for *fp* from the cache and all indexes.

        Args:
            fp: Fingerprint in any case, with or without ``0x`` prefix.
        """
        fp = self._normalize_fp(fp)
        entry = self._store.pop(fp, None)
        if entry is not None:
            self._deindex_key(fp, entry.source_key)

    def remove_by_source(self, source_name: str) -> None:
        """Remove all cached keys that originated from *source_name*.

        Args:
            source_name: The :attr:`~hokeypokey.models.SourceKey.source_name`
                         value to match.
        """
        to_remove = [
            fp for fp, entry in self._store.items() if entry.source_key.source_name == source_name
        ]
        for fp in to_remove:
            self.remove(fp)

    def __len__(self) -> int:
        return len(self._store)
