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
import re
import time
import random
from collections import Counter
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Validate HTTPS for credential safety
if not AZURE_ENDPOINT.startswith("https://"):
    raise EnvironmentError("AZURE_ENDPOINT must use HTTPS to protect API keys in transit.")

MAX_FILES = int(os.getenv("MAX_FILES", "15"))
MAX_LINES = int(os.getenv("MAX_LINES", "400"))
MAX_TOKENS = 8000
TIMEOUT = 180
MAX_TENTATIVAS = 3
NUM_DIV = 50
ADO_REQUEST_TIMEOUT = 20
MAX_TOKEN_BUDGET = int(os.getenv("MAX_TOKEN_BUDGET", "50000"))
MAX_HIGH_BLOCK = int(os.getenv("MAX_HIGH_BLOCK", "3"))

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
import shared_state

# ─── Router ───────────────────────────────────────────────────────────────────
router = APIRouter(tags=["Code Review"])


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AZURE DEVOPS
# ══════════════════════════════════════════════════════════════════════════════

_ADO_AUTH_HEADER = f"Basic {base64.b64encode(f':{ADO_PAT}'.encode()).decode()}"


def _ado_headers() -> dict:
    return {"Authorization": _ADO_AUTH_HEADER, "Content-Type": "application/json"}


def _linguagem(caminho: str) -> str:
    ext = caminho.rsplit(".", 1)[-1].lower() if "." in caminho else ""
    return LANG_MAP.get(ext, "")



