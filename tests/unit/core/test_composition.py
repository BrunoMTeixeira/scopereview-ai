import pytest
from unittest.mock import Mock, MagicMock
from typing import Any

from src.core.di import SimpleDependencyInjector
from src.composition import (
    get_pipeline_orchestrator,
    reset_pipeline_for_tests,
    register_service,
)
from src.services.orchestrator import PipelineOrchestrator
from src.ports.ai_client import AIModelClientPort
from src.ports.repository_client import RepositoryClientPort


class TestSimpleDependencyInjector:
    """Test suite for the SimpleDependencyInjector."""

    @pytest.fixture
    def injector(self) -> SimpleDependencyInjector:
        """Provide a fresh injector instance for each test."""
        inj = SimpleDependencyInjector()
        yield inj
        inj.clear_all()

    def test_register_and_retrieve_service(self, injector):
        """Test basic service registration and retrieval."""

        # Arrange
        class TestService:
            pass

        instance = TestService()

        # Act
        injector.register(TestService, instance)
        retrieved = injector.get(TestService)

        # Assert
        assert retrieved is instance

    def test_register_raises_type_error_on_mismatch(self, injector):
        """Test that registration fails if instance type doesn't match."""

        # Arrange
        class ServiceA:
            pass

        class ServiceB:
            pass

        instance = ServiceB()

        # Act & Assert
        with pytest.raises(TypeError):
            injector.register(ServiceA, instance)

    def test_get_raises_key_error_when_not_registered(self, injector):
        """Test that retrieval fails when service is not registered."""

        # Arrange
        class UnregisteredService:
            pass

        # Act & Assert
        with pytest.raises(KeyError):
            injector.get(UnregisteredService)

    def test_override_service_for_testing(self, injector):
        """Test that services can be overridden for testing."""

        # Arrange
        class Service:
            pass

        original = Service()
        mock_instance = Service()
        injector.register(Service, original)

        # Act
        injector.override(Service, mock_instance)
        retrieved = injector.get(Service)

        # Assert
        assert retrieved is mock_instance

    def test_clear_overrides(self, injector):
        """Test that overrides can be cleared."""

        # Arrange
        class Service:
            pass

        original = Service()
        mock_instance = Service()
        injector.register(Service, original)
        injector.override(Service, mock_instance)

        # Act
        injector.clear_overrides()
        retrieved = injector.get(Service)

        # Assert
        assert retrieved is original

    def test_clear_all_wipes_registry_and_overrides(self, injector):
        """Test that clear_all removes everything."""

        # Arrange
        class Service:
            pass

        injector.register(Service, Service())

        # Act
        injector.clear_all()

        # Assert
        with pytest.raises(KeyError):
            injector.get(Service)


class TestCompositionBootstrap:
    """Test suite for composition and bootstrap logic."""

    @pytest.fixture(autouse=True)
    def cleanup_di(self):
        """Cleanup DI after each test."""
        yield
        reset_pipeline_for_tests()

    def test_get_pipeline_orchestrator_raises_on_not_registered(self):
        """Test that get_pipeline_orchestrator fails gracefully when not registered."""
        # Arrange
        reset_pipeline_for_tests()

        # Act & Assert
        with pytest.raises(KeyError):
            get_pipeline_orchestrator()

    def test_get_pipeline_orchestrator_returns_registered_instance(self):
        """Test that get_pipeline_orchestrator returns the registered instance."""
        # Arrange
        mock_ado = Mock(spec=RepositoryClientPort)
        mock_ai = Mock(spec=AIModelClientPort)
        mock_orchestrator = Mock(spec=PipelineOrchestrator)

        from src.core.di import injector
        injector.register(PipelineOrchestrator, mock_orchestrator)

        # Act
        retrieved = get_pipeline_orchestrator()

        # Assert
        assert retrieved is mock_orchestrator

    def test_reset_pipeline_for_tests_clears_overrides(self):
        """Test that reset_pipeline_for_tests clears test overrides."""
        # Arrange
        from src.core.di import injector

        original = Mock(spec=PipelineOrchestrator)
        override = Mock(spec=PipelineOrchestrator)

        injector.register(PipelineOrchestrator, original)
        injector.override(PipelineOrchestrator, override)

        # Act
        reset_pipeline_for_tests()
        retrieved = injector.get(PipelineOrchestrator)

        # Assert
        assert retrieved is original


class TestBootstrapFunction:
    """Test suite for the bootstrap function."""

    @pytest.fixture(autouse=True)
    def cleanup_di_after_bootstrap(self):
        """Cleanup DI after each test."""
        yield
        from src.core.di import injector
        injector.clear_all()

    def test_bootstrap_with_valid_configuration(self, monkeypatch):
        """Test that bootstrap succeeds with valid configuration."""
        # Arrange
        monkeypatch.setenv("AZURE_ENDPOINT_CR", "https://test.openai.azure.com/")
        monkeypatch.setenv("AZURE_API_KEY_CR", "test-key-cr")
        monkeypatch.setenv("AZURE_MODEL_CR", "gpt-4")
        monkeypatch.setenv("AZURE_ENDPOINT_REQ", "https://test.openai.azure.com/")
        monkeypatch.setenv("AZURE_API_KEY_REQ", "test-key-req")
        monkeypatch.setenv("AZURE_MODEL_REQ", "gpt-4")
        monkeypatch.setenv("ADO_ORGANIZATION", "test-org")
        monkeypatch.setenv("ADO_PAT", "test-pat")

        # Reload config to pick up new env vars
        from importlib import reload
        import src.core.config
        reload(src.core.config)

        # Act
        from src.bootstrap import bootstrap_dependencies
        bootstrap_dependencies()

        # Assert
        from src.core.di import injector
        assert injector.get(PipelineOrchestrator) is not None
