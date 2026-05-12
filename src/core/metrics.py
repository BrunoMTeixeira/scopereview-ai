import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class SystemMetrics:
    """Consolidates system telemetry data."""

    total_requests: int = 0
    total_success: int = 0
    total_failures: int = 0
    total_tokens_consumed: int = 0
    total_input_tokens_consumed: int = 0
    total_output_tokens_consumed: int = 0
    total_latency_ms: float = 0.0
    start_time: float = field(default_factory=time.time)

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_analysis(
        self,
        success: bool,
        total_tokens: int,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float
    ):
        """Thread-safe recording of an analysis event."""
        with self._lock:
            self.total_requests += 1
            if success:
                self.total_success += 1
            else:
                self.total_failures += 1
            self.total_tokens_consumed += total_tokens
            self.total_input_tokens_consumed += input_tokens
            self.total_output_tokens_consumed += output_tokens
            self.total_latency_ms += latency_ms

    def get_summary(self) -> Dict[str, Any]:
        """Returns a snapshot of current metrics."""
        with self._lock:
            uptime = time.time() - self.start_time
            avg_latency = (self.total_latency_ms / self.total_requests) if self.total_requests > 0 else 0

            return {
                "uptime_seconds": round(uptime, 2),
                "total_requests": self.total_requests,
                "success_rate": (
                    f"{(self.total_success / self.total_requests * 100):.1f}%" if self.total_requests > 0 else "0%"
                ),
                "total_failures": self.total_failures,
                "total_tokens": self.total_tokens_consumed,
                "input_tokens": self.total_input_tokens_consumed,
                "output_tokens": self.total_output_tokens_consumed,
                "average_latency_ms": round(avg_latency, 2),
            }


# Global singleton for metrics
metrics = SystemMetrics()
