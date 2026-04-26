"""
ScopeReview AI · requirements_review_agent.py

Requirements validation agent for Azure DevOps Pull Requests.
Powered by Llama-3.3-70B-Instruct via Azure AI Foundry.

Validates whether the code changes in a PR correctly implement the
business requirements defined in the linked Work Items (tasks/US from the board).

High-level flow:
  1. Azure DevOps fires a webhook when a PR is created or updated
  2. Agent fetches all Work Items linked to the PR via ADO API
  3. For each Work Item: reads title, description, acceptance criteria and comments
  4. Also reads a repository-level rules file (.requirements.yml) if present
  5. The LLM compares requirements against the actual code changes
  6. A structured validation report is published as a separate PR comment

This file exposes an APIRouter (not a FastAPI app directly).
The router is mounted by main.py into the shared application instance.

Author: Bruno Teixeira — ISEP / DevScope — 2025/2026
"""

# ─── Imports ──────────────────────────────────────────────────────────────────
import json
import base64
import logging
import os
import re
import time
import random
from collections import Counter
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("ScopeReviewAI.Requirements")


# ─── Configuration ────────────────────────────────────────────────────────────

def _obter_env_obrigatoria(chave: str) -> str:
    valor = os.getenv(chave)
    if not valor:
        raise EnvironmentError(
            f"Required environment variable missing: '{chave}'. Check your .env file."
        )
    return valor


AZURE_ENDPOINT = _obter_env_obrigatoria("AZURE_ENDPOINT")
AZURE_MODEL = os.getenv("AZURE_MODEL", "Llama-3.3-70B-Instruct")
AZURE_API_KEY = _obter_env_obrigatoria("AZURE_API_KEY")
ADO_ORGANIZATION = _obter_env_obrigatoria("ADO_ORGANIZATION")
ADO_PAT = _obter_env_obrigatoria("ADO_PAT")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Validate HTTPS for credential safety
if not AZURE_ENDPOINT.startswith("https://"):
    raise EnvironmentError("AZURE_ENDPOINT must use HTTPS to protect API keys in transit.")

MAX_FILES = int(os.getenv("MAX_FILES", "5"))
MAX_LINES = int(os.getenv("MAX_LINES", "400"))
MAX_TOKENS = 16000
TIMEOUT = 180
MAX_TENTATIVAS = 3
ADO_REQUEST_TIMEOUT = 20

REQUIREMENTS_FILE_CANDIDATES = [
    "/.requirements.yml",
    "/.requirements.yaml",
    "/.scope-requirements.yml",
    "/.codereview.yml",
    "/docs/requirements.md",
    "/REQUIREMENTS.md",
]

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".lock", ".gitignore",
}

LANG_MAP = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "cs": "csharp", "java": "java", "go": "go", "cpp": "cpp",
}

WI_FIELDS = [
    "System.Title",
    "System.Description",
    "System.WorkItemType",
    "System.State",
    "System.Tags",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "Microsoft.VSTS.Scheduling.StoryPoints",
]

# ─── State ────────────────────────────────────────────────────────────────────
import shared_state

# ─── Router ───────────────────────────────────────────────────────────────────
router = APIRouter(tags=["Requirements Review"])

# ══════════════════════════════════════════════════════════════════════════════
# Class
# ══════════════════════════════════════════════════════════════════════════════

class RequirementsResult(BaseModel):
    work_items_analysed: List[dict] = []
    requirements: List[dict] = []
    overall_verdict: str
    verdict_reason: str
    implementation_summary: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AZURE DEVOPS
# ══════════════════════════════════════════════════════════════════════════════

_ADO_AUTH_HEADER = f"Basic {base64.b64encode(f':{ADO_PAT}'.encode()).decode()}"


def _ado_headers() -> dict:
    return {"Authorization": _ADO_AUTH_HEADER, "Content-Type": "application/json"}


def _linguagem(caminho: str) -> str:
    ext = caminho.rsplit(".", 1)[-1].lower() if "." in caminho else ""
    return LANG_MAP.get(ext, "")



def _limpar_html(texto: str) -> str:
    """Strips HTML tags from ADO rich-text fields (Description, AcceptanceCriteria)."""
    if not texto:
        return ""
    limpo = re.sub(r"<[^>]+>", " ", texto)
    limpo = re.sub(r"&nbsp;", " ", limpo)
    limpo = re.sub(r"&lt;", "<", limpo)
    limpo = re.sub(r"&gt;", ">", limpo)
    limpo = re.sub(r"&amp;", "&", limpo)
    limpo = re.sub(r"\s{2,}", " ", limpo)
    return limpo.strip()


