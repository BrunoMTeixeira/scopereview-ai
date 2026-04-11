"""
poc-code-review · agent.py
Agente de revisão automática de Pull Requests no Azure DevOps,
suportado pelo modelo Phi-4-mini-instruct via Azure AI Foundry.
"""

import json
import base64
import logging
import os
import time
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
import requests

# ─── CONFIGURAÇÃO INICIAL ────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── VARIÁVEIS DE AMBIENTE ───────────────────────────────────────────────────
AZURE_ENDPOINT   = os.getenv("AZURE_ENDPOINT")
AZURE_MODEL      = os.getenv("AZURE_MODEL", "Phi-4-mini-instruct")
AZURE_API_KEY    = os.getenv("AZURE_API_KEY")
ADO_ORGANIZATION = os.getenv("ADO_ORGANIZATION")
ADO_PAT          = os.getenv("ADO_PAT")

# ─── CONSTANTES DE CONFIGURAÇÃO ─────────────────────────────────────────────
MAX_FILES      = 5       # número máximo de ficheiros analisados por PR
MAX_LINES      = 300     # número máximo de linhas por ficheiro
MAX_TOKENS     = 4000    # limite de tokens na resposta da IA
DEDUP_SECONDS  = 300     # janela de deduplicação (5 minutos)

IGNORED_EXTENSIONS = {
    ".md", ".txt", ".json", ".lock", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".html", ".css", ".xml", ".toml", ".ini", ".cfg",
}

_SEVERITY_EMOJI = {
    "crítica": "🔴",
    "alta":    "🟠",
    "média":   "🟡",
    "baixa":   "🟢",
}

_TYPE_LABEL = {
    "segurança": "SEGURANÇA",
    "bug":       "BUG",
    "qualidade": "QUALIDADE",
    "melhoria":  "MELHORIA",
}

_LANG_MAP = {
    "py":   "python",
    "js":   "javascript",
    "ts":   "typescript",
    "cs":   "csharp",
    "java": "java",
    "go":   "go",
    "rb":   "ruby",
    "php":  "php",
    "cpp":  "cpp",
}

# ─── APLICAÇÃO ───────────────────────────────────────────────────────────────
app = FastAPI(title="poc-code-review", version="1.0.0")

# Registo em memória para deduplicação de PRs
_processed_prs: dict[int, float] = {}


# ════════════════════════════════════════════════════════════════════════════
# AZURE DEVOPS — helpers
# ════════════════════════════════════════════════════════════════════════════

def _ado_headers() -> dict:
    """Constrói os headers de autenticação para a REST API do Azure DevOps."""
    token = base64.b64encode(f":{ADO_PAT}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type":  "application/json",
    }


def _should_ignore(path: str) -> bool:
    """Determina se um ficheiro deve ser ignorado com base na extensão."""
    return any(path.lower().endswith(ext) for ext in IGNORED_EXTENSIONS)


def _lang(ficheiro: str) -> str:
    """Devolve o identificador de linguagem para blocos de código Markdown."""
    ext = ficheiro.rsplit(".", 1)[-1].lower() if "." in ficheiro else ""
    return _LANG_MAP.get(ext, "")


def get_pr_head_commit(repo_id: str, pr_id: int, project: str) -> str:
    """
    Obtém o SHA do commit mais recente da branch de origem do PR.
    Necessário para aceder ao conteúdo actual dos ficheiros.
    """
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}"
        f"?api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=15)
        resp.raise_for_status()
        commit_sha = (
            resp.json()
            .get("lastMergeSourceCommit", {})
            .get("commitId", "")
        )
        log.info("HEAD commit: %s", commit_sha[:8] if commit_sha else "não encontrado")
        return commit_sha
    except requests.RequestException as exc:
        log.error("Erro ao obter HEAD commit: %s", exc)
        return ""


def get_file_content(repo_id: str, project: str, path: str, commit_sha: str) -> str:
    """
    Obtém o conteúdo de um ficheiro num commit específico,
    numerado por linha para referenciação exacta pela IA.
    """
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/items"
        f"?path={path}"
        f"&versionDescriptor.version={commit_sha}"
        f"&versionDescriptor.versionType=commit"
        f"&api-version=7.1"
    )
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=15)
        resp.raise_for_status()
        lines     = resp.text.splitlines()
        truncated = lines[:MAX_LINES]
        numbered  = [f"{i+1:>4} | {line}" for i, line in enumerate(truncated)]
        if len(lines) > MAX_LINES:
            numbered.append(f"     | ... ({len(lines) - MAX_LINES} linhas omitidas)")
        return "\n".join(numbered)
    except requests.RequestException as exc:
        log.warning("Não foi possível obter %s: %s", path, exc)
        return ""