def obter_commit_head(repo_id: str, pr_id: int, project: str) -> str:
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
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
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
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
        resp = requests.get(url, headers=_ado_headers(), timeout=ADO_REQUEST_TIMEOUT)
        resp.raise_for_status()
        iter_id = resp.json()["value"][-1]["id"]

        url_changes = (
            f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations/{iter_id}/changes?api-version=7.1"
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
        requests.post(url, headers=_ado_headers(), json=payload, timeout=ADO_REQUEST_TIMEOUT).raise_for_status()
        log.info("Code review published in PR #%s", pr_id)
    except requests.RequestException as exc:
        log.error("Failed to publish code review: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — DETERMINISTIC STATIC CHECKS
# ══════════════════════════════════════════════════════════════════════════════

# Names that are commonly used without import — if found in code but not imports, flag them
_COMMON_STDLIB_NAMES = {
    "timedelta": "datetime", "defaultdict": "collections", "deque": "collections",
    "Path": "pathlib", "Enum": "enum", "dataclass": "dataclasses",
    "abstractmethod": "abc", "wraps": "functools", "partial": "functools",
}


def _static_checks(path: str, content: str) -> List[dict]:
    """Deterministic regex-based checks that catch bugs LLMs consistently miss."""
    findings = []
    lines = content.splitlines()
    raw_lines = [l.split("|", 1)[-1] if "|" in l else l for l in lines]  # strip line numbers
    raw_code = "\n".join(raw_lines)

    # ── 1. Unused imports ─────────────────────────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        stripped = raw_l.strip()
        # "import X" or "from X import Y, Z"
        m_import = re.match(r'^import\s+(\w+)', stripped)
        m_from = re.match(r'^from\s+\S+\s+import\s+(.+)', stripped)
        if m_import:
            name = m_import.group(1)
            # Check if name is used anywhere else in the code (excluding the import line itself)
            other_lines = raw_lines[:i] + raw_lines[i+1:]
            if not any(re.search(r'\b' + re.escape(name) + r'\b', ol) for ol in other_lines):
                findings.append({
                    "file": path, "line": i + 1, "type": "quality", "severity": "low",
                    "title": f"Unused import: {name}",
                    "description": f"Module `{name}` is imported but never used in this file.",
                    "vulnerable_code": [stripped],
                    "recommendation": f"Remove the unused import `{name}`.",
                    "fixed_code": [f"# import {name}  — removed (unused)"],
                    "_source": "static",
                })
        elif m_from:
            names = [n.strip().split(" as ")[-1].strip() for n in m_from.group(1).split(",")]
            for name in names:
                if not name or name == "*":
                    continue
                other_lines = raw_lines[:i] + raw_lines[i+1:]
                if not any(re.search(r'\b' + re.escape(name) + r'\b', ol) for ol in other_lines):
                    findings.append({
                        "file": path, "line": i + 1, "type": "quality", "severity": "low",
                        "title": f"Unused import: {name}",
                        "description": f"`{name}` is imported but never used in this file.",
                        "vulnerable_code": [stripped],
                        "recommendation": f"Remove the unused import `{name}`.",
                        "fixed_code": [],
                        "_source": "static",
                    })

    # ── 2. print() in production code ─────────────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        if re.search(r'\bprint\s*\(', raw_l.strip()) and not raw_l.strip().startswith("#"):
            findings.append({
                "file": path, "line": i + 1, "type": "quality", "severity": "medium",
                "title": "print() used instead of logging",
                "description": "Production code should use the `logging` module, not `print()`. "
                               "Print statements bypass log configuration and cannot be filtered.",
                "vulnerable_code": [raw_l.strip()],
                "recommendation": "Replace with `log.debug(...)` or `log.info(...)`.",
                "fixed_code": [raw_l.strip().replace("print(", "log.info(", 1)],
                "_source": "static",
            })

    # ── 3. Broad except clauses ───────────────────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        stripped = raw_l.strip()
        if re.match(r'^except\s*:', stripped) or re.match(r'^except\s+Exception\s*:', stripped):
            findings.append({
                "file": path, "line": i + 1, "type": "quality", "severity": "medium",
                "title": "Broad exception handler",
                "description": "Catching `Exception` or using a bare `except:` hides real errors. "
                               "Catch specific exceptions (e.g., `sqlite3.Error`, `ValueError`).",
                "vulnerable_code": [stripped],
                "recommendation": "Replace with specific exception types and log the error.",
                "fixed_code": [stripped.replace("Exception", "SpecificError as e") if "Exception" in stripped
                               else stripped.replace("except:", "except SpecificError as e:")],
                "_source": "static",
            })

    # ── 4. DEBUG logging in production ────────────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        if re.search(r'level\s*=\s*logging\.DEBUG', raw_l) or re.search(r'level\s*=\s*DEBUG', raw_l):
            findings.append({
                "file": path, "line": i + 1, "type": "quality", "severity": "medium",
                "title": "DEBUG logging level in production",
                "description": "Logging level is set to DEBUG, which generates excessive output "
                               "in production and may expose sensitive data.",
                "vulnerable_code": [raw_l.strip()],
                "recommendation": "Set to `logging.INFO` or higher for production.",
                "fixed_code": [raw_l.strip().replace("DEBUG", "INFO")],
                "_source": "static",
            })

    # ── 5. Missing imports (name used but never imported/defined) ─────────
    for name, module in _COMMON_STDLIB_NAMES.items():
        if re.search(r'\b' + re.escape(name) + r'\b', raw_code):
            # Check if it's actually imported
            if not re.search(r'import\s+.*\b' + re.escape(name) + r'\b', raw_code):
                # Find first usage line
                for i, raw_l in enumerate(raw_lines):
                    if re.search(r'\b' + re.escape(name) + r'\b', raw_l):
                        findings.append({
                            "file": path, "line": i + 1, "type": "bug", "severity": "high",
                            "title": f"NameError: `{name}` used but never imported",
                            "description": f"`{name}` is used but not imported. This will cause "
                                           f"a `NameError` at runtime. It should be imported from `{module}`.",
                            "vulnerable_code": [raw_l.strip()],
                            "recommendation": f"Add `from {module} import {name}` to the imports.",
                            "fixed_code": [f"from {module} import {name}"],
                            "_source": "static",
                        })
                        break

    # ── 6. Unreachable code (pass followed by return) ─────────────────────
    for i in range(len(raw_lines) - 1):
        curr = raw_lines[i].strip()
        nxt = raw_lines[i + 1].strip()
        if curr == "pass" and nxt.startswith("return "):
            findings.append({
                "file": path, "line": i + 2, "type": "bug", "severity": "medium",
                "title": "Unreachable code after `pass`",
                "description": "The `return` statement after `pass` may indicate dead code or a logic error. "
                               "The `pass` is redundant if followed by `return`.",
                "vulnerable_code": [curr, nxt],
                "recommendation": "Remove the `pass` statement or restructure the logic.",
                "fixed_code": [nxt],
                "_source": "static",
            })

    # ── 7. Rowcount check after UPDATE/DELETE ─────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        if re.search(r'cursor\.execute\s*\(\s*["\'](?:UPDATE|DELETE)', raw_l, re.I):
            lookahead = "\n".join(raw_lines[i+1:i+6])
            if "rowcount" not in lookahead and "commit()" in lookahead:
                findings.append({
                    "file": path, "line": i + 1, "type": "quality", "severity": "medium",
                    "title": "Missing rowcount check after UPDATE/DELETE",
                    "description": "Executing an UPDATE or DELETE without verifying if any rows were affected (cursor.rowcount).",
                    "vulnerable_code": [raw_l.strip()],
                    "recommendation": "Check if cursor.rowcount > 0 to ensure the operation actually affected rows.",
                    "fixed_code": [],
                    "_source": "static",
                })

    # ── 8. PII in log statements ──────────────────────────────────────────
    for i, raw_l in enumerate(raw_lines):
        if re.search(r'log\.(info|debug|warning|error|critical)\s*\(.*\b(email|password|token|ip_address|phone|nif|ssn)\b', raw_l, re.I):
            findings.append({
                "file": path, "line": i + 1, "type": "security", "severity": "medium",
                "title": "Possible PII exposed in logs",
                "description": "Logging sensitive data (PII) like email, passwords, IPs or tokens is a security risk.",
                "vulnerable_code": [raw_l.strip()],
                "recommendation": "Mask the sensitive information before logging, or remove it from the log message.",
                "fixed_code": [],
                "_source": "static",
            })

    # ── 9. Negative number guard for LIMIT/OFFSET ─────────────────────────
    for i, raw_l in enumerate(raw_lines):
        if re.search(r'LIMIT\s+\?', raw_l, re.I):
            lookback = "\n".join(raw_lines[max(0,i-10):i])
            if not re.search(r'if\s+.*limit.*[<>]|max\(|min\(', lookback, re.I):
                findings.append({
                    "file": path, "line": i + 1, "type": "security", "severity": "medium",
                    "title": "Unvalidated LIMIT parameter in SQL query",
                    "description": "Using a parameter for LIMIT without explicitly validating it against negative values or setting a maximum bound.",
                    "vulnerable_code": [raw_l.strip()],
                    "recommendation": "Ensure the limit variable is validated (e.g., limit = max(1, min(100, limit))).",
                    "fixed_code": [],
                    "_source": "static",
                })

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def _build_context_header(content: str) -> str:
    """Extract imports and class/function signatures as context for each block."""
    lines = content.splitlines()
    context = []
    for line in lines:
        stripped = line.strip()
        if (stripped.startswith("import ") or stripped.startswith("from ") or
            stripped.startswith("class ") or stripped.startswith("def ")):
            context.append(stripped)
    if not context:
        return ""
    return "# FILE CONTEXT (imports & signatures):\n" + "\n".join(context[:30]) + "\n\n"

def _sanitize_json(raw: str) -> str:
    """Fix common LLM JSON issues: invalid escape sequences and trailing commas."""
    # Fix invalid escape sequences (e.g. \d, \s, \w) — only valid JSON escapes are: \" \\ \/ \b \f \n \r \t \uXXXX
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
    # Remove trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    return fixed

def _dividir_em_blocos(conteudo: str) -> List[str]:
    padrao = r'\n(?=\s*\d+\s*\|\s*(?:def |class |async def |public |private |protected |static |function ))'
    fragmentos = re.split(padrao, conteudo)
    blocos, atual = [], ""
    for i, frag in enumerate(fragmentos):
        atual += frag
        if len(atual.splitlines()) >= NUM_DIV or i == len(fragmentos) - 1:
            if atual.strip():
                blocos.append(atual)
            atual = ""
    return blocos if blocos else [conteudo]


def _analisar_bloco(caminho: str, bloco: str) -> Optional[dict]:
    headers = {"api-key": AZURE_API_KEY, "Content-Type": "application/json"}

    prompt = f"""Analyse this code block from '{caminho}'. Respond ONLY in valid JSON.
RULES:
- Be a practical Senior Mentor. If the code is functional and secure, approve it.
- HIGH/CRITICAL only for real, exploitable vulnerabilities (SQLi, Hardcoded Secrets, Critical Logic Bugs, Runtime Crashes).
- Architectural improvements, Magic Strings, Missing Constants → LOW or MEDIUM at most.
- os.getenv() is ACCEPTABLE. Parameterised queries (? placeholders) are CORRECT. log.info()/log.warning() ARE the logging module.
- JSON RULES: 'vulnerable_code'/'fixed_code' MUST be arrays of strings (one per line). Escape double quotes. No trailing commas. No markdown fences.
MANDATORY CHECKS — verify EACH ONE:
1. PASSWORDS: Is the password hashed before DB comparison? If compared in plaintext → HIGH.
2. ERROR HANDLING: Broad `except Exception:` or bare `except:` that hides errors → MEDIUM.
3. INPUT VALIDATION: Missing checks for None, empty strings, negative numbers, wrong types → MEDIUM.
4. EDGE CASES: Login failures without user_id logged? Functions that silently fail without error info? → MEDIUM.
5. TYPE HINTS: Public methods without return type hints → LOW.
6. ATOMICITY: Bulk DB operations that mix queries and side-effects (e.g. log_action inside a for loop before commit) → MEDIUM.
ALSO CHECK (type="quality", severity="low"/"medium"):
- PERFORMANCE: O(n²) in hot paths, repeated DB calls
- MAINTAINABILITY: dead code, DRY violations > 5 lines
- TESTABILITY: untestable side effects, hidden dependencies

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
                "content": "You are a thorough Senior Code Reviewer. Analyse code for security flaws, bugs, missing error handling, and code quality issues. Report ALL issues you find. Respond only in valid JSON."
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

            log.info("  [BLOCK] %.2fs | %dP / %dC tokens", elapsed, p_tokens, c_tokens)

            raw = data["choices"][0]["message"]["content"].strip()

            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None

            try:
                sanitized = _sanitize_json(raw[start:end + 1])
                res = json.loads(sanitized)
                res["_metrics"] = {"time": elapsed, "p_tokens": p_tokens, "c_tokens": c_tokens}
                return res
            except json.JSONDecodeError as json_err:
                log.error("Failed to parse AI JSON response: %s", json_err)
                return None

        except requests.RequestException as exc:
            if tentativa >= MAX_TENTATIVAS:
                log.error("AI call failed after %d attempts: %s", MAX_TENTATIVAS, exc)
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


def obter_revisao_ia(mapa: Dict[str, str]) -> Optional[dict]:
    all_f, all_p, scores, summaries = [], [], [], []
    total_time, total_p, total_c = 0, 0, 0

    # ── Phase 1: Deterministic static checks (free, no tokens) ─────────────
    for path, content in mapa.items():
        static_findings = _static_checks(path, content)
        if static_findings:
            log.info("  [STATIC] '%s' — %d finding(s)", path, len(static_findings))
            all_f.extend(static_findings)

    # ── Phase 2: LLM analysis (per block) ─────────────────────────────────────
    for path, content in mapa.items():
        blocos = _dividir_em_blocos(content)
        context_header = _build_context_header(content)
        log.info("Analysing '%s' — %d block(s)", path, len(blocos))
        for i, b in enumerate(blocos):
            if total_p + total_c >= MAX_TOKEN_BUDGET:
                log.warning("Token budget exhausted (%d tokens). Stopping analysis.", total_p + total_c)
                break
            if i > 0:
                time.sleep(0.5)
            log.info("  [BLOCK %d/%d]", i + 1, len(blocos))
            res = _analisar_bloco(path, context_header + b)
            if res is None:
                log.warning("  [BLOCK %d/%d] could not be analysed — adding warning finding.", i + 1, len(blocos))
                all_f.append({
                    "file": path, "line": None, "type": "quality", "severity": "low",
                    "title": f"Block {i + 1}/{len(blocos)} could not be analysed",
                    "description": "This code block failed all AI inference attempts and was not reviewed.",
                    "vulnerable_code": [], "recommendation": "Review this block manually.",
                    "fixed_code": []
                })
                continue

            for f in res.get("findings", []):
                f["file"] = path
                all_f.append(f)
            all_p.extend(res.get("positive_aspects", []))
            if res.get("security_score") is not None:
                scores.append(res["security_score"])
            if res.get("summary"):
                summaries.append(res["summary"])
            m = res.get("_metrics", {})
            total_time += m.get("time", 0)
            total_p += m.get("p_tokens", 0)
            total_c += m.get("c_tokens", 0)

    if not scores and not all_f:
        return None

    # ── Phase 3: Deduplication and scoring ─────────────────────────────────────
    vistos, unique_f = set(), []
    
    # Prioritize static findings by processing them first
    sorted_all_f = sorted(all_f, key=lambda x: 0 if x.get("_source") == "static" else 1)
    
    for f in sorted_all_f:
        # Aggressive dedup: same file + same line ± 2 + same type = same finding
        line_group = f.get("line", 0) // 3 if f.get("line") else 0
        key = (f.get("file"), line_group, f.get("type"))
        if key not in vistos:
            vistos.add(key)
            unique_f.append(f)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    unique_f.sort(key=lambda x: order.get(x.get("severity", "low"), 99))

    # Deterministic score calculation based on findings (replaces LLM scoring)
    penalty = {
        "critical": 3.0,
        "high": 1.5,
        "medium": 0.5,
        "low": 0.15
    }
    total_penalty = sum(penalty.get(f.get("severity", "low"), 0) for f in unique_f)
    final_score = max(1, min(10, round(10 - total_penalty)))

    sevs_list = [f.get("severity") for f in unique_f]
    num_high = sevs_list.count("high")
    has_critical = "critical" in sevs_list

    # Verdict: CRITICALs always block. HIGHs block above configurable threshold.
    approved = not (has_critical or num_high >= MAX_HIGH_BLOCK or final_score < 7)

    clean_p, p_vistos = [], set()
    for p in sorted(set(all_p), key=len, reverse=True):
        if len(clean_p) >= 5:
            break
        pref = " ".join(str(p).lower().split()[:4]) if p else ""
        if pref not in p_vistos:
            p_vistos.add(pref)
            clean_p.append(p)

    log.info(
        "[CODE REVIEW SUMMARY] %.2fs | %d tokens | %d findings (static: %d, LLM: %d)",
        total_time, total_p + total_c, len(unique_f),
        sum(1 for f in unique_f if f.get("_source") == "static"),
        sum(1 for f in unique_f if f.get("_source") != "static"),
    )

    return {
        "findings": unique_f,
        "positive_aspects": clean_p,
        "security_score": final_score,
        "approve": approved,
        "executive_summary": summaries[0] if summaries else "Analysis completed.",
        "metrics": {"time": total_time, "tokens": total_p + total_c},
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMMENT FORMATTING
# ══════════════════════════════════════════════════════════════════════════════

_SEV_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🔵",
}

_SEV_LABEL = {
    "critical": "CRITICAL",
    "high":     "HIGH",
    "medium":   "MEDIUM",
    "low":      "LOW",
}

_TYPE_LABEL = {
    "security": "Security",
    "bug":      "Bug",
    "quality":  "Quality",
}


def _score_gauge(score: int) -> str:
    """Renders a clean 10-step gauge for the security score."""
    filled = "█" * score
    empty  = "░" * (10 - score)
    return f"`{filled}{empty}`  **{score} / 10**"


def formatar_comentario(res: dict) -> str:
    """Formats code review results as a modern, minimalist Markdown PR comment."""


    score    = res.get("security_score", 5)
    findings = res.get("findings", [])
    aprovado = res.get("approve", False)
    metrics  = res.get("metrics", {})

    sev_counts = Counter(f.get("severity", "low").lower() for f in findings)

    # ── verdict ───────────────────────────────────────────────────────────────
    if aprovado:
        verdict = "✅  Cleared for merge — no blocking issues detected."
    else:
        verdict = "⛔  Review required — one or more issues must be addressed before merging."

    # ── header ────────────────────────────────────────────────────────────────
    lines = [
        "## 🔍  Code Review",
        "",
        f"> **ScopeReview AI**  ·  `{AZURE_MODEL}`  ·  Automated static analysis",
        "",
        "---",
        "",
        "| | |",
        "|:--|:--|",
        f"| **Score** | {_score_gauge(score)} |",
        f"| **Verdict** | {verdict} |",
        "",
        "| 🔴 Critical | 🟠 High | 🟡 Medium | 🔵 Low |",
        "|:--:|:--:|:--:|:--:|",
        f"| {sev_counts.get('critical', 0)} | {sev_counts.get('high', 0)} | {sev_counts.get('medium', 0)} | {sev_counts.get('low', 0)} |",
        "",
    ]

    # ── executive summary ─────────────────────────────────────────────────────
    summary = res.get("executive_summary", "")
    if summary:
        lines += [f"> {summary}", ""]

    lines += ["---", ""]

    # ── findings ──────────────────────────────────────────────────────────────
    if findings:
        lines += ["### Findings", ""]

        for i, f in enumerate(findings, 1):
            lang = _linguagem(f.get("file", ""))
            sev_emoji = _SEV_EMOJI.get(f.get("severity", "low").lower(), "🔵")
            sev_label = _SEV_LABEL.get(f.get("severity", "low").lower(), "LOW")
            cat   = _TYPE_LABEL.get(f.get("type", ""), f.get("type", "").capitalize())
            title = f.get("title", "")
            file  = f.get("file", "").split("/")[-1]
            line_num = f.get("line", "—")
            loc = f"`{file}:{line_num}`" if line_num and line_num != "—" else f"`{file}`"

            vuln_code = f.get("vulnerable_code", "")
            if isinstance(vuln_code, list):
                vuln_code = "\n".join(vuln_code)

            fixed_code = f.get("fixed_code", "")
            if isinstance(fixed_code, list):
                fixed_code = "\n".join(fixed_code)

            # ── finding header ────────────────────────────────────────────────
            lines += [
                f"**{i} · {sev_emoji} {sev_label}** · {cat} · {loc}",
                "",
            ]

            # ── code where the issue is ───────────────────────────────────────
            if vuln_code and vuln_code.strip():
                lines += [
                    f"```{lang}",
                    vuln_code.strip(),
                    "```",
                    "",
                ]

            # ── brief justification ──────────────────────────────────────────
            desc = f.get("description", "")
            if desc:
                lines += [f"> {desc}", ""]

            # ── suggestion ────────────────────────────────────────────────────
            rec = f.get("recommendation", "")
            if rec:
                lines += [f"**💡 Suggestion** — {rec}", ""]

            if fixed_code and fixed_code.strip():
                lines += [
                    f"```{lang}",
                    fixed_code.strip(),
                    "```",
                    "",
                ]

            lines += ["---", ""]

    else:
        lines += [
            "### Findings",
            "",
            "No issues detected. The code passed all automated checks. ✅",
            "",
            "---",
            "",
        ]

    # ── positive aspects ──────────────────────────────────────────────────────
    positives = res.get("positive_aspects", [])
    if positives:
        lines += ["### ✨ Strengths", ""]
        for p in positives:
            lines.append(f"- {p}")
        lines += ["", "---", ""]

    # ── footer ────────────────────────────────────────────────────────────────
    t = metrics.get("time", 0)
    tok = metrics.get("tokens", 0)
    lines.append(f"<sub>⏱ {t:.1f}s · {tok:,} tokens · ScopeReview AI v1.0</sub>")

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

def _processar_pr_sync(payload: dict) -> list:
    """Synchronous pipeline that returns findings for orchestration."""
    resource = payload.get("resource", {})
    pr_id = resource.get("pullRequestId", 0)
    repo = resource.get("repository", {})
    repo_id = repo.get("id", "")
    project = repo.get("project", {}).get("name", "")

    log.info("=" * 60)
    log.info("[CODE REVIEW] Starting PR #%s | Project: %s", pr_id, project)

    sha = obter_commit_head(repo_id, pr_id, project)
    if not sha:
        return []

    mapa = obter_ficheiros_alterados(repo_id, pr_id, project, sha)
    if not mapa:
        log.warning("PR #%s: no relevant files found.", pr_id)
        return []

    res = obter_revisao_ia(mapa)
    if res:
        publicar_comentario(repo_id, pr_id, project, formatar_comentario(res))
        return res.get("findings", [])
    else:
        log.error("Code review analysis failed for PR #%s", pr_id)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
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

    log.info("[CODE REVIEW] Webhook received — PR #%s | Project: %s", pr_id, project)

    if not all([pr_id, repo_id, project]):
        raise HTTPException(status_code=400, detail="Incomplete payload.")

    if shared_state.verificar_duplicado(pr_id, agent="code_review"):
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