# ── PR details ────────────────────────────────────────────────────────────────

def obter_detalhes_pr(repo_id: str, pr_id: int, project: str) -> dict:
    """Fetches PR metadata: title, description, author, branch, commit SHA."""
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {
            "title": data.get("title", ""),
            "description": data.get("description", "") or "",
            "author": data.get("createdBy", {}).get("displayName", "Unknown"),
            "source_branch": data.get("sourceRefName", "").replace("refs/heads/", ""),
            "commit_sha": data.get("lastMergeSourceCommit", {}).get("commitId", ""),
        }
    except requests.RequestException as exc:
        log.error("Failed to fetch PR details: %s", exc)
        return {}


# ── Work Items ────────────────────────────────────────────────────────────────

def obter_ids_work_items_pr(repo_id: str, pr_id: int, project: str) -> List[int]:
    """
    Returns IDs of all Work Items linked to this PR.
    Links are created automatically when developers reference #ID in commits,
    or manually via the PR interface.
    """
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}"
        f"/workitems?api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        ids = [item["id"] for item in items if "id" in item]
        log.info("Work Items linked to PR #%s: %s", pr_id, ids)
        return ids
    except requests.RequestException as exc:
        log.warning("Failed to fetch Work Item IDs: %s", exc)
        return []


def obter_detalhes_work_item(wi_id: int) -> Optional[dict]:
    """
    Fetches full details of a Work Item including Acceptance Criteria.
    AcceptanceCriteria is the primary source of requirements.
    """
    fields_param = ",".join(WI_FIELDS)
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}"
        f"/_apis/wit/workitems/{wi_id}"
        f"?fields={fields_param}&api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        fields = resp.json().get("fields", {})
        return {
            "id": wi_id,
            "type": fields.get("System.WorkItemType", "Work Item"),
            "title": fields.get("System.Title", ""),
            "state": fields.get("System.State", ""),
            "description": _limpar_html(fields.get("System.Description", "")),
            "acceptance_criteria": _limpar_html(
                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
            ),
            "tags": fields.get("System.Tags", ""),
        }
    except requests.RequestException as exc:
        log.warning("Failed to fetch Work Item #%s: %s", wi_id, exc)
        return None


def obter_comentarios_work_item(wi_id: int) -> List[str]:
    """
    Fetches comments on a Work Item.
    Comments often contain requirement clarifications added during the sprint.
    """
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}"
        f"/_apis/wit/workitems/{wi_id}/comments?api-version=7.1-preview.3"
    )
    comentarios = []
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)

        # --- PROTEÇÃO ADICIONADA AQUI ---
        if resp.status_code == 404:
            return []

        resp.raise_for_status()
        for comment in resp.json().get("comments", []):
            texto = _limpar_html(comment.get("text", ""))
            author = comment.get("createdBy", {}).get("displayName", "")
            if texto:
                comentarios.append(f"[{author}]: {texto}")
        return comentarios
    except requests.RequestException as exc:
        log.warning("Failed to fetch comments for Work Item #%s: %s", wi_id, exc)
        return []


def obter_todos_work_items(repo_id: str, pr_id: int, project: str) -> List[dict]:
    """Collects all Work Items linked to the PR with their full details and comments."""
    ids = obter_ids_work_items_pr(repo_id, pr_id, project)
    if not ids:
        return []
    work_items = []
    for wi_id in ids:
        detalhes = obter_detalhes_work_item(wi_id)
        if detalhes:
            if detalhes.get("state") in ("Closed", "Removed", "Cancelled"):
                log.info("Work Item #%s skipped — state is '%s'", wi_id, detalhes["state"])
                continue
            detalhes["comments"] = obter_comentarios_work_item(wi_id)
            work_items.append(detalhes)
            # --- MUDANÇA DO %d PARA %s FEITA AQUI ---
            log.info(
                "Work Item #%s loaded: [%s] %s",
                wi_id, detalhes["type"], detalhes["title"],
            )
    return work_items


# ── Repository rules file ─────────────────────────────────────────────────────

def obter_ficheiro_repositorio(repo_id: str, project: str,
                               path: str, commit_sha: str) -> str:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/items"
        f"?path={path}&versionDescriptor.version={commit_sha}"
        f"&versionDescriptor.versionType=commit&api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)

        # 404 is expected — file simply doesn't exist in this repo
        if resp.status_code == 404:
            return ""

        resp.raise_for_status()
        return resp.text.strip()

    except requests.RequestException as exc:
        log.warning("Failed to read repository file %s: %s", path, exc)
        return ""


