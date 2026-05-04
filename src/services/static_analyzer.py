import re
from typing import List

_COMMON_STDLIB_NAMES = {
    "timedelta": "datetime", "defaultdict": "collections", "deque": "collections",
    "Path": "pathlib", "Enum": "enum", "dataclass": "dataclasses",
    "abstractmethod": "abc", "wraps": "functools", "partial": "functools",
}

class StaticAnalyzer:
    """Deterministic regex-based checks that catch bugs LLMs consistently miss."""
    
    @staticmethod
    def analyze_file(path: str, content: str) -> List[dict]:
        findings = []
        lines = content.splitlines()
        raw_lines = [l.split("|", 1)[-1] if "|" in l else l for l in lines]  # strip line numbers
        raw_code = "\n".join(raw_lines)

        # ── 1. Unused imports ─────────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            stripped = raw_l.strip()
            m_import = re.match(r'^import\s+(\w+)', stripped)
            m_from = re.match(r'^from\s+\S+\s+import\s+(.+)', stripped)
            if m_import:
                name = m_import.group(1)
                other_lines = raw_lines[:i] + raw_lines[i+1:]
                if not any(re.search(r'\b' + re.escape(name) + r'\b', ol) for ol in other_lines):
                    findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "low", 
                        f"Unused import: {name}", f"Module `{name}` is imported but never used.", [stripped]))
            elif m_from:
                names = [n.strip().split(" as ")[-1].strip() for n in m_from.group(1).split(",")]
                for name in names:
                    if not name or name == "*":
                        continue
                    other_lines = raw_lines[:i] + raw_lines[i+1:]
                    if not any(re.search(r'\b' + re.escape(name) + r'\b', ol) for ol in other_lines):
                        findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "low", 
                            f"Unused import: {name}", f"`{name}` is imported but never used.", [stripped]))

        # ── 2. print() in production code ─────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'\bprint\s*\(', raw_l.strip()) and not raw_l.strip().startswith("#"):
                findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "medium", 
                    "print() used instead of logging", "Production code should use the `logging` module, not `print()`.", [raw_l.strip()]))

        # ── 3. Broad except clauses ───────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            stripped = raw_l.strip()
            if re.match(r'^except\s*:', stripped) or re.match(r'^except\s+Exception\s*:', stripped):
                findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "medium", 
                    "Broad exception handler", "Catching `Exception` or using a bare `except:` hides real errors.", [stripped]))

        # ── 4. DEBUG logging in production ────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'level\s*=\s*logging\.DEBUG', raw_l) or re.search(r'level\s*=\s*DEBUG', raw_l):
                findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "medium", 
                    "DEBUG logging level in production", "Logging level is set to DEBUG, which generates excessive output.", [raw_l.strip()]))

        # ── 5. Missing imports (NameError) ────────────────────────────────────
        for name, module in _COMMON_STDLIB_NAMES.items():
            if re.search(r'\b' + re.escape(name) + r'\b', raw_code):
                if not re.search(r'import\s+.*\b' + re.escape(name) + r'\b', raw_code):
                    for i, raw_l in enumerate(raw_lines):
                        if re.search(r'\b' + re.escape(name) + r'\b', raw_l):
                            findings.append(StaticAnalyzer._build_finding(path, i+1, "bug", "high", 
                                f"NameError: `{name}` used but never imported", f"`{name}` is used but not imported.", [raw_l.strip()]))
                            break

        # ── 6. Unreachable code ───────────────────────────────────────────────
        for i in range(len(raw_lines) - 1):
            curr = raw_lines[i].strip()
            nxt = raw_lines[i + 1].strip()
            if curr == "pass" and nxt.startswith("return "):
                findings.append(StaticAnalyzer._build_finding(path, i+2, "bug", "medium", 
                    "Unreachable code after `pass`", "The `return` statement after `pass` is dead code.", [curr, nxt]))

        # ── 7. Rowcount check ─────────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'cursor\.execute\s*\(\s*["\'](?:UPDATE|DELETE)', raw_l, re.I):
                lookahead = "\n".join(raw_lines[i+1:i+6])
                if "rowcount" not in lookahead and "commit()" in lookahead:
                    findings.append(StaticAnalyzer._build_finding(path, i+1, "quality", "medium", 
                        "Missing rowcount check after UPDATE/DELETE", "Executing UPDATE/DELETE without verifying affected rows.", [raw_l.strip()]))

        # ── 8. PII in log statements ──────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'log\.(info|debug|warning|error|critical)\s*\(.*\b(email|password|token|ip_address|phone|nif|ssn|iban|credit_card|cvv|address)\b', raw_l, re.I):
                findings.append(StaticAnalyzer._build_finding(path, i+1, "security", "medium", 
                    "Possible PII exposed in logs", "Logging sensitive data (PII) is a security and compliance risk.", [raw_l.strip()]))

        # ── 9. Negative number guard for LIMIT/OFFSET ─────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'LIMIT\s+\?', raw_l, re.I):
                lookback = "\n".join(raw_lines[max(0,i-10):i])
                if not re.search(r'if\s+.*limit.*[<>]|max\(|min\(', lookback, re.I):
                    findings.append(StaticAnalyzer._build_finding(path, i+1, "security", "medium", 
                        "Unvalidated LIMIT parameter in SQL query", "Using LIMIT parameter without checking negative boundaries.", [raw_l.strip()]))

        # ── 10. Hardcoded Secrets ─────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            # Detects assignment of long strings to sensitive-looking variable names
            if re.search(r'\b(secret|api_key|password|auth_token|access_key|private_key)\b\s*[:=]\s*["\'][a-zA-Z0-9_\-\.]{16,}["\']', raw_l, re.I):
                if not re.search(r'config|settings|env|os\.environ|dotenv', raw_l, re.I):
                    findings.append(StaticAnalyzer._build_finding(path, i+1, "security", "critical", 
                        "Hardcoded secret detected", "Secrets should be loaded from environment variables or a key vault, never hardcoded.", [raw_l.strip()]))

        # ── 11. SQL Injection Risk ────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            # Detects string formatting/interpolation inside cursor.execute calls
            if re.search(r'cursor\.execute\s*\(.*(f["\']|%|\.format\()', raw_l, re.I):
                if any(k in raw_l.upper() for k in ("SELECT", "UPDATE", "DELETE", "INSERT")):
                    findings.append(StaticAnalyzer._build_finding(path, i+1, "security", "critical", 
                        "Possible SQL Injection", "Use parameterized queries (?, %s) instead of string formatting.", [raw_l.strip()]))

        return findings

    @staticmethod
    def _build_finding(path: str, line: int, typ: str, severity: str, title: str, desc: str, code: List[str]) -> dict:
        return {
            "file": path, "line": line, "type": typ, "severity": severity,
            "title": title, "description": desc, "vulnerable_code": code,
            "suggestion_code": [], "justification": desc, "_source": "static",
        }
