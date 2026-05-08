import pytest
import requests
from unittest.mock import MagicMock
from src.core.resilience import with_retry_on_transient_http_errors, with_fallback

def test_resilience_timeout():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    def fail_timeout():
        nonlocal attempts
        attempts += 1
        raise requests.exceptions.Timeout("Timeout")

    with pytest.raises(requests.exceptions.Timeout):
        fail_timeout()
        
    assert attempts == 2

def test_resilience_connection_error():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    def fail_conn():
        nonlocal attempts
        attempts += 1
        raise requests.exceptions.ConnectionError("ConnError")

    with pytest.raises(requests.exceptions.ConnectionError):
        fail_conn()
        
    assert attempts == 2

def test_resilience_http_error_retry():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    def fail_http_500():
        nonlocal attempts
        attempts += 1
        mock_response = MagicMock()
        mock_response.status_code = 500
        raise requests.exceptions.HTTPError("500 Server Error", response=mock_response)

    with pytest.raises(requests.exceptions.HTTPError):
        fail_http_500()
        
    assert attempts == 2

def test_resilience_http_error_no_retry():
    attempts = 0
    
    @with_retry_on_transient_http_errors(max_attempts=2, min_wait=0, max_wait=0)
    def fail_http_404():
        nonlocal attempts
        attempts += 1
        mock_response = MagicMock()
        mock_response.status_code = 404
        raise requests.exceptions.HTTPError("404 Not Found", response=mock_response)

    with pytest.raises(requests.exceptions.HTTPError):
        fail_http_404()
        
    assert attempts == 1  # No retry for 404

def test_with_fallback():
    @with_retry_on_transient_http_errors(max_attempts=1, min_wait=0, max_wait=0)
    @with_fallback({"status": "fallback"})
    def fail_with_fallback():
        raise requests.exceptions.ConnectionError("Fail")

    result = fail_with_fallback()
    assert result == {"status": "fallback"}
