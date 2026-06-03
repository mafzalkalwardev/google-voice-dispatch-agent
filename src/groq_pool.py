"""Multi-key Groq API pool — failover across accounts on limits and auth errors."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Callable, List, Optional, TypeVar

from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

T = TypeVar("T")

_PLACEHOLDER_PREFIX = "your_"
_MAX_KEYS = 10


def _is_placeholder(key: str) -> bool:
    k = key.strip()
    return not k or k.startswith(_PLACEHOLDER_PREFIX)


def load_groq_api_keys() -> List[str]:
    """
    Load Groq API keys from environment (deduped, order preserved).

    Supported:
      - GROQ_API_KEY
      - GROQ_API_KEY_2 … GROQ_API_KEY_10
      - GROQ_API_KEYS=key1,key2,key3
    """
    seen: set[str] = set()
    keys: List[str] = []

    def add(raw: str) -> None:
        for part in re.split(r"[\s,;]+", raw):
            k = part.strip()
            if _is_placeholder(k) or k in seen:
                continue
            seen.add(k)
            keys.append(k)

    for i in range(1, _MAX_KEYS + 1):
        name = "GROQ_API_KEY" if i == 1 else f"GROQ_API_KEY_{i}"
        val = os.getenv(name, "").strip()
        if val:
            add(val)

    bulk = os.getenv("GROQ_API_KEYS", "").strip()
    if bulk:
        add(bulk)

    return keys


def groq_should_failover(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "rate-limited" in msg
        or "rate_limit" in msg
        or "too many requests" in msg
        or "401" in msg
        or "403" in msg
        or "invalid_api_key" in msg
        or "invalid api key" in msg
        or "authentication" in msg
    )


def _retry_after_seconds(exc: BaseException, default: float = 45.0) -> float:
    msg = str(exc)
    m = re.search(r"retry[- ]?after[:\s]+(\d+(?:\.\d+)?)", msg, re.I)
    if m:
        return max(5.0, float(m.group(1)))
    m = re.search(r"retry after (\d+)", msg, re.I)
    if m:
        return max(5.0, float(m.group(1)))
    headers = getattr(exc, "headers", None)
    if headers:
        try:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra is not None:
                return max(5.0, float(ra))
        except (TypeError, ValueError):
            pass
    return default


class GroqAllKeysFailed(Exception):
    """Raised when every configured Groq key failed for one operation."""

    def __init__(self, errors: List[tuple[int, BaseException]]):
        self.errors = errors
        parts = [f"key#{i + 1}: {e}" for i, e in errors]
        super().__init__("; ".join(parts[:3]) + ("..." if len(parts) > 3 else ""))


class GroqKeyPool:
    """Round-robin Groq clients with failover on rate limits and bad keys."""

    def __init__(self, keys: List[str]):
        clean = [k.strip() for k in keys if k.strip() and not _is_placeholder(k.strip())]
        if not clean:
            raise ValueError("At least one valid Groq API key is required")
        self._keys = clean
        self._lock = threading.Lock()
        self._index = 0
        self._clients: dict[int, object] = {}
        self._cooldown_until: dict[int, float] = {}
        self._disabled: set[int] = set()

    @classmethod
    def from_env(cls, *, extra: Optional[str] = None) -> "GroqKeyPool":
        keys = load_groq_api_keys()
        if extra and not _is_placeholder(extra) and extra.strip() not in keys:
            keys = [extra.strip(), *keys]
        return cls(keys)

    @property
    def primary_key(self) -> str:
        return self._keys[0]

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def mask_key(self, key: str) -> str:
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def _available_indices(self) -> List[int]:
        now = time.monotonic()
        out: List[int] = []
        for i in range(len(self._keys)):
            if i in self._disabled:
                continue
            until = self._cooldown_until.get(i, 0.0)
            if until > now:
                continue
            out.append(i)
        return out

    def _client_for(self, idx: int):
        with self._lock:
            if idx not in self._clients:
                self._clients[idx] = Groq(api_key=self._keys[idx])
            return self._clients[idx]

    def _disable_key(self, idx: int, exc: BaseException) -> None:
        msg = str(exc).lower()
        if "401" in msg or "invalid_api_key" in msg or "invalid api key" in msg:
            with self._lock:
                self._disabled.add(idx)
            logger.error(
                "Groq key #%d (%s) rejected (invalid/revoked) — disabled for this run",
                idx + 1,
                self.mask_key(self._keys[idx]),
            )
            return
        wait = _retry_after_seconds(exc)
        with self._lock:
            self._cooldown_until[idx] = time.monotonic() + wait
        logger.warning(
            "Groq key #%d (%s) rate-limited or busy — cooldown %.0fs",
            idx + 1,
            self.mask_key(self._keys[idx]),
            wait,
        )

    def execute(self, fn: Callable[[object], T]) -> T:
        """Run fn(client) using the next available key; failover on limit/auth errors."""
        errors: List[tuple[int, BaseException]] = []
        with self._lock:
            start = self._index

        indices = self._available_indices()
        if not indices:
            # All on cooldown — try least-recently blocked anyway
            indices = [i for i in range(len(self._keys)) if i not in self._disabled]
        if not indices:
            raise GroqAllKeysFailed([(0, ValueError("all Groq API keys disabled or invalid"))])

        ordered = indices[start % len(indices) :] + indices[: start % len(indices)]
        # dedupe order while preserving rotation
        seen_ord: set[int] = set()
        try_order: List[int] = []
        for i in ordered:
            if i not in seen_ord:
                seen_ord.add(i)
                try_order.append(i)
        for i in indices:
            if i not in seen_ord:
                try_order.append(i)

        for idx in try_order:
            try:
                client = self._client_for(idx)
                result = fn(client)
                with self._lock:
                    self._index = idx
                return result
            except Exception as exc:
                errors.append((idx, exc))
                if groq_should_failover(exc) and len(errors) < len(self._keys):
                    self._disable_key(idx, exc)
                    logger.info(
                        "Groq failover: key #%d failed, trying next (%d key(s) left)",
                        idx + 1,
                        len(self._keys) - len({e[0] for e in errors}),
                    )
                    continue
                if len(errors) >= len(self._keys) or not groq_should_failover(exc):
                    break

        raise GroqAllKeysFailed(errors)

    def test_all_keys(self) -> tuple[int, int, List[str]]:
        """Return (ok_count, total, detail_messages)."""
        ok = 0
        lines: List[str] = []
        for i, key in enumerate(self._keys):
            try:
                client = Groq(api_key=key)
                models = client.models.list()
                count = len(list(models.data))
                ok += 1
                lines.append(f"key#{i + 1} {self.mask_key(key)}: OK ({count} models)")
            except Exception as exc:
                short = str(exc)[:80]
                lines.append(f"key#{i + 1} {self.mask_key(key)}: FAIL ({short})")
        return ok, len(self._keys), lines


_pool_singleton: Optional[GroqKeyPool] = None
_pool_lock = threading.Lock()


def pool_for_request(explicit_key: str = "") -> GroqKeyPool:
    """Pool for post-call/CRM helpers: env keys, plus optional explicit key."""
    keys = list(load_groq_api_keys())
    k = explicit_key.strip()
    if k and not _is_placeholder(k) and k not in keys:
        keys.insert(0, k)
    if not keys and k:
        keys = [k]
    return GroqKeyPool(keys)


def get_groq_pool(*, refresh: bool = False) -> GroqKeyPool:
    """Process-wide pool from environment (reloaded when refresh=True)."""
    global _pool_singleton
    with _pool_lock:
        if refresh or _pool_singleton is None:
            keys = load_groq_api_keys()
            if not keys:
                raise ValueError(
                    "No Groq API keys configured. Set GROQ_API_KEY and optionally "
                    "GROQ_API_KEY_2, GROQ_API_KEY_3, or GROQ_API_KEYS in .env"
                )
            _pool_singleton = GroqKeyPool(keys)
        return _pool_singleton
