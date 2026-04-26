"""
ScopeReview AI · shared_state.py

Shared state between agents to ensure thread-safe deduplication
across both Code Review and Requirements Review agents.

Author: Bruno Teixeira — ISEP / DevScope — 2025/2026
"""

import threading
import time
from typing import Dict, Tuple

# ─── SHARED STATE ─────────────────────────────────────────────────────────────
# Each agent tracks its own deduplication independently using (agent, pr_id) keys
_prs_processados: Dict[Tuple[str, int], float] = {}
_prs_lock = threading.Lock()

DEDUP_SECONDS = 300


def verificar_duplicado(pr_id: int, agent: str = "default") -> bool:
    """
    Thread-safe duplicate check per agent.
    Each agent can process the same PR independently — only duplicate
    webhooks for the SAME agent and PR are blocked.
    """
    agora = time.time()
    key = (agent, pr_id)
    with _prs_lock:
        # Clean up expired entries
        expiradas = [k for k, v in _prs_processados.items() if agora - v > DEDUP_SECONDS]
        for k in expiradas:
            del _prs_processados[k]

        # Check if this agent already processed this PR
        if (agora - _prs_processados.get(key, 0)) < DEDUP_SECONDS:
            return True

        # Mark as processing
        _prs_processados[key] = agora
        return False


def limpar_pr(pr_id: int, agent: str = "default") -> None:
    """Manually clear a PR from the deduplication cache (after processing completes)."""
    key = (agent, pr_id)
    with _prs_lock:
        if key in _prs_processados:
            del _prs_processados[key]
