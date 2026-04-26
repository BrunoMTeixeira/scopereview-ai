"""
ScopeReview AI · main.py

Entry point and orchestrator for the ScopeReview AI multi-agent system.
Mounts both agents into a single FastAPI application so they can run
on the same port and be exposed through a single ngrok tunnel.

Endpoints registered:
  POST /webhook                   → Code Review Agent
  POST /webhook/requirements      → Requirements Review Agent
  GET  /health                    → System health check
  GET  /health/code-review        → Code Review Agent health
  GET  /health/requirements       → Requirements Review Agent health

Author: Bruno Teixeira — ISEP / DevScope — 2025/2026
"""

import logging
from fastapi import FastAPI, Request, BackgroundTasks

import code_review_agent
import requirements_review_agent
import shared_state

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ScopeReviewAI.Main")

# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="ScopeReview AI",
    description="Multi-agent automated PR analysis system for Azure DevOps.",
    version="1.0.0",
)

@app.post("/webhook/orchestrate")
async def handle_pr_webhook(request: Request, bg_tasks: BackgroundTasks):
    payload = await request.json()
    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId")
    
    if not pr_id:
        return {"msg": "No PR ID"}

    if shared_state.verificar_duplicado(pr_id, agent="orchestrator"):
        return {"msg": "PR already being orchestrated"}

    bg_tasks.add_task(_pipeline_review, payload)
    return {"msg": "Review Pipeline scheduled"}

def _pipeline_review(payload: dict):
    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId")
    try:
        log.info(f"🚀 Iniciando Pipeline para o PR #{pr_id}")

        log.info(f"Fase 1: A executar Code Review Agent...")
        code_review_findings = code_review_agent._processar_pr_sync(payload)
        
        relevant_findings = [
            f for f in code_review_findings 
            if f["type"] in ("quality", "bug")
        ]
        
        log.info(f"Fase 2: A executar Requirements Agent com {len(relevant_findings)} Code Findings...")
        requirements_review_agent._processar_pr_sync(payload, injected_findings=relevant_findings)
        
        log.info(f"✅ Pipeline terminada com sucesso para o PR #{pr_id}")

    except Exception as e:
        log.error(f"Erro na pipeline do PR #{pr_id}: {e}")
    finally:
        shared_state.limpar_pr(pr_id, agent="orchestrator")

# Mount both agents — each exposes its own router
app.include_router(code_review_agent.router)
app.include_router(requirements_review_agent.router)

log.info("✓ Code Review Agent mounted at /webhook")
log.info("✓ Requirements Review Agent mounted at /webhook/requirements")


# ─── System health endpoint ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """
    Top-level health check for the full ScopeReview AI system.
    Returns the status of both agents and the model in use.
    """
    status = {
        "status": "ok",
        "system": "ScopeReview AI",
        "version": "1.0.0",
        "agents": {
            "code_review":    {"endpoint": "POST /webhook",              "status": "active"},
            "requirements":   {"endpoint": "POST /webhook/requirements",  "status": "active"},
        },
        "model": code_review_agent.AZURE_MODEL,
    }

    return status
