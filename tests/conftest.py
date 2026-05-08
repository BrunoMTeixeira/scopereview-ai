"""
Shared test fixtures for the ScopeReview AI test suite.

Provides:
- Environment setup (dev mode, dummy credentials)
- DI container cleanup between tests
- Reusable mock adapters for ADO, AI and Dedup ports
- FastAPI app dependency override helpers
"""

import pytest
import os
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

# ──────────────────────────────────────────────────────────────────────────────
# CRITICAL: Set ENVIRONMENT=dev BEFORE any imports from src
# This prevents Pydantic Settings validation errors when loading config
# ──────────────────────────────────────────────────────────────────────────────
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("WEBHOOK_SECRET", "")
os.environ.setdefault("AZURE_ENDPOINT_CR", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_API_KEY_CR", "test-key")
os.environ.setdefault("AZURE_ENDPOINT_REQ", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_API_KEY_REQ", "test-key")
os.environ.setdefault("ADO_ORGANIZATION", "test-org")
os.environ.setdefault("ADO_PAT", "test-pat")

from src.core.di import injector  # noqa: E402
from src.core import config  # noqa: E402
from src.ports.ado_client import AzureDevOpsClientPort  # noqa: E402
from src.ports.ai_client import AIModelClientPort  # noqa: E402
from src.ports.dedup import PipelineDedupPort  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# MOCK ADAPTERS (Port implementations for testing)
# ──────────────────────────────────────────────────────────────────────────────

class MockAzureDevOpsClient(AzureDevOpsClientPort):
    """In-memory ADO client stub. Records all post_comment calls for assertions."""

    def __init__(self):
        self.post_comment_calls: List[dict] = []

    def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Optional[dict]:
        return {
            "title": "Test PR",
            "description": "Test Description",
            "author": "Test Author",
            "commit_sha": "abc123",
            "base_sha": "base123",
        }

    def get_changed_files(
        self, repo_id: str, pr_id: int, project: str, commit_sha: str, base_sha: str = ""
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        return (
            {"test.py": "   1 | def hello():\n   2 |     print('hello')\n"},
            {"test.py": "+def hello():\n+    print('hello')\n"},
        )

    def get_work_items(self, repo_id: str, pr_id: int, project: str) -> List[dict]:
        return []

    def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str:
        return ""

    def post_comment(self, repo_id: str, pr_id: int, project: str, comment: str) -> None:
        self.post_comment_calls.append({
            "repo_id": repo_id, "pr_id": pr_id,
            "project": project, "comment": comment,
        })


class MockAIClient(AIModelClientPort):
    """Deterministic AI client that returns a fixed JSON response."""

    def __init__(self, response: str = '{"findings": [], "positive_aspects": []}', tokens: int = 100):
        self._response = response
        self._tokens = tokens

    def complete(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 8000
    ) -> Tuple[Optional[str], int]:
        return self._response, self._tokens


class MockDedupPort(PipelineDedupPort):
    """Dedup stub that never blocks (always allows pipeline execution)."""

    def should_skip_duplicate(self, pr_id: int, agent: str = "default") -> bool:
        return False

    def release(self, pr_id: int, agent: str = "default") -> None:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# GLOBAL FIXTURES
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_di():
    """Clean DI container before and after each test for isolation."""
    injector.clear_all()
    yield
    injector.clear_all()


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    """Setup environment variables for all tests (dev mode, dummy credentials)."""
    env_vars = {
        "ENVIRONMENT": "dev",
        "AZURE_ENDPOINT_CR": "https://test.openai.azure.com/",
        "AZURE_API_KEY_CR": "test-key-cr",
        "AZURE_MODEL_CR": "gpt-4",
        "AZURE_ENDPOINT_REQ": "https://test.openai.azure.com/",
        "AZURE_API_KEY_REQ": "test-key-req",
        "AZURE_MODEL_REQ": "gpt-4",
        "ADO_ORGANIZATION": "test-org",
        "ADO_PAT": "test-pat",
        "WEBHOOK_SECRET": "",
        "SCOPE_REVIEW_ALLOW_HTTP_AI": "1",
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    import importlib
    importlib.reload(config)

    yield env_vars


@pytest.fixture
def cleanup_app_overrides():
    """Cleans FastAPI dependency overrides after test completion."""
    from src.main import app
    yield app
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────────────
# REUSABLE PORT FIXTURES
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ado() -> MockAzureDevOpsClient:
    """Provides a fresh MockAzureDevOpsClient instance."""
    return MockAzureDevOpsClient()


@pytest.fixture
def mock_ai() -> MockAIClient:
    """Provides a fresh MockAIClient instance with default empty response."""
    return MockAIClient()


@pytest.fixture
def mock_dedup() -> MockDedupPort:
    """Provides a fresh MockDedupPort instance (never blocks)."""
    return MockDedupPort()


@pytest.fixture
def disable_webhook_auth(monkeypatch):
    """Disables webhook auth for integration/e2e tests hitting the real app."""
    from src.api import webhooks
    mock_settings = MagicMock()
    mock_settings.WEBHOOK_SECRET = ""
    mock_settings.is_production = False
    monkeypatch.setattr(webhooks, "settings", mock_settings)
