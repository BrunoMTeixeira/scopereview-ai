"""
ScopeReview AI · code_review_agent.py

Automated code review agent for Azure DevOps Pull Requests,
powered by Llama-3.3-70B-Instruct via Azure AI Foundry.

High-level flow:
  1. Azure DevOps fires a webhook when a Pull Request is created or updated
  2. The agent fetches the real content of changed files (with line numbers)
  3. Each file is split into logical blocks (functions/classes) and sent to the LLM
  4. The model returns structured JSON findings for each block
  5. All findings are aggregated, deduplicated and formatted as a Markdown comment
  6. The comment is published directly in the Pull Request

This file exposes an APIRouter (not a FastAPI app directly).
The router is mounted by main.py into the shared application instance.

Author: Bruno Teixeira — ISEP / DevScope — 2025/2026
"""

# ─── Imports ──────────────────────────────────────────────────────────────────
import json
import base64
import logging
import os
import time
import threading
import random  # Adicionado para o Jitter no Exponential Backoff
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ScopeReviewAI.CodeReview")


# ─── Configuration ────────────────────────────────────────────────────────────

def _obter_env_obrigatoria(chave: str) -> str:
    """Reads a required environment variable. Raises EnvironmentError if missing."""
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

MAX_FILES = int(os.getenv("MAX_FILES", "5"))
MAX_LINES = int(os.getenv("MAX_LINES", "400"))
MAX_TOKENS = 8000
TIMEOUT = 180
MAX_TENTATIVAS = 3
NUM_DIV = 50
DEDUP_SECONDS = 300

IGNORED_EXTENSIONS = {
    ".md", ".txt", ".json", ".lock", ".yaml", ".yml", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".ico", ".html", ".css", ".xml", ".toml", ".ini", ".cfg",
    ".env", ".gitignore",
}

LANG_MAP = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "cs": "csharp", "java": "java", "go": "go", "cpp": "cpp",
}

# ─── State ────────────────────────────────────────────────────────────────────
_prs_processados: Dict[int, float] = {}
_prs_lock = threading.Lock()

# ─── Router ───────────────────────────────────────────────────────────────────
router = APIRouter(tags=["Code Review"])


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AZURE DEVOPS
# ══════════════════════════════════════════════════════════════════════════════

def _ado_headers() -> dict:
    token = base64.b64encode(f":{ADO_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _linguagem(caminho: str) -> str:
    ext = caminho.rsplit(".", 1)[-1].lower() if "." in caminho else ""
    return LANG_MAP.get(ext, "")


def _verificar_duplicado(pr_id: int) -> bool:
    agora = time.time()
    with _prs_lock:
        expiradas = [k for k, v in _prs_processados.items() if agora - v > DEDUP_SECONDS]
        for k in expiradas:
            del _prs_processados[k]
        if (agora - _prs_processados.get(pr_id, 0)) < DEDUP_SECONDS:
            return True
        _prs_processados[pr_id] = agora
        return False


def obter_commit_head(repo_id: str, pr_id: int, project: str) -> str:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=20)
        resp.raise_for_status()
        return resp.json().get("lastMergeSourceCommit", {}).get("commitId", "")
    except requests.RequestException as exc:
        log.error("Failed to get HEAD commit: %s", exc)
        return ""


def obter_diff_ficheiro(repo_id: str, project: str, path: str, commit_sha: str) -> str:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/items"
        f"?path={path}&versionDescriptor.version={commit_sha}"
        f"&versionDescriptor.versionType=commit&api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=20)
        resp.raise_for_status()
        linhas = resp.text.splitlines()[:MAX_LINES]
        return "\n".join([f"{i + 1:>4} | {l}" for i, l in enumerate(linhas)])
    except requests.RequestException as exc:
        log.warning("Failed to read file %s: %s", path, exc)
        return ""


def obter_ficheiros_alterados(repo_id: str, pr_id: int, project: str, commit_sha: str) -> Dict[str, str]:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations?api-version=7.1"
    )
    mapa = {}
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=20)
        resp.raise_for_status()
        iter_id = resp.json()["value"][-1]["id"]

        url_changes = (
            f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations/{iter_id}/changes?api-version=7.1"
        )
        resp_changes = requests.get(url_changes, headers=_ado_headers(), timeout=20)
        changes = resp_changes.json().get("changeEntries", [])

        for change in changes[:MAX_FILES]:
            path = change.get("item", {}).get("path", "")
            if path and not any(path.lower().endswith(ext) for ext in IGNORED_EXTENSIONS):
                conteudo = obter_diff_ficheiro(repo_id, project, path, commit_sha)
                if conteudo:
                    mapa[path] = conteudo
        return mapa
    except requests.RequestException as exc:
        log.error("Failed to list changed files: %s", exc)
        return {}


