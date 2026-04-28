from typing import Any
from ..core.logger import get_logger

_log = get_logger("DependencyInjector")


class SimpleDependencyInjector:
    """
    Lightweight, thread-safe dependency injector for managing singletons.

    This injector follows a simple registry pattern suitable for FastAPI
    applications. It ensures:
    - Services are instantiated once and reused (singleton pattern)
    - Dependencies can be registered and retrieved in a type-safe manner
    - Override capabilities for testing scenarios

    Example:
        >>> injector = SimpleDependencyInjector()
        >>> injector.register(PipelineOrchestrator, my_orchestrator)
        >>> orchestrator = injector.get(PipelineOrchestrator)
    """

    def __init__(self) -> None:
        """Initialize an empty dependency registry."""
        self._registry: dict[type, Any] = {}
        self._overrides: dict[type, Any] = {}

    def register(self, service_type: type, instance: Any) -> None:
        """
        Register a service instance in the container.

        Args:
            service_type (type): The service interface or class.
            instance (Any): The concrete instance to register.

        Raises:
            TypeError: If instance is not of the expected type.
            ValueError: If the service is already registered.

        Example:
            >>> injector.register(AIModelClientPort, AzureOpenAIClient(...))
        """
        if not isinstance(instance, service_type):
            raise TypeError(
                f"Instance of {service_type.__name__} expected, "
                f"got {type(instance).__name__}"
            )

        if service_type in self._registry:
            _log.warning(
                "Service %s is already registered. Overwriting.",
                service_type.__name__,
            )

        self._registry[service_type] = instance
        _log.debug("Registered service: %s", service_type.__name__)

    def get(self, service_type: type) -> Any:
        """
        Retrieve a registered service instance.

        Checks overrides first (for testing), then the main registry.

        Args:
            service_type (type): The service interface or class to retrieve.

        Returns:
            Any: The registered instance.

        Raises:
            KeyError: If the service is not registered.

        Example:
            >>> orchestrator = injector.get(PipelineOrchestrator)
        """
        # Check test overrides first
        if service_type in self._overrides:
            _log.debug(
                "Retrieved override for service: %s", service_type.__name__
            )
            return self._overrides[service_type]

        # Then check main registry
        if service_type not in self._registry:
            raise KeyError(
                f"Service {service_type.__name__} is not registered. "
                f"Available services: {list(self._registry.keys())}"
            )

        _log.debug("Retrieved service: %s", service_type.__name__)
        return self._registry[service_type]

    def override(self, service_type: type, instance: Any) -> None:
        """
        Override a registered service with a test double.

        Args:
            service_type (type): The service to override.
            instance (Any): The replacement instance (typically a test double).

        Example:
            >>> mock_ai = MockAIClient()
            >>> injector.override(AIModelClientPort, mock_ai)
        """
        if not isinstance(instance, service_type):
            _log.warning(
                "Override instance of %s is not a strict type match. "
                "Type: %s, Expected: %s",
                service_type.__name__,
                type(instance).__name__,
                service_type.__name__,
            )

        self._overrides[service_type] = instance
        _log.info(
            "Registered test override for service: %s", service_type.__name__
        )

    def clear_overrides(self) -> None:
        """
        Clear all test overrides, reverting to registered services.

        Useful for cleanup between test cases.
        """
        self._overrides.clear()
        _log.debug("Cleared all test overrides")

    def clear_all(self) -> None:
        """
        Clear all registered services and overrides.

        Use with caution; typically only for testing cleanup.
        """
        self._registry.clear()
        self._overrides.clear()
        _log.debug("Cleared all registered services and overrides")


# Global singleton injector instance
injector: SimpleDependencyInjector = SimpleDependencyInjector()