def get_diff(repo_id: str, pr_id: int, project: str) -> str:
    """
    Constrói o contexto de revisão: lista os ficheiros alterados no PR
    e obtém o conteúdo actual de cada um, numerado por linha.
    """
    base_url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}"
    )

    commit_sha = get_pr_head_commit(repo_id, pr_id, project)
    if not commit_sha:
        return ""

    try:
        resp = requests.get(
            f"{base_url}/iterations?api-version=7.1",
            headers=_ado_headers(),
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Erro ao obter iterações do PR: %s", exc)
        return ""

    iterations = resp.json().get("value", [])
    if not iterations:
        log.warning("PR #%s sem iterações.", pr_id)
        return ""

    iteration_id = iterations[-1]["id"]

    try:
        resp2 = requests.get(
            f"{base_url}/iterations/{iteration_id}/changes?api-version=7.1",
            headers=_ado_headers(),
            timeout=15,
        )
        resp2.raise_for_status()
    except requests.RequestException as exc:
        log.error("Erro ao obter ficheiros alterados: %s", exc)
        return ""

    changes = resp2.json().get("changeEntries", [])
    if not changes:
        return "Sem alterações detectadas."

    diff_parts  = []
    files_added = 0

    for change in changes:
        if files_added >= MAX_FILES:
            remaining = len(changes) - files_added
            diff_parts.append(
                f"\n[{remaining} ficheiro(s) adicional(is) omitido(s) — limite atingido]"
            )
            break

        path        = change.get("item", {}).get("path", "")
        change_type = change.get("changeType", "edit").upper()

        if not path or _should_ignore(path):
            log.info("Ignorado: %s", path)
            continue

        log.info("A processar: %s", path)
        content = get_file_content(repo_id, project, path, commit_sha)

        section  = f"\n{'=' * 60}\n"
        section += f"FICHEIRO: {path}  [{change_type}]\n"
        section += f"{'=' * 60}\n"
        section += content or "(conteúdo não disponível)\n"

        diff_parts.append(section)
        files_added += 1

    return "\n".join(diff_parts) if diff_parts else "Todos os ficheiros foram ignorados."


def publicar_comentario(repo_id: str, pr_id: int, project: str, texto: str) -> bool:
    """Publica um comentário de revisão no Pull Request via REST API."""
    url = (
        f"https://dev.azure.com/{ADO_ORGANIZATION}/{project}"
        f"/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/threads?api-version=7.1"
    )
    payload = {
        "comments": [
            {"parentCommentId": 0, "content": texto, "commentType": 1}
        ],
        "status": 1,
    }
    try:
        resp = requests.post(url, headers=_ado_headers(), json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Comentário publicado no PR #%s.", pr_id)
        return True
    except requests.RequestException as exc:
        log.error("Erro ao publicar comentário: %s", exc)
        return False


# ════════════════════════════════════════════════════════════════════════════
# AZURE AI FOUNDRY — prompt e análise
# ════════════════════════════════════════════════════════════════════════════

def _construir_prompt(diff: str) -> str:
    """
    Constrói o prompt enviado ao modelo de linguagem.
    O modelo recebe o conteúdo real dos ficheiros com números de linha.
    """
    return f"""És um revisor de código sénior com experiência em segurança e qualidade de software.
Vais receber o conteúdo de ficheiros alterados num Pull Request, com números de linha no formato "   N | código".

IMPORTANTE: Analisa cada ficheiro COMPLETO, do início ao fim. Verifica CADA função individualmente.

Categorias a verificar obrigatoriamente:

SEGURANÇA:
- SQL Injection: concatenação de strings em queries SQL
- Credenciais hardcoded: passwords, API keys, tokens em variáveis
- Command injection com input externo
- Recursos não fechados (ficheiros, conexões) fora de context manager

BUGS:
- Variável usada antes de ser inicializada
- Divisão por zero sem validação
- bare except ou except Exception: pass
- Ficheiro aberto com open() fora de bloco with

QUALIDADE:
- print() usado para logging em vez do módulo logging
- Paths hardcoded no sistema de ficheiros
- Nomes de variáveis não descritivos

Responde APENAS com JSON válido, sem markdown nem texto adicional:
{{
  "comentarios": [
    {{
      "linha": <número inteiro exacto, ou null>,
      "ficheiro": "<path exacto do ficheiro>",
      "tipo": "segurança" | "bug" | "qualidade" | "melhoria",
      "severidade": "crítica" | "alta" | "média" | "baixa",
      "titulo": "<título curto, máx 60 caracteres>",
      "problema": "<explicação técnica do problema e impacto>",
      "codigo_problematico": "<trecho exacto do ficheiro, 1-3 linhas>",
      "sugestao": "<como corrigir e porquê>",
      "codigo_corrigido": "<código correcto, 1-6 linhas>"
    }}
  ],
  "resumo": "<avaliação técnica honesta em 3-4 frases>",
  "pontos_positivos": ["<aspectos bem implementados>"],
  "score_seguranca": <inteiro 0-10>,
  "aprovacao_recomendada": <true | false>
}}

Código a rever:
{diff}"""


def get_ai_review(diff: str) -> dict | None:
    """
    Envia o contexto de revisão ao modelo Phi-4-mini-instruct
    via Azure AI Foundry e devolve o resultado em dicionário.
    """
    headers = {
        "api-key":      AZURE_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "model": AZURE_MODEL,
        "messages": [
            {
                "role":    "system",
                "content": (
                    "És um revisor de código sénior especializado em segurança. "
                    "Analisas apenas o código fornecido, referenciando ficheiros e linhas exactas. "
                    "Nunca inventas ficheiros ou linhas que não existam. "
                    "Respondes sempre em JSON válido sem markdown."
                ),
            },
            {
                "role":    "user",
                "content": _construir_prompt(diff),
            },
        ],
        "temperature": 0.1,
        "max_tokens":  MAX_TOKENS,
    }

    try:
        resp = requests.post(
            AZURE_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Erro na chamada ao Azure AI Foundry: %s", exc)
        return None

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # Remove blocos de markdown se o modelo os incluir
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        log.error("Resposta da IA não é JSON válido: %s", exc)
        return None


# ════════════════════════════════════════════════════════════════════════════
# FORMATAÇÃO DO COMENTÁRIO MARKDOWN
# ════════════════════════════════════════════════════════════════════════════

def _formatar_comentario(resultado: dict) -> str:
    """Converte o JSON de análise num comentário Markdown legível para o PR."""
    score       = resultado.get("score_seguranca", "N/A")
    resumo      = resultado.get("resumo", "Sem resumo disponível.")
    comentarios = resultado.get("comentarios", [])
    positivos   = resultado.get("pontos_positivos", [])
    aprovacao   = resultado.get("aprovacao_recomendada", None)

    if isinstance(score, int):
        score_str = f"{score}/10 `{'█' * score}{'░' * (10 - score)}`"
    else:
        score_str = f"{score}/10"

    if aprovacao is True:
        aprovacao_str = "Aprovação recomendada"
    elif aprovacao is False:
        aprovacao_str = "Não recomendado — existem problemas críticos"
    else:
        aprovacao_str = "Rever manualmente"

    lines = [
        "## Code Review Automático",
        "",
        "| | |",
        "|---|---|",
        f"| **Score de Segurança** | {score_str} |",
        f"| **Recomendação** | {aprovacao_str} |",
        "",
        f"**Resumo:** {resumo}",
    ]

    if positivos:
        lines += ["", "### Pontos Positivos", ""]
        lines += [f"- {p}" for p in positivos]

    if comentarios:
        ordem = {"crítica": 0, "alta": 1, "média": 2, "baixa": 3}
        comentarios = sorted(
            comentarios,
            key=lambda c: ordem.get(c.get("severidade", "baixa"), 99),
        )

        contagem = {}
        for c in comentarios:
            sev = c.get("severidade", "?")
            contagem[sev] = contagem.get(sev, 0) + 1

        resumo_contagem = "  ·  ".join(
            f"{_SEVERITY_EMOJI.get(s, '⚪')} {n} {s}"
            for s, n in contagem.items()
        )

        lines += [
            "",
            f"### 🔍 Problemas Encontrados — {len(comentarios)} total",
            "",
            f"> {resumo_contagem}",
            "",
        ]

        for i, c in enumerate(comentarios, 1):
            sev      = c.get("severidade", "baixa")
            emoji    = _SEVERITY_EMOJI.get(sev, "⚪")
            tipo     = _TYPE_LABEL.get(c.get("tipo", ""), c.get("tipo", "").upper())
            titulo   = c.get("titulo", "Problema detectado")
            ficheiro = c.get("ficheiro", "")
            linha    = c.get("linha")
            lang     = _lang(ficheiro)

            loc = (
                f"`{ficheiro}` linha **{linha}**" if linha
                else f"`{ficheiro}`" if ficheiro
                else "geral"
            )

            lines += [
                "---",
                f"#### {emoji} #{i} · {titulo}",
                "",
                f"**Localização:** {loc}",
                f"**Tipo:** `{tipo}` &nbsp;|&nbsp; **Severidade:** `{sev.upper()}`",
                "",
                "**🔎 Problema:**",
                f"> {c.get('problema', '')}",
                "",
            ]

            if codigo_mau := c.get("codigo_problematico", "").strip():
                lines += [
                    "**Código problemático:**",
                    f"```{lang}",
                    codigo_mau,
                    "```",
                    "",
                ]

            if sugestao := c.get("sugestao", "").strip():
                lines += [f"**💡 Sugestão:** {sugestao}", ""]

            if codigo_bom := c.get("codigo_corrigido", "").strip():
                lines += [
                    "**Código corrigido:**",
                    f"```{lang}",
                    codigo_bom,
                    "```",
                    "",
                ]
    else:
        lines += [
            "",
            "### Nenhum problema encontrado",
            "",
            "O código passou em todas as verificações automáticas.",
        ]

    lines += [
        "---",
        f"*Gerado por **{AZURE_MODEL}** via Azure AI Foundry · poc-code-review v1.0*",
    ]

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@app.post("/webhook")
async def webhook(request: Request):
    """
    Recebe eventos de Pull Request do Azure DevOps (git.pullrequest.created).
    Extrai o código alterado, analisa com o modelo de IA e publica os
    comentários de revisão directamente no Pull Request.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido.")

    resource = payload.get("resource", {})
    pr_id    = resource.get("pullRequestId", 0)
    repo     = resource.get("repository", {})
    repo_id  = repo.get("id", "")
    project  = repo.get("project", {}).get("name", "")

    log.info("=" * 60)
    log.info("PR #%s | Projecto: %s", pr_id, project)

    if not all([pr_id, repo_id, project]):
        log.warning("Payload incompleto — a ignorar.")
        return {"status": "ignorado", "motivo": "payload incompleto"}

    # Deduplicação — o Azure DevOps faz retry automático em caso de timeout
    agora  = time.time()
    ultimo = _processed_prs.get(pr_id, 0)
    if agora - ultimo < DEDUP_SECONDS:
        restante = int(DEDUP_SECONDS - (agora - ultimo))
        log.warning("PR #%s já processado — ignorado (retry ADO). Aguardar %ds.", pr_id, restante)
        return {"status": "ignorado", "motivo": f"deduplicação: aguardar {restante}s"}

    _processed_prs[pr_id] = agora

    # 1 — Extrair conteúdo dos ficheiros alterados
    log.info("A extrair ficheiros alterados...")
    diff = get_diff(repo_id, pr_id, project)

    if not diff:
        log.warning("Diff vazio para PR #%s.", pr_id)
        return {"status": "sem_diff", "pr_id": pr_id}

    log.info("Contexto extraído: %d caracteres.", len(diff))

    # 2 — Análise pela IA
    log.info("A enviar para o modelo %s...", AZURE_MODEL)
    resultado = get_ai_review(diff)

    if resultado is None:
        publicar_comentario(
            repo_id, pr_id, project,
            "## Revisão Automática\n\n"
            "Não foi possível obter a análise da IA. Consulta os logs do agente.",
        )
        return {"status": "erro_ia", "pr_id": pr_id}

    # 3 — Publicar resultado no PR
    score       = resultado.get("score_seguranca", "N/A")
    num_coments = len(resultado.get("comentarios", []))
    aprovacao   = resultado.get("aprovacao_recomendada", "?")

    log.info("Score: %s/10 | Problemas: %s | Aprovação: %s", score, num_coments, aprovacao)

    publicar_comentario(repo_id, pr_id, project, _formatar_comentario(resultado))

    return {
        "status":      "sucesso",
        "pr_id":       pr_id,
        "score":       score,
        "comentarios": num_coments,
        "aprovacao":   aprovacao,
    }


@app.get("/health")
def health():
    """Endpoint de verificação de estado do agente."""
    return {
        "status":  "ok",
        "agente":  "poc-code-review",
        "modelo":  AZURE_MODEL,
        "versao":  "1.0.0",
    }
