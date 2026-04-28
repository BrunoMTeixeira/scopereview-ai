from typing import List, Dict
from collections import Counter

# ─── CONSTANTS & LABELS ───────────────────────────────────────────────────────

STATUS_EMOJI = {"IMPLEMENTED": "✅", "PARTIAL": "⚠️", "MISSING": "❌", "UNVERIFIABLE": "🔍"}
STATUS_LABEL = {"IMPLEMENTED": "Implemented", "PARTIAL": "Partial", "MISSING": "Missing", "UNVERIFIABLE": "Needs testing"}
STATUS_ORDER = {"MISSING": 0, "PARTIAL": 1, "UNVERIFIABLE": 2, "IMPLEMENTED": 3}
SOURCE_LABEL = {
    "acceptance_criteria": "Acceptance Criteria",
    "description": "WI Description",
    "wi_comment": "WI Comment",
    "repository_rule": "Repository Rule",
    "pr_description": "PR Description",
}
PRIORITY_LABEL = {"MUST": "Must", "SHOULD": "Should", "COULD": "Could"}

LANG_MAP = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "cs": "csharp", "java": "java", "go": "go", "cpp": "cpp",
}

def _linguagem(caminho: str) -> str:
    ext = caminho.rsplit(".", 1)[-1].lower() if "." in caminho else ""
    return LANG_MAP.get(ext, "")


# ─── CODE REVIEW FORMATTER ────────────────────────────────────────────────────

def _score_gauge(score: int) -> str:
    filled = "█" * score
    empty = "░" * (10 - score)
    return f"`{filled}{empty}`  **{score} / 10**"

def format_code_review(res: dict, metrics: dict, *, model_display_name: str) -> str:
    findings = res.get("findings", [])
    if not findings:
        return "✅ **ScopeReview AI**: No security issues or bugs detected."

    budget_note = ""
    if metrics.get("token_budget_exceeded"):
        budget_note = " · ⚠️ token budget reached (some blocks were not sent to the LLM)"

    linhas_tabela = [
        "## 🔍 Code Review",
        f"> **ScopeReview AI**  ·  `{model_display_name}`  ·  Automated static analysis{budget_note}", "",
        "| | |", "|:--|:--|",
        f"| **Score** | {_score_gauge(res.get('security_score', 10))} |",
        f"| **Verdict** | {'✅  Approved — no critical/high issues found.' if res.get('approve') else '⛔  Review required — one or more issues must be addressed before merging.'} |",
        "",
        "| 🔴 Critical | 🟠 High | 🟡 Medium | 🔵 Low |",
        "|:--:|:--:|:--:|:--:|",
    ]

    count = Counter(f.get("severity", "low") for f in findings)
    linhas_tabela.append(f"| {count.get('critical', 0)} | {count.get('high', 0)} | {count.get('medium', 0)} | {count.get('low', 0)} |")

    lines = linhas_tabela + ["", "### Findings", ""]

    for idx, f in enumerate(findings, 1):
        sev = f.get("severity", "low")
        emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(sev, "🔵")
        tipo = f.get("type", "unknown").capitalize()
        lines += [f"**{idx} · {emoji} {sev.upper()} · {tipo} · `{f.get('file', '?')}:{f.get('line', '?')}`**", ""]

        titulo = f.get("title")
        if titulo: lines += [f"*{titulo}*", ""]

        vuln_code = "\n".join(f.get("vulnerable_code", [])) if isinstance(f.get("vulnerable_code"), list) else f.get("vulnerable_code", "")
        fixed_lines = f.get("fixed_code") or f.get("suggestion_code") or []
        if isinstance(fixed_lines, list):
            fixed_code = "\n".join(fixed_lines)
        else:
            fixed_code = str(fixed_lines or "")

        lang = _linguagem(f.get("file", ""))

        if vuln_code or fixed_code or f.get("description"):
            if vuln_code:
                lines += [f"```{lang}", vuln_code, "```"]

            justification = f.get("justification") or f.get("description", "")
            if justification:
                lines += [f"> **Justification:** {justification}"]

            recommendation = f.get("recommendation", "")
            if recommendation:
                lines += [f"> **Suggestion:** {recommendation}"]

            if fixed_code:
                lines += ["", "**Fix snippet:**", f"```{lang}", fixed_code, "```"]
            lines += ["", "---", ""]

    strengths = res.get("positive_aspects", [])
    if strengths:
        lines += ["---", "", "### ✨ Strengths", ""]
        for s in set(strengths):
            lines.append(f"- {s}")
        lines += [""]

    time_val = metrics.get('time', 0)
    tokens_val = metrics.get('tokens', 0)
    lines += ["---", "", f"<sub>⏱ {time_val}s · {tokens_val:,} tokens · ScopeReview AI v2.0</sub>"]
    return "\n".join(lines)


# ─── REQUIREMENTS VALIDATION FORMATTER ────────────────────────────────────────

def _progress_bar(requisitos: List[dict]) -> str:
    total = len(requisitos)
    done = sum(1 for r in requisitos if r.get("status") == "IMPLEMENTED")
    if total == 0: return "—"
    pct = round(done / total * 100)
    filled = round(done / total * 10)
    bar = f"`{'█' * filled}{'░' * (10 - filled)}`"
    return f"{bar}  **{done} / {total}**  ({pct}%)"