def obter_regras_repositorio(repo_id: str, project: str, commit_sha: str) -> str:
    """Tries each candidate path and returns the first requirements file found."""
    for candidate in REQUIREMENTS_FILE_CANDIDATES:
        conteudo = obter_ficheiro_repositorio(repo_id, project, candidate, commit_sha)
        if conteudo:
            log.info("Repository rules file found: %s", candidate)
            return conteudo
    log.info("No repository rules file found.")
    return ""


# ── Changed files ─────────────────────────────────────────────────────────────

def obter_conteudo_ficheiro(repo_id: str, project: str,
                            path: str, commit_sha: str) -> str:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/items"
        f"?path={path}&versionDescriptor.version={commit_sha}"
        f"&versionDescriptor.versionType=commit&api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        linhas = resp.text.splitlines()[:MAX_LINES]
        return "\n".join([f"{i + 1:>4} | {l}" for i, l in enumerate(linhas)])
    except requests.RequestException as exc:
        log.warning("Failed to read file %s: %s", path, exc)
        return ""


def obter_ficheiros_alterados(repo_id: str, pr_id: int,
                              project: str, commit_sha: str) -> Dict[str, str]:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}"
        f"/iterations?api-version=7.1"
    )
    mapa = {}
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        iterations = resp.json().get("value", [])
        if not iterations:
            log.warning("No iterations found for PR #%s", pr_id)
            return {}
        iter_id = iterations[-1]["id"]
        url_changes = (
            f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}"
            f"/iterations/{iter_id}/changes?api-version=7.1"
        )
        resp_changes = requests.get(url_changes, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp_changes.raise_for_status()
        changes = resp_changes.json().get("changeEntries", [])
        for change in changes[:MAX_FILES]:
            # Skip rename-only changes (no code modification)
            if change.get("changeType") in ("rename",):
                continue
            path = change.get("item", {}).get("path", "")
            if path and not any(path.lower().endswith(ext) for ext in IGNORED_EXTENSIONS):
                conteudo = obter_conteudo_ficheiro(repo_id, project, path, commit_sha)
                if conteudo:
                    mapa[path] = conteudo
        return mapa
    except requests.RequestException as exc:
        log.error("Failed to list changed files: %s", exc)
        return {}


# ── Publish ───────────────────────────────────────────────────────────────────

def publicar_comentario(repo_id: str, pr_id: int, project: str, texto: str):
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    payload = {
        "comments": [{"content": texto, "parentCommentId": 0, "commentType": 1}],
        "status": 1,
    }
    try:
        requests.post(url, headers=_ado_headers(), json=payload, timeout=ADO_REQUEST_TIMEOUT).raise_for_status()
        log.info("Requirements report published in PR #%s", pr_id)
    except requests.RequestException as exc:
        log.error("Failed to publish requirements report: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_json(raw: str) -> str:
    """Fix common LLM JSON issues: invalid escape sequences and trailing commas."""
    import re
    # Fix invalid escape sequences (e.g. \d, \s, \w) — only valid JSON escapes are: \" \\ \/ \b \f \n \r \t \uXXXX
    fixed = re.sub(r'\\(?!["\\\//bfnrtu])', r'\\\\', raw)
    # Remove trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    return fixed

def _formatar_work_items_para_prompt(work_items: List[dict]) -> str:
    """Formats Work Item data as structured text for the LLM prompt."""
    if not work_items:
        return "(No Work Items linked to this PR)"
    secoes = []
    for wi in work_items:
        secao = [
            f"--- Work Item #{wi['id']}: {wi['type'].upper()} ---",
            f"Title:  {wi['title']}",
            f"State:  {wi['state']}",
        ]
        if wi.get("description"):
            secao += ["", "Description:", wi["description"]]
        if wi.get("acceptance_criteria"):
            secao += ["", "Acceptance Criteria:", wi["acceptance_criteria"]]
        if wi.get("tags"):
            secao += ["", f"Tags: {wi['tags']}"]
        if wi.get("comments"):
            secao += ["", "Team Comments:"]
            comentarios_ordenados = sorted(wi.get("comments", []), key=len, reverse=True)
            for c in comentarios_ordenados[:5]:
                secao.append(f"  • {c}")
        secoes.append("\n".join(secao))
    return "\n\n".join(secoes)


def _construir_prompt(pr_info: dict, work_items: List[dict],
                      regras_repo: str, mapa_ficheiros: Dict[str, str], injected_findings: list = None) -> str:
    """Builds the full requirements validation prompt."""
    wi_section = _formatar_work_items_para_prompt(work_items)
    regras_section = f"\n{regras_repo}" if regras_repo else "(No repository rules file found)"
    codigo_section = "\n".join([
        f"\n--- FILE: {path} ---\n{content}"
        for path, content in mapa_ficheiros.items()
    ]) if mapa_ficheiros else "(No code changes provided)"
    pr_desc = pr_info.get("description", "") or "(No PR description provided)"

    if injected_findings:
        findings_str = "\n".join([
            f"- [{f.get('severity', '').upper()}] File {f.get('file')}, Line {f.get('line')}: {f.get('title')} - {f.get('description')}"
            for f in injected_findings
        ])
        code_review_context = f"""
=== CODE REVIEW STATIC FINDINGS ===
The Code Review agent has already analyzed this PR and found the following issues.
CRITICAL: You MUST use these findings as source of truth for code quality Non-Functional Requirements.
For example, if the Code Review found "Unused imports", you CANNOT mark a "No Unused Imports" requirement as IMPLEMENTED.

{findings_str}
"""
    else:
        code_review_context = ""

    return f"""You are a Requirements Validation Agent for a software development team.
Your task is to determine whether the code changes in this Pull Request correctly
implement the business requirements defined in the linked Work Items.

=== WORK ITEMS LINKED TO THIS PR ===
(Tasks/User Stories from the team board. Acceptance Criteria is the primary source.)

{wi_section}

=== REPOSITORY BUSINESS RULES ===
(Standing rules that apply to ALL pull requests in this repository.)

{regras_section}
{code_review_context}
=== PR DESCRIPTION (fallback context) ===
PR Title: {pr_info.get('title', 'N/A')}
Author: {pr_info.get('author', 'N/A')}
Branch: {pr_info.get('source_branch', 'N/A')}

{pr_desc}

=== CHANGED CODE (with line numbers: "   N | code") ===

{codigo_section}

=== YOUR TASK ===

Step 1 — Extract ALL requirements from:
  1. Work Item Acceptance Criteria (PRIMARY source — each numbered AC is a SEPARATE requirement)
  2. Work Item Description (fallback if no Acceptance Criteria — parse ALL items from free text, numbered lists, bullet points, or meeting notes)
  3. Team Comments
  4. Repository Business Rules (if relevant to this change)
  CRITICAL: Extract EVERY individually identifiable requirement. If the AC contains 15 items (AC 1..AC 15) plus Non-Functional requirements (NF-1..NF-7), you MUST output ALL of them as separate entries. Do NOT summarize, skip, or group multiple items.
  Include both functional ACs and non-functional/quality requirements (e.g. code quality, logging, type hints, validation).
  Assign IDs: WI-{{id}}-AC-{{n}} for Acceptance Criteria, WI-{{id}}-NF-{{n}} for Non-Functional, RULE-{{n}} for repo rules.

Step 2 — For each requirement, assign ONE status:
  IMPLEMENTED   — clearly and correctly satisfied in the changed code
  PARTIAL       — partially addressed; something specific is missing
  MISSING       — not addressed at all in the changed code
  UNVERIFIABLE  — cannot be determined from static analysis alone
                  (runtime behaviour, external systems, timing constraints)
  STATUS ASSIGNMENT  — Be pragmatic. If a requirement is 90% implemented and functional, mark it as IMPLEMENTED even if small non-functional details (like type hints) are missing. Only use PARTIAL/MISSING for real functional gaps.
  CRITICAL: When evaluating IMPLEMENTED status, you MUST verify ALL code paths, not just the happy path. If a requirement says "all failures must be logged" and there is ANY code path where a failure is NOT logged, the status is PARTIAL.
  CRITICAL: When validating if an action/event is tracked or implemented, check the ENTIRE CALL GRAPH, not just inside a single helper method. It counts as IMPLEMENTED if other methods invoke the required action.
  - Mark any requirement from Acceptance Criteria as MUST (highest priority).
  - Mark requirements from Description as SHOULD.
  - Mark requirements from comments or repo rules as COULD.
  Add a "priority" field: "MUST"|"SHOULD"|"COULD" to each requirement.
  The overall_verdict must be NEEDS_WORK if ANY MUST requirement is MISSING or PARTIAL.

Step 3 — Overall verdict:
  APPROVED        — all verifiable requirements are IMPLEMENTED
  NEEDS_WORK      — at least one PARTIAL or MISSING requirement
  UNVERIFIABLE    — all requirements need runtime testing
  NO_REQUIREMENTS — no requirements found in any source

STRICT RULES:
- For evidence, provide the EXACT code snippet causing the problem (or showing the implementation).
- Provide the file name and line number in separate JSON fields. "evidence_code" MUST be an array of strings.
- For PARTIAL requirements, your `missing_detail` MUST include:
  1. Which specific function(s) or line(s) violate the requirement
  2. What specifically is missing or wrong
  3. A concrete fix example
  BAD: "Logging is not consistent across the code"
  GOOD: "export_user_data() uses print() on line 163 instead of log.info()."
- Do NOT comment on code quality or security — that is a separate agent.
- Do NOT invent requirements not in the sources above.
- If a Work Item has no Acceptance Criteria, say so explicitly.
- For UNVERIFIABLE, always provide a concrete testing hint.

TESTING EXPECTATIONS:
- If requirement says "implement feature X", code must have:
  • Unit tests (pytest, jest, etc.)
  • Integration tests if X interacts with external systems
  Missing tests → Mark as PARTIAL, not IMPLEMENTED
  
CONFLICT RESOLUTION:
- If 2 Work Items have contradicting requirements:
  • Reference the conflict explicitly in verdict_reason
  • Mark both as UNVERIFIABLE with explanation
  


Respond ONLY with valid JSON — no markdown, no extra text:
{{
  "work_items_analysed": [
    {{
      "id": <int>,
      "title": "<str>",
      "type": "<str>",
      "has_acceptance_criteria": <bool>,
      "priority": "MUST"|"SHOULD"|"COULD"
    }}
  ],
  "requirements": [
    {{
      "id": "<e.g. WI-42-AC-01 or RULE-01>",
      "work_item_id": <int or null>,
      "source": "acceptance_criteria"|"description"|"wi_comment"|"repository_rule"|"pr_description",
      "description": "<full requirement as stated>",
      "status": "IMPLEMENTED"|"PARTIAL"|"MISSING"|"UNVERIFIABLE",
      "evidence_file": "<file name>",
      "evidence_line": <int>,
      "evidence_code": ["line 1 of code", "line 2 of code"],
      "missing_detail": "<what is absent — null if IMPLEMENTED>",
      "manual_test_hint": "<how to test at runtime — null unless UNVERIFIABLE>"
    }}
  ],
  "overall_verdict": "APPROVED"|"NEEDS_WORK"|"UNVERIFIABLE"|"NO_REQUIREMENTS",
  "verdict_reason": "<one sentence>",
  "implementation_summary": "<2-3 sentences>"
}}"""



def _chamar_ia(prompt: str) -> Optional[dict]:
    """Sends the requirements validation prompt to Azure AI Foundry."""
    from pydantic import ValidationError

    headers = {"api-key": AZURE_API_KEY, "Content-Type": "application/json"}
    payload = {
        "model": AZURE_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise Requirements Validation Agent. "
                    "Respond ONLY in valid JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        start_time = time.time()
        try:
            resp = requests.post(AZURE_ENDPOINT, headers=headers, json=payload, timeout=TIMEOUT)
            resp.raise_for_status()

            data = resp.json()
            usage = data.get("usage", {})
            elapsed = time.time() - start_time
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)
            log.info(
                "[REQUIREMENTS] Done — %.2fs | %d tokens (%dP / %dC)",
                elapsed, p_tokens + c_tokens, p_tokens, c_tokens,
            )

            raw = data["choices"][0]["message"]["content"].strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                log.error("No JSON found in AI response")
                return None

            try:
                sanitized = _sanitize_json(raw[start:end + 1])
                parsed = json.loads(sanitized)
                resultado = RequirementsResult(**parsed)
            except (json.JSONDecodeError, ValidationError) as parse_err:
                log.error("Failed to parse/validate AI response: %s", parse_err)
                return None

            resultado_dict = resultado.model_dump()
            resultado_dict["_metrics"] = {"time": elapsed, "tokens": p_tokens + c_tokens}

            return resultado_dict

        except requests.RequestException as exc:
            if tentativa >= MAX_TENTATIVAS:
                log.error("Requirements AI failed after %d attempts: %s", MAX_TENTATIVAS, exc)
                return None

            espera = 0

            # 1. Respect the Retry-After header (REST best practice)
            if hasattr(exc, 'response') and exc.response is not None:
                if exc.response.status_code == 429:
                    retry_header = exc.response.headers.get("Retry-After")
                    if retry_header and retry_header.isdigit():
                        espera = int(retry_header)

            # 2. Exponential Backoff + Jitter
            if espera == 0:
                base_delay = 2 ** (tentativa + 1)
                jitter = random.uniform(0, base_delay * 0.5)
                espera = base_delay + jitter

            log.warning(
                "API failure (possible Rate Limit). Backoff: waiting %.2fs before attempt %d/%d...",
                espera, tentativa + 1, MAX_TENTATIVAS
            )
            time.sleep(espera)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# COMMENT FORMATTING
# ══════════════════════════════════════════════════════════════════════════════

STATUS_EMOJI = {
    "IMPLEMENTED":  "✅",
    "PARTIAL":      "⚠️",
    "MISSING":      "❌",
    "UNVERIFIABLE": "🔍",
}

STATUS_LABEL = {
    "IMPLEMENTED":  "Implemented",
    "PARTIAL":      "Partial",
    "MISSING":      "Missing",
    "UNVERIFIABLE": "Needs testing",
}

STATUS_ORDER = {"MISSING": 0, "PARTIAL": 1, "UNVERIFIABLE": 2, "IMPLEMENTED": 3}

SOURCE_LABEL = {
    "acceptance_criteria": "Acceptance Criteria",
    "description":         "WI Description",
    "wi_comment":          "WI Comment",
    "repository_rule":     "Repository Rule",
    "pr_description":      "PR Description",
}

PRIORITY_LABEL = {
    "MUST":   "Must",
    "SHOULD": "Should",
    "COULD":  "Could",
}


def _progress_bar(requisitos: List[dict]) -> str:
    """Renders a 10-step progress gauge for requirement completion."""
    total = len(requisitos)
    done  = sum(1 for r in requisitos if r.get("status") == "IMPLEMENTED")
    if total == 0:
        return "—"
    pct    = round(done / total * 100)
    filled = round(done / total * 10)
    bar    = f"`{'█' * filled}{'░' * (10 - filled)}`"
    return f"{bar}  **{done} / {total}**  ({pct}%)"


def formatar_comentario(resultado: dict, pr_info: dict, work_items: List[dict]) -> str:
    """Formats requirements validation results as a modern, minimalist Markdown PR comment."""


    requisitos  = resultado.get("requirements", [])
    veredicto   = resultado.get("overall_verdict", "UNVERIFIABLE")
    sumario     = resultado.get("implementation_summary", "")
    wi_info     = resultado.get("work_items_analysed", [])
    metrics     = resultado.get("_metrics", {})

    requisitos_ord = sorted(
        requisitos,
        key=lambda r: STATUS_ORDER.get(r.get("status", "MISSING"), 99),
    )

    # ── verdict ───────────────────────────────────────────────────────────────
    if veredicto == "APPROVED":
        verdict = "✅  Approved — all verifiable requirements are implemented."
    elif veredicto == "NO_REQUIREMENTS":
        verdict = "📭  No requirements found — link a Work Item with Acceptance Criteria to this PR."
    elif veredicto == "UNVERIFIABLE":
        verdict = "🔍  Manual review required — requirements need runtime verification."
    else:
        verdict = "⛔  Changes needed — one or more requirements are missing or incomplete."

    contagem = Counter(r.get("status") for r in requisitos)

    # ── header ────────────────────────────────────────────────────────────────
    lines = [
        "## 📋  Requirements Validation",
        "",
        f"> **ScopeReview AI**  ·  `{AZURE_MODEL}`  ·  Requirements analysis",
        "",
        "---",
        "",
        "| | |",
        "|:--|:--|",
        f"| **Verdict** | {verdict} |",
        f"| **Author** | {pr_info.get('author', '—')} |",
        f"| **Branch** | `{pr_info.get('source_branch', '—')}` |",
        f"| **Progress** | {_progress_bar(requisitos)} |",
        "",
        "| ✅ Implemented | ⚠️ Partial | ❌ Missing | 🔍 Needs Testing |",
        "|:--:|:--:|:--:|:--:|",
        f"| {contagem.get('IMPLEMENTED', 0)} | {contagem.get('PARTIAL', 0)} | {contagem.get('MISSING', 0)} | {contagem.get('UNVERIFIABLE', 0)} |",
        "",
    ]

    if sumario:
        lines += [f"> {sumario}", ""]

    lines += ["---", ""]

    # ── linked work items ─────────────────────────────────────────────────────
    if wi_info:
        lines += [
            "### Linked Work Items",
            "",
            "| ID | Type | Priority | Title | Acceptance Criteria |",
            "|--:|:--|:--:|:--|:--:|",
        ]
        for wi in wi_info:
            has_ac = "✅" if wi.get("has_acceptance_criteria") else "—"
            prio = PRIORITY_LABEL.get(wi.get("priority", ""), wi.get("priority", "—"))
            lines.append(
                f"| #{wi.get('id')} | {wi.get('type', '—')} | {prio}"
                f" | {wi.get('title', '')} | {has_ac} |"
            )
        lines += ["", "---", ""]

    # ── requirements table ────────────────────────────────────────────────────
    if not requisitos:
        lines += [
            "### Requirements",
            "",
            "No requirements could be extracted from the linked Work Items, "
            "repository rules file, or PR description.",
            "",
            "> **Next step** — Open the linked Work Item and add Acceptance Criteria. "
            "The agent will validate them on the next PR update.",
            "",
            "---",
            "",
        ]
    else:
        lines += [
            "### Requirements",
            "",
            "| ID | Priority | Source | Status | Requirement |",
            "|:--|:--:|:--|:--:|:--|",
        ]
        for r in requisitos_ord:
            rid    = r.get("id", "?")
            src    = SOURCE_LABEL.get(r.get("source", ""), r.get("source", ""))
            emoji  = STATUS_EMOJI.get(r.get("status", "MISSING"), "❌")
            prio   = PRIORITY_LABEL.get(r.get("priority", ""), r.get("priority", "—"))
            desc   = r.get("description", "")
            desc   = desc[:90] + "…" if len(desc) > 90 else desc
            lines.append(f"| `{rid}` | {prio} | {src} | {emoji} | {desc} |")

        lines += ["", "---", ""]

        # ── issues requiring attention ────────────────────────────────────────
        needs_work = [r for r in requisitos_ord if r.get("status") in ("MISSING", "PARTIAL")]
        if needs_work:
            lines += ["### ⚠️ Issues Requiring Attention", ""]
            for r in needs_work:
                rid    = r.get("id", "?")
                emoji  = STATUS_EMOJI.get(r.get("status", "MISSING"), "❌")
                status = STATUS_LABEL.get(r.get("status", ""), r.get("status", ""))

                # ── requirement header ────────────────────────────────────────
                ev_file = r.get("evidence_file", "unknown_file")
                ev_line = r.get("evidence_line", "?")
                
                lines += [
                    f"**{emoji} {rid}** · {status} · `{ev_file}:{ev_line}`",
                    "",
                ]

                # ── evidence (code snippet) ───────────────────────────────────
                ev_code = r.get("evidence_code", [])
                if ev_code:
                    code_str = "\n".join(ev_code) if isinstance(ev_code, list) else str(ev_code)
                    lines += [
                        "```python",
                        code_str,
                        "```",
                        "",
                    ]

                # ── brief explanation ─────────────────────────────────────────
                desc = r.get("description", "")
                if desc:
                    lines += [f"> {desc}", ""]

                # ── what is missing ──────────────────────────────────────────
                missing = r.get("missing_detail", "")
                if missing:
                    lines += [f"> {missing}", ""]

                lines += ["---", ""]


        # ── implemented ───────────────────────────────────────────────────────
        implemented = [r for r in requisitos_ord if r.get("status") == "IMPLEMENTED"]
        if implemented:
            lines += ["### ✅ Implemented", ""]
            for r in implemented:
                lines.append(f"- **`{r.get('id')}`** — {r.get('description', '')[:100]}")
            lines += ["", "---", ""]

        # ── manual testing ────────────────────────────────────────────────────
        unverifiable = [r for r in requisitos_ord if r.get("status") == "UNVERIFIABLE"]
        if unverifiable:
            lines += [
                "### 🧪 Manual Testing Required",
                "",
                "> The following requirements cannot be verified through static analysis alone.",
                "",
            ]
            for r in unverifiable:
                hint = r.get("manual_test_hint", "")
                lines += [f"**`{r.get('id')}`** — {r.get('description', '')}"]
                if hint:
                    lines += [f"> 💡 {hint}"]
                lines.append("")
            lines += ["---", ""]

    # ── footer ────────────────────────────────────────────────────────────────
    t   = metrics.get("time", 0)
    tok = metrics.get("tokens", 0)
    lines.append(f"<sub>⏱ {t:.1f}s · {tok:,} tokens · ScopeReview AI v1.0</sub>")


    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

async def _processar_pr(pr_id: int, repo_id: str, project: str) -> None:
    """Full requirements validation pipeline. Runs as a BackgroundTask."""
    log.info("=" * 60)
    log.info("[REQUIREMENTS] Starting PR #%s | Project: %s", pr_id, project)

    pr_info = obter_detalhes_pr(repo_id, pr_id, project)
    if not pr_info or not pr_info.get("commit_sha"):
        log.error("PR #%s: could not fetch PR details — aborting.", pr_id)
        return

    commit_sha = pr_info["commit_sha"]
    log.info("PR: '%s' | Author: %s", pr_info.get("title"), pr_info.get("author"))

    log.info("Fetching linked Work Items...")
    work_items = obter_todos_work_items(repo_id, pr_id, project)
    log.info("Work Items: %d", len(work_items))

    regras_repo = obter_regras_repositorio(repo_id, project, commit_sha)
    mapa_ficheiros = obter_ficheiros_alterados(repo_id, pr_id, project, commit_sha)
    log.info("Files: %d", len(mapa_ficheiros))

    log.info("Sending to %s...", AZURE_MODEL)
    prompt = _construir_prompt(pr_info, work_items, regras_repo, mapa_ficheiros)
    resultado = _chamar_ia(prompt)

    if resultado is None:
        publicar_comentario(
            repo_id, pr_id, project,
            "## ScopeReview AI | Requirements Validation Report\n\n"
            "WARNING: The AI analysis could not be completed. Check the service logs.",
        )
        return

    veredicto = resultado.get("overall_verdict", "?")
    n_reqs = len(resultado.get("requirements", []))
    n_pass = sum(1 for r in resultado.get("requirements", []) if r.get("status") == "IMPLEMENTED")
    log.info("[REQUIREMENTS] Verdict: %s | %d/%d implemented", veredicto, n_pass, n_reqs)

    publicar_comentario(repo_id, pr_id, project, formatar_comentario(resultado, pr_info, work_items))

def _processar_pr_sync(payload: dict, injected_findings: list = None) -> None:
    """Synchronous pipeline for requirements validation."""
    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId", 0)
    repo = resource.get("repository", {})
    repo_id = repo.get("id", "")
    project = repo.get("project", {}).get("name", "")

    log.info("=" * 60)
    log.info("[REQUIREMENTS] Starting PR #%s | Project: %s", pr_id, project)

    pr_info = obter_detalhes_pr(repo_id, pr_id, project)
    if not pr_info or not pr_info.get("commit_sha"):
        log.error("PR #%s: could not fetch PR details — aborting.", pr_id)
        return

    commit_sha = pr_info["commit_sha"]
    log.info("PR: '%s' | Author: %s", pr_info.get("title"), pr_info.get("author"))

    log.info("Fetching linked Work Items...")
    work_items = obter_todos_work_items(repo_id, pr_id, project)
    log.info("Work Items: %d", len(work_items))

    regras_repo = obter_regras_repositorio(repo_id, project, commit_sha)
    mapa_ficheiros = obter_ficheiros_alterados(repo_id, pr_id, project, commit_sha)
    log.info("Files: %d", len(mapa_ficheiros))

    log.info("Sending to %s...", AZURE_MODEL)
    prompt = _construir_prompt(pr_info, work_items, regras_repo, mapa_ficheiros, injected_findings)
    resultado = _chamar_ia(prompt)

    if resultado is None:
        publicar_comentario(
            repo_id, pr_id, project,
            "## ScopeReview AI | Requirements Validation Report\n\n"
            "WARNING: The AI analysis could not be completed. Check the service logs.",
        )
        return

    veredicto = resultado.get("overall_verdict", "?")
    n_reqs = len(resultado.get("requirements", []))
    n_pass = sum(1 for r in resultado.get("requirements", []) if r.get("status") == "IMPLEMENTED")
    log.info("[REQUIREMENTS] Verdict: %s | %d/%d implemented", veredicto, n_pass, n_reqs)

    publicar_comentario(repo_id, pr_id, project, formatar_comentario(resultado, pr_info, work_items))

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook/requirements")
async def webhook_requirements(request: Request, background_tasks: BackgroundTasks):
    """
    Receives PR events and triggers requirements validation.
    Separate path from /webhook so both agents can run on the same host.
    Configure a second Service Hook in Azure DevOps pointing to this path.
    """
    # Validate webhook secret if configured
    if WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            raise HTTPException(status_code=401, detail="Unauthorized webhook.")
        try:
            creds = base64.b64decode(auth[6:]).decode()
            password = creds.split(":", 1)[-1]
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized webhook.")
        if password != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized webhook.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload.")

    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId", 0)
    repo = resource.get("repository", {})
    repo_id = repo.get("id", "")
    project = repo.get("project", {}).get("name", "")

    log.info("[REQUIREMENTS] Webhook received — PR #%s | Project: %s", pr_id, project)

    if not all([pr_id, repo_id, project]):
        raise HTTPException(status_code=400, detail="Incomplete payload.")

    if shared_state.verificar_duplicado(pr_id, agent="requirements"):
        log.warning("[REQUIREMENTS] PR #%s already processed — ignoring retry.", pr_id)
        return {"status": "ignored"}

    background_tasks.add_task(_processar_pr, pr_id, repo_id, project)
    return {"status": "accepted", "agent": "requirements", "pr_id": pr_id}


@router.get("/health/requirements")
def health_requirements():
    """Health check for the Requirements Review Agent."""
    return {
        "status": "ok",
        "agent": "Requirements Review Agent",
        "model": AZURE_MODEL,
    }

