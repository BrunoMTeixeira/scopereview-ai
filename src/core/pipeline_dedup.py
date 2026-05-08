import time
from threading import Lock
from typing import Dict, Tuple

from .logger import get_logger

log = get_logger("Dedup")


class InMemoryPipelineDedup:
    """Process-local duplicate guard (TTL).

    Not shared across replicas — inject a Redis-backed adapter for scale-out.
    Thread-safe via threading.Lock.
    """

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._entries: Dict[Tuple[str, int], float] = {}
        self._lock = Lock()

    def should_skip_duplicate(self, pr_id: int, agent: str = "default") -> bool:
        """Checks if the request is a duplicate. Cleans up expired entries."""
        key = (agent, pr_id)
        now = time.time()
        with self._lock:
            # Cleanup expired entries to prevent memory growth
            expired = [k for k, ts in self._entries.items() if now - ts > self._ttl]
            if expired:
                for k in expired:
                    del self._entries[k]
                log.debug("Cleaned up %d expired deduplication entries", len(expired))

            last_ts = self._entries.get(key, 0)
            if (now - last_ts) < self._ttl:
                return True

            self._entries[key] = now
            return False

    def release(self, pr_id: int, agent: str = "default") -> None:
        """Manually release the lock for a specific PR/Agent combo."""
        key = (agent, pr_id)
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                log.debug("Released deduplication lock for PR #%s (Agent: %s)", pr_id, agent)
