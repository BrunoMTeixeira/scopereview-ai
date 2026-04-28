"""Redis-backed pipeline deduplication (distributed TTL lock)."""

from __future__ import annotations

from typing import Optional

from ..core.logger import get_logger
from ..ports.dedup import PipelineDedupPort

log = get_logger("RedisDedup")

try:
    import redis  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[misc, assignment]


class RedisPipelineDedup:
    """
    SET key NX EX ttl — first process acquires the slot; others skip until TTL.
    `release` deletes the key (e.g. after pipeline failure) so ADO can retry immediately.
    """

    def __init__(self, url: str, ttl_seconds: int, key_prefix: str = "scopereview:dedup"):
        if redis is None:
            raise ImportError("The 'redis' package is required for RedisPipelineDedup. pip install redis")
        self._ttl = max(1, int(ttl_seconds))
        self._prefix = key_prefix.rstrip(":")
        self._client = redis.from_url(url, decode_responses=True)

    def _key(self, pr_id: int, agent: str) -> str:
        return f"{self._prefix}:{agent}:{pr_id}"

    def should_skip_duplicate(self, pr_id: int, agent: str = "default") -> bool:
        try:
            acquired: Optional[bool] = self._client.set(self._key(pr_id, agent), "1", nx=True, ex=self._ttl)
            if acquired:
                return False
            return True
        except redis.RedisError as exc:
            log.warning("Redis dedup error (%s); allowing pipeline run (no distributed dedupe).", exc)
            return False

    def release(self, pr_id: int, agent: str = "default") -> None:
        try:
            self._client.delete(self._key(pr_id, agent))
        except redis.RedisError as exc:
            log.warning("Redis dedup release failed: %s", exc)
