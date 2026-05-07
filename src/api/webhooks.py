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
from ..services.orchestrator import PipelineOrchestrator

log = get_logger("Webhooks")
router = APIRouter(prefix="/webhook", tags=["Webhooks"])

# Azure DevOps service hook payloads are typically < 100 KiB; cap to limit abuse.
_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


import hashlib
import hmac

async def check_webhook_secret(request: Request) -> bool:
    """Validates the webhook secret using HTTPS, Basic Auth, or HMAC Signature.

    Args:
        request: The incoming FastAPI request.

    Returns:
        bool: True if authentication is successful or not required.

    Raises:
        HTTPException: 401 error if authentication fails, 403 for insecure connections.
    """
    # 1. Validate payload size before authentication (protection against DOS)
    content_length = request.headers.get("Content-Length")
    if content_length and int(content_length) > _MAX_WEBHOOK_BODY_BYTES:
        log.error("Payload too large: %s bytes (max allowed: %s)", content_length, _MAX_WEBHOOK_BODY_BYTES)
        raise HTTPException(status_code=413, detail="Payload too large")

    # 2. Enforce HTTPS in production (Network Hardening)
    if settings.is_production:
        scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
        if scheme != "https":
            log.warning("Insecure webhook request rejected (HTTP instead of HTTPS)")
            raise HTTPException(status_code=403, detail="HTTPS required in production")

    if not settings.WEBHOOK_SECRET:
        return True

    # 3. HMAC Signature Validation (if provided by gateway/proxy)
    hmac_header = request.headers.get("X-Hub-Signature-256")
    if hmac_header:
        body = await request.body()
        expected_mac = hmac.new(settings.WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not secrets.compare_digest(hmac_header.replace("sha256=", ""), expected_mac):
            log.warning("Webhook HMAC Auth rejected: signature mismatch")
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")
        return True

    # 4. Fallback to Basic Auth (Standard Azure DevOps Service Hooks)

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


def check_rate_limit() -> None:
    limiter = injector.get(RateLimiter)
    if not limiter.is_allowed():
        log.warning("Rate limit exceeded for webhooks")
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")


@router.post("/orchestrate", dependencies=[Depends(check_webhook_secret), Depends(check_rate_limit)])
async def webhook_orchestrate(
    payload: ADOWebhookPayload, 
    background_tasks: BackgroundTasks,
    orchestrator: PipelineOrchestrator = Depends(get_pipeline_orchestrator)
) -> JSONResponse:
    """Receives ADO PR events and triggers the sequential pipeline asynchronously.

    Validated via Pydantic model for strict schema enforcement.
    Uses BackgroundTasks to mitigate HTTP timeouts from the caller (Issue #21).
    """
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