def publicar_comentario(repo_id: str, pr_id: int, project: str, texto: str):
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    payload = {
        "comments": [{"content": texto, "parentCommentId": 0, "commentType": 1}],
        "status": 1,
    }
    try:
        requests.post(url, headers=_ado_headers(), json=payload, timeout=20).raise_for_status()
        log.info("Code review published in PR #%s", pr_id)
    except requests.RequestException as exc:
        log.error("Failed to publish code review: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def _dividir_em_blocos(conteudo: str) -> List[str]:
    linhas = conteudo.splitlines()
    return ["\n".join(linhas[i:i + NUM_DIV]) for i in range(0, len(linhas), NUM_DIV)]


def _analisar_bloco(caminho: str, bloco: str, tentativa: int = 1) -> Optional[dict]:
    start_time = time.time()
    headers = {"api-key": AZURE_API_KEY, "Content-Type": "application/json"}

    prompt = f"""Analyse this code block from '{caminho}'.
Respond ONLY in valid JSON.
STRICT RULES:
- You are a practical Senior Mentor, not a rigid auditor.
- Only report HIGH or CRITICAL for real, exploitable vulnerabilities (SQLi, Hardcoded Secrets, Critical Logic Bugs).
- Architectural improvements must be LOW or MEDIUM.
- If the code is functional and secure against common attacks, approve it.
- os.getenv() calls are ACCEPTABLE — do not flag as hardcoding.
- Parameterised queries using ? placeholders are CORRECT — do not flag as SQL Injection.
- log.info(), log.warning() ARE the logging module — never report as "print used".
- CRITICAL JSON RULE 1: For 'vulnerable_code' and 'fixed_code', NEVER use a multi-line string. You MUST output an ARRAY OF STRINGS (one string per line of code).
- CRITICAL JSON RULE 2: You MUST escape all double quotes inside your string values with a backslash.
- CRITICAL JSON RULE 3: NEVER leave trailing commas in your JSON object or arrays.
- Do NOT output markdown formatting (like ```json). Return raw JSON only.
- BE RELAXED: Do not be a "perfectionist". If the code follows standard secure patterns, it's fine.
- PRAGMATIC SECURITY: Do not demand advanced frameworks (like bcrypt or hmac) unless the current implementation is clearly broken or exposed. Standard library solutions (like hashlib) are acceptable.
- GRADING POLICY: Give a score >= 7 if the code is functional, readable, and lacks critical vulnerabilities. Only give < 7 if the code is genuinely dangerous or poorly written.
- IGNORE NITPICKS: Do not report "Magic Strings" or "Missing Constants" as High/Critical. Those are LOW quality issues at most.

JSON Format:
{{
  "findings": [
    {{
      "line": int,
      "type": "security"|"bug"|"quality",
      "severity": "critical"|"high"|"medium"|"low",
      "title": "str",
      "description": "str",
      "vulnerable_code": ["line 1 of code", "line 2 of code"],
      "recommendation": "str",
      "fixed_code": ["line 1 of fixed code", "line 2 of fixed code"]
    }}
  ],
  "positive_aspects": ["str"],
  "security_score": int,
  "approve": bool,
  "summary": "2-sentence technical summary"
}}

Code:
{bloco}"""

    payload = {
        "model": AZURE_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a Senior Mentor. Focus on real risks. Be concise. Respond only in JSON."
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
    }

    try:
        resp = requests.post(AZURE_ENDPOINT, headers=headers, json=payload, timeout=TIMEOUT)

        # Se for erro 429, o raise_for_status vai atirar a exceção que é apanhada abaixo
        resp.raise_for_status()

        data = resp.json()
        usage = data.get("usage", {})
        elapsed = time.time() - start_time
        p_tokens = usage.get("prompt_tokens", 0)
        c_tokens = usage.get("completion_tokens", 0)

        log.info("  [BLOCK] %.2fs | %dP / %dC tokens", elapsed, p_tokens, c_tokens)

        raw = data["choices"][0]["message"]["content"].strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return None

        try:
            res = json.loads(raw[start:end + 1], strict=False)
            res["_metrics"] = {"time": elapsed, "p_tokens": p_tokens, "c_tokens": c_tokens}
            return res
        except json.JSONDecodeError as json_err:
            log.error("Erro a ler o JSON da IA: %s", json_err)
            return None

    except requests.RequestException as exc:
        if tentativa < MAX_TENTATIVAS:
            espera = 0

            # 1. Tentar respeitar o cabeçalho 'Retry-After' (Boa prática REST)
            if hasattr(exc, 'response') and exc.response is not None:
                if exc.response.status_code == 429:
                    retry_header = exc.response.headers.get("Retry-After")
                    if retry_header and retry_header.isdigit():
                        espera = int(retry_header)

            # 2. Exponential Backoff + Jitter (Se não houver Retry-After ou for outro erro)
            if espera == 0:
                base_delay = 2 ** tentativa  # Tentativa 1: 2s | Tentativa 2: 4s | Tentativa 3: 8s
                jitter = random.uniform(0, 1) # Adiciona aleatoriedade
                espera = base_delay + jitter

            log.warning(
                "Falha na API (possível Rate Limit). Backoff ativo: a esperar %.2fs antes da tentativa %d/%d...",
                espera, tentativa + 1, MAX_TENTATIVAS
            )
            time.sleep(espera)
            return _analisar_bloco(caminho, bloco, tentativa + 1)

        log.error("AI call failed after %d attempts: %s", MAX_TENTATIVAS, exc)
        return None


def obter_revisao_ia(mapa: Dict[str, str]) -> Optional[dict]:
    all_f, all_p, scores, summaries = [], [], [], []
    total_time, total_p, total_c = 0, 0, 0

    for path, content in mapa.items():
        blocos = _dividir_em_blocos(content)
        log.info("Analysing '%s' — %d block(s)", path, len(blocos))
        for i, b in enumerate(blocos):
            log.info("  [BLOCK %d/%d]", i + 1, len(blocos))
            res = _analisar_bloco(path, b)
            if res:
                metrics = res.get("_metrics", {})
                total_time += metrics.get("time", 0)
                total_p += metrics.get("p_tokens", 0)
                total_c += metrics.get("c_tokens", 0)
                for f in res.get("findings", []):
                    f["file"] = path
                    all_f.append(f)
                all_p.extend(res.get("positive_aspects", []))
                if isinstance(res.get("security_score"), int):
                    scores.append(res["security_score"])
                if res.get("summary"):
                    summaries.append(res["summary"])

    if not scores and not all_f:
        return None

    vistos, unique_f = set(), []
    for f in all_f:
        key = (f.get("file"), f.get("line"), f.get("title", "")[:30])
        if key not in vistos:
            vistos.add(key)
            unique_f.append(f)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique_f.sort(key=lambda x: order.get(x.get("severity", "low"), 99))

    # 1. Calcular a nota final PRIMEIRO
    final_score = min(max(round(sum(scores) / len(scores)), 1), 10) if scores else 5

    # 2. Nova regra de "Justiça":
    sevs_list = [f.get("severity") for f in unique_f]
    num_high = sevs_list.count("high")
    has_critical = "critical" in sevs_list

    # 3. Só reprova se: Tem Crítico OU tem mais de 3 Highs OU nota < 7
    approved = not (has_critical or num_high > 3 or final_score < 7)

    clean_p, p_vistos = [], set()
    for p in sorted(set(all_p), key=len, reverse=True):
        if len(clean_p) >= 5:
            break
        pref = " ".join(p.lower().split()[:4])
        if pref not in p_vistos:
            p_vistos.add(pref)
            clean_p.append(p)

    log.info(
        "[CODE REVIEW SUMMARY] %.2fs | %d tokens | %d findings",
        total_time, total_p + total_c, len(unique_f),
    )

    return {
        "findings": unique_f,
        "positive_aspects": clean_p,
        "security_score": final_score,  # Passamos a usar a variável aqui
        "approve": approved,
        "executive_summary": summaries[0] if summaries else "Analysis completed.",
        "metrics": {"time": total_time, "tokens": total_p + total_c},
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMMENT FORMATTING
# ══════════════════════════════════════════════════════════════════════════════

def _barra_score(score: int) -> str:
    return f"`{'█' * score}{'░' * (10 - score)}` **{score}/10**"


def formatar_comentario(res: dict) -> str:
    score = res.get("security_score", 5)
    findings = res.get("findings", [])
    aprovado = res.get("approve", False)
    metrics = res.get("metrics", {})

    badge = "**PASSED** — *Ready for merge*" if aprovado else "**ACTION REQUIRED** — *Critical findings detected*"

    lines = [
        "## ScopeReview AI | Code Review Report",
        "---",
        "### Overview",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Security Score | {_barra_score(score)} |",
        f"| Recommendation | {badge} |",
        f"| Total Issues | {len(findings)} |",
        "",
        "### Executive Summary",
        "",
        f"> {res.get('executive_summary')}",
        "",
        "---",
    ]

    if findings:
        lines += [
            "### Findings Index", "",
            "| ID | Severity | Category | Location | Title |",
            "|:---|:---|:---|:---|:---|",
        ]
        for i, f in enumerate(findings, 1):
            lines.append(
                f"| {i:02d} | `{f.get('severity', '').upper()}` "
                f"| {f.get('type', '').upper()} "
                f"| `{f.get('file', '').split('/')[-1]}:{f.get('line', '-')}` "
                f"| {f.get('title')} |"
            )

        lines += ["", "---", "### Detailed Analysis", ""]
        for i, f in enumerate(findings, 1):
            lang = _linguagem(f.get("file", ""))

            # --- PROTEÇÃO DO ARRAY DE CÓDIGO ---
            vuln_code = f.get("vulnerable_code", "")
            if isinstance(vuln_code, list):
                vuln_code = "\n".join(vuln_code)

            fixed_code = f.get("fixed_code", "")
            if isinstance(fixed_code, list):
                fixed_code = "\n".join(fixed_code)

            lines += [
                f"#### {i:02d} | {f.get('severity', '').upper()}: {f.get('title')}",
                f"**Location:** `{f.get('file')}` (Line {f.get('line', '-')})",
                "",
                f"**Context & Impact** \n{f.get('description')}",
                "",
            ]
            if vuln_code:
                lines += [
                    f"**Vulnerable Implementation**",
                    f"```{lang}",
                    vuln_code.strip(),
                    "```", "",
                ]
            if f.get("recommendation"):
                lines += [f"**Remediation Strategy** \n{f.get('recommendation')}", ""]
            if fixed_code:
                lines += [
                    "**Suggested Correction**",
                    f"```{lang}",
                    fixed_code.strip(),
                    "```", "",
                ]
            lines += ["---"]
    else:
        lines += ["", "### No Issues Found", "", "The code passed all automated checks.", ""]

    if res.get("positive_aspects"):
        lines += ["### Positive Aspects", ""]
        lines += [f"- {p}" for p in res["positive_aspects"]]
        lines.append("")

    lines += [
        "---",
        f"*Generated by **{AZURE_MODEL}** via Azure AI Foundry "
        f"| {metrics.get('time', 0):.1f}s | {metrics.get('tokens', 0)} tokens*",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

async def _processar_pr(pr_id: int, repo_id: str, project: str) -> None:
    log.info("=" * 60)
    log.info("[CODE REVIEW] Starting PR #%s | Project: %s", pr_id, project)

    sha = obter_commit_head(repo_id, pr_id, project)
    if not sha:
        return

    mapa = obter_ficheiros_alterados(repo_id, pr_id, project, sha)
    if not mapa:
        log.warning("PR #%s: no relevant files found.", pr_id)
        return

    res = obter_revisao_ia(mapa)
    if res:
        publicar_comentario(repo_id, pr_id, project, formatar_comentario(res))
    else:
        log.error("Code review analysis failed for PR #%s", pr_id)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload.")

    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId", 0)
    repo = resource.get("repository", {})
    repo_id = repo.get("id", "")
    project = repo.get("project", {}).get("name", "")

    log.info("[CODE REVIEW] Webhook received — PR #%s | Project: %s", pr_id, project)

    if not all([pr_id, repo_id, project]):
        raise HTTPException(status_code=400, detail="Incomplete payload.")

    if _verificar_duplicado(pr_id):
        log.warning("[CODE REVIEW] PR #%s already processed — ignoring retry.", pr_id)
        return {"status": "ignored"}

    background_tasks.add_task(_processar_pr, pr_id, repo_id, project)
    return {"status": "accepted", "agent": "code_review", "pr_id": pr_id}


@router.get("/health/code-review")
def health():
    return {
        "status": "ok",
        "agent": "Code Review Agent",
        "model": AZURE_MODEL,
    }