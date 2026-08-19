from __future__ import annotations

"""Lightweight in-process TTL cache manager with namespacing.

Goals
- Single place to manage small per-process caches (embeddings, configs, prompts, histories).
- Minimal API to facilitate later swap to Redis/Memcached.

Non-goals
- Cross-process consistency, persistence, or LRU eviction. This is a per-worker helper only.
"""

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class _Entry:
    exp: float
    val: Any


class CacheManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ns: Dict[str, Dict[str, _Entry]] = {}

    def get(self, namespace: str, key: str) -> Any | None:
        now = time.perf_counter()
        with self._lock:
            bucket = self._ns.get(namespace)
            if not bucket:
                return None
            ent = bucket.get(key)
            if not ent or ent.exp <= now:
                if ent:
                    # expired
                    bucket.pop(key, None)
                return None
            return ent.val

    def set(self, namespace: str, key: str, value: Any, ttl_s: float) -> None:
        exp = time.perf_counter() + max(float(ttl_s), 0.0)
        with self._lock:
            bucket = self._ns.setdefault(namespace, {})
            bucket[key] = _Entry(exp=exp, val=value)

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            bucket = self._ns.get(namespace)
            if bucket:
                bucket.pop(key, None)

    def clear(self, namespace: str) -> None:
        with self._lock:
            self._ns.pop(namespace, None)

    def clear_all(self) -> None:
        with self._lock:
            self._ns.clear()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {ns: len(bucket) for ns, bucket in self._ns.items()}


# Global singleton used across the app
cache = CacheManager()


# Helpers for TTLs from env with sensible defaults
def ttl_from_env(env_name: str, default_s: float) -> float:
    try:
        return float(os.getenv(env_name, str(default_s)))
    except Exception:
        return default_s
