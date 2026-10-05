import pytest
import httpx
from unittest.mock import MagicMock
from src.core.resilience import with_retry_on_transient_http_errors, with_fallback

@pytest.mark.anyio
async def test_resilience_timeout():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    async def fail_timeout():
        nonlocal attempts
        attempts += 1
        raise httpx.TimeoutException("Timeout")

    with pytest.raises(httpx.TimeoutException):
        await fail_timeout()
        
    assert attempts == 2

@pytest.mark.anyio
async def test_resilience_connection_error():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    async def fail_conn():
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("ConnError")

    with pytest.raises(httpx.ConnectError):
        await fail_conn()
        
    assert attempts == 2

@pytest.mark.anyio
async def test_resilience_http_error_retry():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    async def fail_http_500():
        nonlocal attempts
        attempts += 1
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_request = MagicMock()
        raise httpx.HTTPStatusError("500 Server Error", response=mock_response, request=mock_request)

    with pytest.raises(httpx.HTTPStatusError):
        await fail_http_500()
        
    assert attempts == 2

@pytest.mark.anyio
async def test_resilience_http_error_no_retry():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    async def fail_http_404():
        nonlocal attempts
        attempts += 1
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request = MagicMock()
        raise httpx.HTTPStatusError("404 Not Found", response=mock_response, request=mock_request)

    with pytest.raises(httpx.HTTPStatusError):
        await fail_http_404()
        
    assert attempts == 1  # No retry for 404

@pytest.mark.anyio
async def test_with_fallback():
    @with_retry_on_transient_http_errors(max_attempts=1, min_wait=0, max_wait=0)
    @with_fallback({"status": "fallback"})
    async def fail_with_fallback():
        raise httpx.ConnectError("Fail")

    result = await fail_with_fallback()
    assert result == {"status": "fallback"}
