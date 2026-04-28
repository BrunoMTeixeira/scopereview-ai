from .core.logger import get_logger
from .services.orchestrator import PipelineOrchestrator
from .core.di import injector

_log = get_logger("Composition")


def get_pipeline_orchestrator() -> PipelineOrchestrator:
    """
    Retrieve the singleton PipelineOrchestrator from DI container.

    The PipelineOrchestrator coordinates the execution of the code review
    and requirements validation agents in a sequential pipeline. It is
    managed by the global dependency injector as a singleton, ensuring
    consistent state across the entire application lifecycle.

    Returns:
        PipelineOrchestrator: The orchestrator singleton instance.

    Raises:
        KeyError: If the PipelineOrchestrator is not registered in the DI
                 container. This typically indicates a configuration error
                 during application startup.

    Example:
        >>> orchestrator = get_pipeline_orchestrator()
        >>> orchestrator.process_pr_pipeline(pr_id=123, repo_id="abc",
        ...                                   project="proj")

    Notes:
        - This function is used by the webhook handler to trigger the
          pipeline.
        - The injector ensures that the same instance is returned on
          every call.
        - For testing, use `injector.override()` to substitute a test
          double.
    """
    try:
        instance = injector.get(PipelineOrchestrator)
        _log.debug("PipelineOrchestrator retrieved from DI container")
        return instance
    except KeyError as e:
        _log.error(
            "Failed to retrieve PipelineOrchestrator: %s. "
            "Ensure it is registered during application startup.",
            str(e),
        )
        raise


def reset_pipeline_for_tests() -> None:
    """
    Reset the DI container to a clean state for test execution.

    This function clears all test overrides registered via
    `injector.override()`, allowing subsequent tests to use the original
    registered instances or define new ones.

    Usage:
        Use this in test fixture cleanup to ensure isolation between test
        cases:

        ```python
        @pytest.fixture(autouse=True)
        def cleanup_di_after_test():
            yield
            reset_pipeline_for_tests()
        ```

    Notes:
        - This function does NOT clear the main registry of services.
        - It only clears test-specific overrides created via
          `injector.override()`.
        - For complete cleanup, use `injector.clear_all()` (use sparingly
          in tests).
    """
    _log.debug("Resetting test overrides in DI container")
    injector.clear_overrides()


def register_service(service_type: type, instance: object) -> None:
    """
    Register a service instance in the DI container.

    This is typically called during application startup to bootstrap all
    dependencies.

    Args:
        service_type (type): The service interface or class.
        instance (object): The concrete instance to register.

    Raises:
        TypeError: If the instance does not match the expected service
                  type.

    Example:
        >>> ai_client = AzureOpenAIClient(endpoint, api_key, model)
        >>> register_service(AIModelClientPort, ai_client)

    Notes:
        - Services are singletons and reused throughout the application.
        - Calling this multiple times for the same service_type will log
          a warning.
        - Consider calling this from a dedicated bootstrap or
          configuration module.
    """
    try:
        injector.register(service_type, instance)
        _log.info("Service registered: %s", service_type.__name__)
    except (TypeError, ValueError) as e:
        _log.error(
            "Failed to register service %s: %s",
            service_type.__name__,
            str(e),
        )
        raise
