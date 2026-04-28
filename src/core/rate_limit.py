import time
import threading
from collections import deque

class RateLimiter:
    """Simple in-memory rate limiter using a sliding window approach."""
    
    def __init__(self, requests_limit: int, window_seconds: int):
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self.requests = deque()
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        """Checks if a new request is allowed within the time window."""
        now = time.time()
        with self._lock:
            # Remove expired timestamps
            while self.requests and self.requests[0] < now - self.window_seconds:
                self.requests.popleft()
            
            if len(self.requests) < self.requests_limit:
                self.requests.append(now)
                return True
            return False

    def reset(self):
        """Clears the request history."""
        with self._lock:
            self.requests.clear()
