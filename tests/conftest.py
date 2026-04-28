"""
Fixtures compartilhadas para todos os testes.
"""

import pytest
import os

# CRITICAL: Set ENVIRONMENT=dev BEFORE any imports from src
# This prevents Pydantic Settings validation errors when loading config
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("WEBHOOK_SECRET", "")  # Disable webhook auth in tests
os.environ.setdefault("AZURE_ENDPOINT_CR", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_API_KEY_CR", "test-key")
os.environ.setdefault("AZURE_ENDPOINT_REQ", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_API_KEY_REQ", "test-key")
os.environ.setdefault("ADO_ORGANIZATION", "test-org")
os.environ.setdefault("ADO_PAT", "test-pat")

from src.core.di import injector  # noqa: E402
from src.core import config  # noqa: E402


@pytest.fixture(autouse=True)
def cleanup_di():
    """Limpar DI antes e depois de cada teste."""
    injector.clear_all()
    yield
    injector.clear_all()


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    """Setup de variáveis de ambiente para testes (auto-applied to all tests)."""
    env_vars = {
        "ENVIRONMENT": "dev",  # Allow tests to run without strict validation
        "AZURE_ENDPOINT_CR": "https://test.openai.azure.com/",
        "AZURE_API_KEY_CR": "test-key-cr",
        "AZURE_MODEL_CR": "gpt-4",
        "AZURE_ENDPOINT_REQ": "https://test.openai.azure.com/",
        "AZURE_API_KEY_REQ": "test-key-req",
        "AZURE_MODEL_REQ": "gpt-4",
        "ADO_ORGANIZATION": "test-org",
        "ADO_PAT": "test-pat",
        "WEBHOOK_SECRET": "",  # No webhook auth in tests
        "SCOPE_REVIEW_ALLOW_HTTP_AI": "1",
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    # CRITICAL: Reload settings after monkeypatching environment variables
    # This ensures tests see the updated configuration
    import importlib
    importlib.reload(config)

    yield env_vars
