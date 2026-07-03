import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError

from .core.config import settings, validate_settings
from .core.logger import setup_logging, get_logger
from .bootstrap import bootstrap_dependencies
from .api.webhooks import router as webhooks_router
from .core.metrics import metrics
import asyncio
from .core.worker_pool import pr_worker

# Setup logging before any other imports
setup_logging()
_log = get_logger("Main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage FastAPI application lifecycle: startup and shutdown.

    Startup:
        - Validate configuration from environment variables
        - Bootstrap all dependencies in the DI container
        - Initialize external connections (Azure AI, ADO, Redis if used)

    Shutdown:
        - Cleanup resources (connection pools, Redis, etc.)
        - Flush logs

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Indicates that startup is complete and app is ready to serve.

    Raises:
        EnvironmentError: If configuration validation fails.
        ConnectionError: If external services cannot be reached.
    """
    _log.info("=" * 80)
    _log.info("ScopeReview AI — Application Startup")
    _log.info("=" * 80)

    try:
        # Step 1: Validate configuration
        allow_http = os.environ.get("SCOPE_REVIEW_ALLOW_HTTP_AI", "").lower() in ("1", "true", "yes")
        _log.debug("HTTP AI endpoints allowed: %s", allow_http)

        validate_settings(require_https_endpoints=not allow_http)
        _log.info("✓ Configuration validated")

        # Step 2: Bootstrap dependencies
        bootstrap_dependencies()
        _log.info("✓ Dependency injection bootstrapped")

        # Step 3: Start Bounded Worker Pool
        max_workers = settings.MAX_WORKERS
        app.state.pr_queue = asyncio.Queue()
        app.state.workers = []
        for i in range(max_workers):
            task = asyncio.create_task(pr_worker(i, app.state.pr_queue))
            app.state.workers.append(task)
        _log.info("✓ Started %d background workers for PR processing", max_workers)

        _log.info("=" * 80)
        _log.info("✓ Application ready to serve requests")
        _log.info("=" * 80)

    except Exception as exc:
        _log.critical("Application startup failed: %s", str(exc), exc_info=True)
        raise

    # Application runs here (between startup and shutdown)
    yield

    # Cleanup on shutdown
    _log.info("=" * 80)
    _log.info("ScopeReview AI — Application Shutdown")
    _log.info("=" * 80)
    
    # Gracefully shutdown workers
    workers = getattr(app.state, "workers", [])
    if workers:
        _log.info("Cancelling %d background workers...", len(workers))
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        _log.info("✓ Background workers shut down")

    _log.info("✓ Application stopped cleanly")


# Create FastAPI app with lifespan management
app = FastAPI(
    title="ScopeReview AI",
    description="Automated Code Review and Requirements Validation Pipeline for Azure DevOps",
    version="2.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Include routers
app.include_router(webhooks_router)


@app.get("/health", tags=["Health"])
def health_check() -> JSONResponse:
    """
    Deep health check endpoint for monitoring and orchestration tools (Issue #47).
    Validates that the DI container and all core ports (Azure AI, ADO, Redis)
    are instantiated and successfully wired together in the Composition Root.
    """
    from .composition import get_pipeline_orchestrator

    health_status = "ok"
    details = {}

    try:
        # Check if we can resolve the main orchestrator
        orchestrator = get_pipeline_orchestrator()
        if not orchestrator:
            health_status = "error"
            details["orchestrator"] = "failed to resolve"
        else:
            details["orchestrator"] = "ready"

    except Exception as exc:
        health_status = "error"
        details["error"] = str(exc)

    return JSONResponse(
        status_code=200 if health_status == "ok" else 503,
        content={"status": health_status, "system": "ScopeReview AI", "version": "2.0.0", "details": details},
    )


@app.get("/metrics", tags=["Health"])
async def get_metrics() -> dict:
    """
    Exposes real-time system metrics and AI performance telemetry.
    Exports token consumption, pipeline latency, and success rates for observability.
    """
    return metrics.get_summary()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Masks schema validation errors to prevent leaking internal API structure.
    Returns a generic 400 Bad Request instead of FastAPI's default 422 which leaks schema details.
    """
    _log.warning("Payload validation failed for %s %s", request.method, request.url)
    return JSONResponse(
        {"error": "Bad Request: Invalid payload structure or missing required fields."}, status_code=400
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for unhandled errors.

    Logs the error and returns a generic 500 response to avoid leaking
    sensitive information to clients.
    """
    _log.exception("Unhandled exception in %s %s: %s", request.method, request.url, str(exc))
    return JSONResponse({"error": "Internal server error. Check logs for details."}, status_code=500)
