import base64
import json
import secrets

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.background import BackgroundTasks

from ..core.config import settings
from ..core.logger import get_logger
from ..models.webhooks import ADOWebhookPayload

from ..core.metrics import metrics
from ..core.rate_limit import RateLimiter
from ..composition import get_pipeline_orchestrator, injector

log = get_logger("Webhooks")
router = APIRouter(prefix="/webhook", tags=["Webhooks"])

# Azure DevOps service hook payloads are typically < 100 KiB; cap to limit abuse.
_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


def check_webhook_secret(request: Request) -> bool:
    """Validates the webhook secret using Basic Authentication.

    Args:
        request: The incoming FastAPI request.

    Returns:
        bool: True if authentication is successful or not required.

    Raises:
        HTTPException: 401 error if authentication fails.
    """
    if not settings.WEBHOOK_SECRET:
        return True

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth_header.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    try:
        token = auth_header.split(" ", 1)[1]
        decoded_bytes = base64.b64decode(token)
        decoded_str = decoded_bytes.decode("utf-8")

        if ":" in decoded_str:
            _, password = decoded_str.split(":", 1)
        else:
            password = decoded_str

        if not secrets.compare_digest(password, settings.WEBHOOK_SECRET):
            log.warning("Webhook Basic Auth rejected: credential mismatch")
            raise HTTPException(status_code=401, detail="Invalid credentials")

    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Webhook Basic Auth parse failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    return True


@router.post("/orchestrate", dependencies=[Depends(check_webhook_secret)])
async def webhook_orchestrate(payload: ADOWebhookPayload, background_tasks: BackgroundTasks) -> JSONResponse:
    """Receives ADO PR events and triggers the sequential pipeline.

    Validated via Pydantic model for strict schema enforcement.
    """
    limiter = injector.get(RateLimiter)
    if not limiter.is_allowed():
        log.warning("Rate limit exceeded for webhooks")
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    evento = payload.eventType
    if evento not in ("git.pullrequest.created", "git.pullrequest.updated"):
        return JSONResponse({"status": "ignored", "reason": f"Event type '{evento}' not supported"})

    pr = payload.resource
    pr_id = pr.pullRequestId
    repo_id = pr.repository.id
    project = pr.repository.project.name
    pr_status = pr.status

    if pr_status.lower() != "active":
        log.info("Ignoring PR #%s because status is '%s'", pr_id, pr_status)
        return JSONResponse({"status": "ignored", "reason": f"PR is {pr_status}, only 'active' PRs are processed."})

    log.info("Received orchestrator webhook for PR #%s (Project: %s)", pr_id, project)

    orchestrator = get_pipeline_orchestrator()
    background_tasks.add_task(orchestrator.process_pr_pipeline, pr_id, repo_id, project)

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message": f"Orchestrator Pipeline started for PR {pr_id}",
            "details": {
                "pr_id": pr_id,
                "project": project,
                "event": evento
            }
        }
    )
