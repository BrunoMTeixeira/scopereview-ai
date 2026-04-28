import time
from threading import Lock


class InMemoryPipelineDedup:
    """Process-local duplicate guard (TTL). Not shared across replicas — inject a Redis-backed adapter for scale-out."""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, int], float] = {}
        self._lock = Lock()

    def should_skip_duplicate(self, pr_id: int, agent: str = "default") -> bool:
        key = (agent, pr_id)
        now = time.time()
        with self._lock:
            expired = [k for k, ts in self._entries.items() if now - ts > self._ttl]
            for k in expired:
                del self._entries[k]

            if (now - self._entries.get(key, 0)) < self._ttl:
                return True

            self._entries[key] = now
            return False

    def release(self, pr_id: int, agent: str = "default") -> None:
        key = (agent, pr_id)
        with self._lock:
            if key in self._entries:
                del self._entries[key]
