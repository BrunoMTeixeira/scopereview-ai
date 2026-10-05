"""Resilience utilities for handling transient failures in external API calls.

This module provides decorators and retry logic for:
- Azure AI Foundry API (rate limits, timeouts, network errors)
- Azure DevOps REST API (transient 5xx, network errors)

Uses tenacity library for exponential backoff with jitter.
"""

from functools import wraps
from typing import Callable, Any

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    retry_if_exception_type,
    before_sleep_log,
)
import httpx
import asyncio

from .logger import get_logger

log = get_logger("Resilience")


def with_retry_on_transient_http_errors(
    max_attempts: int = 3,
    multiplier: int = 1,
    min_wait: int = 1,
    max_wait: int = 10,
    retry_status_codes: tuple = (429, 500, 502, 503, 504),
) -> Callable:
    """Decorator to retry HTTP calls on transient errors (5xx, 429, network failures).

    Args:
        max_attempts: Maximum number of retry attempts (default: 3).
        multiplier: Exponential backoff multiplier in seconds (default: 1).
        min_wait: Minimum wait time between retries in seconds (default: 1).
        max_wait: Maximum wait time between retries in seconds (default: 10).

    Returns:
        Decorated function with retry logic.

    Example:
        ```python
        @with_retry_on_transient_http_errors(max_attempts=3)
        async def fetch_data(url: str) -> dict:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        ```
    """

    def should_retry_http_error(exception: BaseException) -> bool:
        """Determine if an HTTP error should trigger a retry."""
        if isinstance(exception, httpx.TimeoutException):
            log.warning("⏱️  Timeout detected, will retry...")
            return True

        if isinstance(exception, (httpx.ConnectError, httpx.NetworkError)):
            log.warning("🔌 Connection error detected, will retry...")
            return True

        if isinstance(exception, httpx.HTTPStatusError):
            status_code = getattr(exception.response, "status_code", None) if exception.response else None
            # Retry on 429 (Rate Limit), 500, 502, 503, 504 (Server Errors)
            if status_code in retry_status_codes:
                log.warning("⚠️  HTTP %s detected, will retry...", status_code)
                return True

        return False

    from tenacity import retry_if_exception
    return retry(
        retry=retry_if_exception(should_retry_http_error),
        stop=stop_after_attempt(max_attempts),
        # Exponential backoff (2, 4, 8...) + Random Jitter (0-2s) to prevent thundering herd
        wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait) + wait_random(0, 2),
        before_sleep=before_sleep_log(log, log_level=30),  # logging.WARNING = 30
        reraise=True,
    )


def with_fallback(fallback_value: Any) -> Callable:
    """Decorator to return a fallback value if all retries are exhausted.

    Args:
        fallback_value: Value to return when function fails after all retries.

    Returns:
        Decorated function with fallback handling.

    Example:
        ```python
        @with_retry_on_transient_http_errors(max_attempts=3)
        @with_fallback(fallback_value={})
        def fetch_optional_data(url: str) -> dict:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        ```
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                log.error(
                    "❌ All retry attempts exhausted for %s. Returning fallback value. Error: %s",
                    func.__name__, e
                )
                return fallback_value

        return wrapper

    return decorator
