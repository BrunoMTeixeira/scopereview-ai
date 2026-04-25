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

from fastapi import FastAPI

import code_review_agent
import requirements_review_agent

# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="ScopeReview AI",
    description="Multi-agent automated PR analysis system for Azure DevOps.",
    version="1.0.0",
)

# Mount both agents — each exposes its own router
app.include_router(code_review_agent.router)
app.include_router(requirements_review_agent.router)


# ─── System health endpoint ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """
    Top-level health check for the full ScopeReview AI system.
    Returns the status of both agents and the model in use.
    """
    return {
        "status": "ok",
        "system": "ScopeReview AI",
        "version": "1.0.0",
        "agents": {
            "code_review":    {"endpoint": "POST /webhook",              "status": "active"},
            "requirements":   {"endpoint": "POST /webhook/requirements",  "status": "active"},
        },
        "model": code_review_agent.AZURE_MODEL,
    }
