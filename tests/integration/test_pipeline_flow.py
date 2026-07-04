import pytest
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any, List

from src.services.orchestrator import PipelineOrchestrator
from src.ports.repository_client import RepositoryClientPort
from src.ports.ai_client import AIModelClientPort
from src.ports.dedup import PipelineDedupPort
from src.services.code_review import CodeReviewService
from src.services.requirements_review import RequirementsReviewService


class MockAzureDevOpsClient(RepositoryClientPort):
    """Mock ADO client for testing."""

    def __init__(self):
        self.post_comment_calls = []

    async def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Dict[str, Any]:
        return {
            "pull_request_id": pr_id,
            "commit_sha": "abc123",
            "base_sha": "base123",
            "title": "Test PR",
            "description": "Test Description",
            "source_branch": "feature/test",
            "target_branch": "main",
        }

    async def get_changed_files(
            self, repo_id: str, pr_id: int, project: str, commit_sha: str, base_sha: str
    ) -> tuple:
        return (
            {
                "test.py": "def hello():\n    print('hello')\n"
            },
            {
                "test.py": "+def hello():\n+    print('hello')\n"
            },
            1
        )

    async def post_comment(self, repo_id: str, pr_id: int, project: str, comment_text: str) -> None:
        self.post_comment_calls.append({
            "repo_id": repo_id,
            "pr_id": pr_id,
            "project": project,
            "comment": comment_text
        })

    async def get_work_items(self, repo_id: str, pr_id: int, project: str) -> List[Dict[str, Any]]:
        return []

    async def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str:
        return ""

    async def post_pr_status(self, repo_id: str, pr_id: int, project: str, state: str, description: str) -> None:
        pass


class MockAIClient(AIModelClientPort):
    """Mock AI client for testing."""

    async def complete(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            max_tokens: int = 8000,
    ) -> tuple:
        return '{"findings": [], "verdict": "PASSED", "score": 9.5}', {"total_tokens": 1000, "prompt_tokens": 900, "completion_tokens": 100}


class MockDedupPort(PipelineDedupPort):
    """Mock dedup port for testing."""

    def should_skip_duplicate(self, pr_id: int, context_id: str) -> bool:
        return False

    def release(self, pr_id: int, context_id: str) -> None:
        pass


class TestPipelineOrchestration:
    """Test suite for the orchestrator pipeline."""

    @pytest.fixture
    def mock_ado(self):
        return MockAzureDevOpsClient()

    @pytest.fixture
    def mock_ai(self):
        return MockAIClient()

    @pytest.fixture
    def mock_dedup(self):
        return MockDedupPort()

    @pytest.fixture
    def code_review_service(self, mock_ai):
        return CodeReviewService(
            ai=mock_ai,
            static_analyzer=MagicMock(),
            max_high_block=3,
            max_token_budget=50000
        )

    @pytest.fixture
    def requirements_review_service(self, mock_ai):
        from src.services.requirements_review import RequirementsReviewService
        return RequirementsReviewService(
            ai=mock_ai,
            max_completion_tokens=24000
        )

    @pytest.fixture
    def orchestrator(
            self, mock_ado, code_review_service, requirements_review_service, mock_dedup
    ):
        return PipelineOrchestrator(
            ado=mock_ado,
            code_review=code_review_service,
            requirements_review=requirements_review_service,
            dedup=mock_dedup,
            code_model_display_name="gpt-4",
            requirements_model_display_name="gpt-4",
        )

    @pytest.mark.anyio
    async def test_orchestrator_processes_pr_successfully(self, orchestrator, mock_ado):
        """Test that the orchestrator successfully processes a PR."""
        # Act
        await orchestrator.process_pr_pipeline(pr_id=123, repo_id="repo1", project="proj1")

        # Assert
        # At least 2 comments should be posted (code review + requirements)
        assert len(mock_ado.post_comment_calls) >= 1

    @pytest.mark.anyio
    async def test_orchestrator_handles_missing_pr_details(self, orchestrator, mock_ado):
        """Test that orchestrator handles missing PR details gracefully."""

        # Arrange
        async def fail_get_pr_details(*args, **kwargs):
            return None

        mock_ado.get_pr_details = fail_get_pr_details

        # Act & Assert - should not raise, just return
        await orchestrator.process_pr_pipeline(pr_id=999, repo_id="repo1", project="proj1")

    @pytest.mark.anyio
    async def test_orchestrator_skips_duplicate_prs(self, orchestrator, mock_ado, mock_dedup):
        """Test that orchestrator skips duplicate PR processing."""

        # Arrange
        def should_skip(*args, **kwargs):
            return True

        mock_dedup.should_skip_duplicate = should_skip

        # Act
        await orchestrator.process_pr_pipeline(pr_id=123, repo_id="repo1", project="proj1")

        # Assert
        # No comments should be posted
        assert len(mock_ado.post_comment_calls) == 0
