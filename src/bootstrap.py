from .core.config import settings
from .core.logger import get_logger
from .core.pipeline_dedup import InMemoryPipelineDedup
from .infra.redis_dedup import RedisPipelineDedup
from .infra.azure_ai import AzureOpenAIClient
from .infra.azure_devops import AzureDevOpsClient
from .ports.dedup import PipelineDedupPort
from .ports.ai_client import AIModelClientPort
from .ports.ado_client import AzureDevOpsClientPort
from .services.code_review import CodeReviewService
from .services.requirements_review import RequirementsReviewService
from .services.static_analyzer import StaticAnalyzer
from .services.orchestrator import PipelineOrchestrator
from .core.rate_limit import RateLimiter
from .core.metrics import metrics
from .composition import register_service

_log = get_logger("Bootstrap")


def bootstrap_dependencies() -> None:
    """
    Initialize and register all application dependencies in the DI.

    This function is called during application startup (lifespan event in
    main.py) to wire all ports, adapters, services, and the orchestrator.

    Dependency Graph:
        1. Create infrastructure adapters (Azure AI, ADO, Dedup)
        2. Create domain services (CodeReview, RequirementsReview)
        3. Create the orchestrator (depends on all above)
        4. Register everything in the DI container

    Raises:
        EnvironmentError: If required configuration is missing or invalid.
        ConnectionError: If infrastructure services (e.g., Redis, Azure AI)
                       cannot be reached.

    Notes:
        - This function should be called only once at application startup.
        - It performs validation and will raise exceptions if
          configuration is incorrect.
        - Check logs for detailed information about registered services.
    """
    _log.info("Starting dependency bootstrap...")

    try:
        # ===== Infrastructure Layer (Ports Implementation) =====

        # MULTI-PROVIDER ROUTING (#42)
        # The architecture dynamically routes tasks to different AI models/providers.
        # - Code Review (Reasoning intensive): Routed to AZURE_*_CR credentials (e.g., o4-mini)
        # - Requirements Validation (Context heavy): Routed to AZURE_*_REQ credentials (e.g., DeepSeek-V3.2)
        code_review_ai = AzureOpenAIClient(
            endpoint=settings.AZURE_ENDPOINT_CR,
            api_key=settings.AZURE_API_KEY_CR,
            model_name=settings.AZURE_MODEL_CR,
            max_retries=settings.MAX_TENTATIVAS,
        )
        _log.info(
            "Initialized Code Review AI client (%s)",
            settings.AZURE_MODEL_CR,
        )

        requirements_ai = AzureOpenAIClient(
            endpoint=settings.AZURE_ENDPOINT_REQ,
            api_key=settings.AZURE_API_KEY_REQ,
            model_name=settings.AZURE_MODEL_REQ,
            max_retries=settings.MAX_TENTATIVAS,
        )
        _log.info(
            "Initialized Requirements AI client (%s)",
            settings.AZURE_MODEL_REQ,
        )

        # Azure DevOps Client
        ado_client = AzureDevOpsClient(
            organization=settings.ADO_ORGANIZATION,
            pat=settings.ADO_PAT,
            max_files=settings.MAX_FILES,
            max_lines=settings.MAX_LINES,
            request_timeout=settings.ADO_REQUEST_TIMEOUT,
        )
        _log.info(
            "Initialized Azure DevOps client (%s)",
            settings.ADO_ORGANIZATION,
        )

        # Deduplication (Redis if configured, else in-memory)
        if settings.DEDUP_REDIS_URL:
            dedup: PipelineDedupPort = RedisPipelineDedup(
                url=settings.DEDUP_REDIS_URL,
                ttl_seconds=settings.DEDUP_SECONDS,
                key_prefix=settings.DEDUP_REDIS_KEY_PREFIX,
            )
            _log.info(
                "Using Redis deduplication (%s)",
                settings.DEDUP_REDIS_URL,
            )
        else:
            dedup = InMemoryPipelineDedup(ttl_seconds=settings.DEDUP_SECONDS)
            _log.info(
                "Using in-memory deduplication (single-process mode)"
            )

        # Rate Limiter
        limiter = RateLimiter(
            requests_limit=settings.WEBHOOK_RATE_LIMIT,
            window_seconds=60
        )
        _log.info("Initialized RateLimiter (limit: %s/min)", settings.WEBHOOK_RATE_LIMIT)

        # ===== Domain Services Layer =====

        static_analyzer = StaticAnalyzer()
        _log.debug("Initialized StaticAnalyzer")

        code_review_service = CodeReviewService(
            ai=code_review_ai,
            max_high_block=settings.MAX_HIGH_BLOCK,
            max_token_budget=settings.MAX_TOKEN_BUDGET,
        )
        _log.info("Initialized CodeReviewService")

        requirements_review_service = RequirementsReviewService(
            ai=requirements_ai,
            max_completion_tokens=settings.REQUIREMENTS_MAX_COMPLETION_TOKENS,
        )
        _log.info("Initialized RequirementsReviewService")

        # ===== Application Orchestrator =====

        orchestrator = PipelineOrchestrator(
            ado=ado_client,
            code_review=code_review_service,
            requirements_review=requirements_review_service,
            dedup=dedup,
            code_model_display_name=settings.AZURE_MODEL_CR,
            requirements_model_display_name=settings.AZURE_MODEL_REQ,
        )
        _log.info("Initialized PipelineOrchestrator")

        # ===== Register in DI Container =====

        register_service(AIModelClientPort, code_review_ai)
        register_service(AzureDevOpsClientPort, ado_client)
        register_service(PipelineDedupPort, dedup)
        register_service(StaticAnalyzer, static_analyzer)
        register_service(CodeReviewService, code_review_service)
        register_service(
            RequirementsReviewService,
            requirements_review_service,
        )
        register_service(PipelineOrchestrator, orchestrator)
        register_service(RateLimiter, limiter)
        # We register the singleton metrics for visibility in DI
        from .core.metrics import SystemMetrics
        register_service(SystemMetrics, metrics)

        _log.info("✓ Dependencies bootstrapped successfully")

    except Exception as exc:
        _log.critical(
            "Failed to bootstrap dependencies: %s. "
            "Application startup aborted.",
            str(exc),
            exc_info=True,
        )
        raise
