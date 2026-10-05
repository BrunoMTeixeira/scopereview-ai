import time
import pytest
from src.core.rate_limit import RateLimiter

def test_rate_limiter_allowed_within_limit():
    limiter = RateLimiter(requests_limit=2, window_seconds=1)
    assert limiter.is_allowed() is True
    assert limiter.is_allowed() is True

def test_rate_limiter_blocks_when_limit_exceeded():
    limiter = RateLimiter(requests_limit=2, window_seconds=1)
    assert limiter.is_allowed() is True
    assert limiter.is_allowed() is True
    # Third request should be blocked
    assert limiter.is_allowed() is False

def test_rate_limiter_allows_after_window_expires(monkeypatch):
    limiter = RateLimiter(requests_limit=1, window_seconds=1)
    
    # Mock time to a specific start time
    start_time = 1000.0
    monkeypatch.setattr(time, "time", lambda: start_time)
    
    assert limiter.is_allowed() is True
    assert limiter.is_allowed() is False
    
    # Advance time beyond the window
    monkeypatch.setattr(time, "time", lambda: start_time + 1.1)
    
    # Should be allowed again
    assert limiter.is_allowed() is True

def test_rate_limiter_reset():
    limiter = RateLimiter(requests_limit=1, window_seconds=10)
    assert limiter.is_allowed() is True
    assert limiter.is_allowed() is False
    
    limiter.reset()
    assert limiter.is_allowed() is True

def test_rate_limiter_concurrent_access():
    import threading
    limiter = RateLimiter(requests_limit=10, window_seconds=1)
    
    results = []
    def worker():
        results.append(limiter.is_allowed())
        
    threads = [threading.Thread(target=worker) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    allowed_count = sum(results)
    assert allowed_count == 10