def format_requirements_review(
    resultado: dict,
    pr_info: dict,
    metrics: dict,
    *,
    model_display_name: str,
) -> str:
    requisitos = resultado.get("requirements", [])
    veredicto = resultado.get("overall_verdict", "UNVERIFIABLE")
    sumario = resultado.get("implementation_summary", "")
    wi_info = resultado.get("work_items_analysed", [])

    requisitos_ord = sorted(requisitos, key=lambda r: STATUS_ORDER.get(r.get("status", "MISSING"), 99))

    if veredicto == "APPROVED":
        verdict = "✅  Approved — all verifiable requirements are implemented."
    elif veredicto == "NO_REQUIREMENTS":
        verdict = "📭  No requirements found — link a Work Item with Acceptance Criteria to this PR."
    elif veredicto == "UNVERIFIABLE":
        verdict = "🔍  Manual review required — requirements need runtime verification."
    else:
        verdict = "⛔  Changes needed — one or more requirements are missing or incomplete."

    contagem = Counter(r.get("status") for r in requisitos)

    lines = [
        "## 📋  Requirements Validation", "",
        f"> **ScopeReview AI**  ·  `{model_display_name}`  ·  Requirements analysis", "",
        "---", "",
        "| | |", "|:--|:--|",
        f"| **Verdict** | {verdict} |",
        f"| **Author** | {pr_info.get('author', '—')} |",
        f"| **Progress** | {_progress_bar(requisitos)} |", "",
        "| ✅ Implemented | ⚠️ Partial | ❌ Missing | 🔍 Needs Testing |", "|:--:|:--:|:--:|:--:|",
        f"| {contagem.get('IMPLEMENTED', 0)} | {contagem.get('PARTIAL', 0)} | {contagem.get('MISSING', 0)} | {contagem.get('UNVERIFIABLE', 0)} |", "",
    ]

    if sumario: lines += [f"> {sumario}", ""]
    lines += ["---", ""]

    if wi_info:
        lines += ["### Linked Work Items", "", "| ID | Type | Priority | Title | Acceptance Criteria |", "|--:|:--|:--:|:--|:--:|"]
        for wi in wi_info:
            has_ac = "✅" if wi.get("has_acceptance_criteria") else "—"
            prio = PRIORITY_LABEL.get(wi.get("priority") or "", wi.get("priority") or "—")
            lines.append(f"| #{wi.get('id')} | {wi.get('type', '—')} | {prio} | {wi.get('title', '')} | {has_ac} |")
        lines += ["", "---", ""]

    if not requisitos:
        lines += ["### Requirements", "", "No requirements could be extracted.", ""]
    else:
        lines += ["### Requirements", "", "| ID | Priority | Source | Status | Requirement |", "|:--|:--:|:--|:--:|:--|"]
        for r in requisitos_ord:
            rid = r.get("id", "?")
            src = SOURCE_LABEL.get(r.get("source", ""), r.get("source", ""))
            emoji = STATUS_EMOJI.get(r.get("status", "MISSING"), "❌")
            prio = PRIORITY_LABEL.get(r.get("priority") or "", r.get("priority") or "—")
            desc = r.get("description", "")
            desc = desc[:90] + "…" if len(desc) > 90 else desc
            lines.append(f"| `{rid}` | {prio} | {src} | {emoji} | {desc} |")
        lines += ["", "---", ""]

        needs_work = [r for r in requisitos_ord if r.get("status") in ("MISSING", "PARTIAL")]
        if needs_work:
            lines += ["### ⚠️ Issues Requiring Attention", ""]
            for r in needs_work:
                rid = r.get("id", "?")
                emoji = STATUS_EMOJI.get(r.get("status", "MISSING"), "❌")
                status = STATUS_LABEL.get(r.get("status", ""), r.get("status", ""))
                ev_file = r.get("evidence_file", "unknown_file")
                ev_line = r.get("evidence_line", "?")
                lang = _linguagem(ev_file)

                lines += [f"**{emoji} {rid}** · {status} · `{ev_file}:{ev_line}`", ""]
                ev_code = r.get("evidence_code", [])
                if ev_code:
                    code_str = "\n".join(ev_code) if isinstance(ev_code, list) else str(ev_code)
                    lines += [f"```{lang}", code_str, "```", ""]

                desc = r.get("description", "")
                if desc: lines += [f"> {desc}", ""]

                missing = r.get("missing_detail", "")
                if missing: lines += [f"> **Missing Detail:** {missing}", ""]

                lines += ["---", ""]

        implemented = [r for r in requisitos_ord if r.get("status") == "IMPLEMENTED"]
        if implemented:
            lines += ["### ✅ Implemented", ""]
            for r in implemented:
                rid = r.get("id", "?")
                desc = r.get("description", "").replace('\n', ' ').strip()
                desc = desc[:115] + "…" if len(desc) > 115 else desc
                lines.append(f"- **`{rid}`** — {desc}")
            lines += [""]

    time_val = metrics.get('time', 0)
    tokens_val = metrics.get('tokens', 0)
    lines += ["---", "", f"<sub>⏱ {time_val}s · {tokens_val:,} tokens · ScopeReview AI v2.0</sub>"]
    return "\n".join(lines)
