import re
from typing import List

_COMMON_STDLIB_NAMES = {
    "timedelta": "datetime",
    "defaultdict": "collections",
    "deque": "collections",
    "Path": "pathlib",
    "Enum": "enum",
    "dataclass": "dataclasses",
    "abstractmethod": "abc",
    "wraps": "functools",
    "partial": "functools",
}

_POLYGLOT_PATTERNS = [
    {
        # code_only=True: strip comments and docstrings before matching — prevents false positives from
        # lines like `# avoid eval`, `# NO EVAL!`, or docstrings describing rules.
        # func_call_only=True: strip string literals to avoid flagging mentions inside messages/errors.
        "pattern": r"\b(eval|exec|system|popen|shell_exec)\b\s*\(",
        "type": "security",
        "severity": "critical",
        "title": "Dangerous Function (Dynamic Execution)",
        "desc": "Risk of Remote Code Execution (RCE). Avoid running strings as code.",
        "flags": re.IGNORECASE,
        "code_only": True,
        "func_call_only": True,
    },
    {
        "pattern": r"\b(strcpy|strcat|gets|sprintf|vsprintf)\b\s*\(",
        "type": "security",
        "severity": "high",
        "title": "Unsafe Memory Function (C/C++)",
        "desc": "Prone to Buffer Overflow. Use safe bounds-checking equivalents (strncpy, snprintf).",
        "flags": re.IGNORECASE,
        "code_only": True,
        "func_call_only": True,
    },
    {
        # code_only=True: `verify=False` in a comment (e.g. `# don't use verify=False`) is NOT a bug.
        "pattern": r"(verify\s*=\s*False|ssl_verify\s*=\s*false|check_hostname\s*=\s*False|TrustAllCerts|ALLOW_ALL_HOSTNAME_VERIFIER)",
        "type": "security",
        "severity": "critical",
        "title": "Disabled SSL/TLS Verification",
        "desc": "Enables Man-in-the-Middle (MitM) attacks by trusting any certificate.",
        "flags": re.IGNORECASE,
        "code_only": True,
    },
    {
        # code_only=True: weak algo names in comments/docs (e.g. `# migrated from MD5`) are not bugs.
        "pattern": r"\b(md5|sha1|des|rc4)\b",
        "type": "security",
        "severity": "medium",
        "title": "Weak Cryptographic Algorithm",
        "desc": "Vulnerable to collision or brute-force attacks. Use SHA256 or AES256.",
        "flags": re.IGNORECASE,
        "code_only": True,
    },
    {
        "pattern": r"catch\s*\([^)]*Exception[^)]*\)\s*\{\s*\}",
        "type": "quality",
        "severity": "medium",
        "title": "Empty Catch Block (Java/JS/C#)",
        "desc": "Generic catch without logic hides failure modes and simplifies corruption.",
        "flags": 0,
        "code_only": True,
    },
    {
        # code_only=False: a hardcoded JWT token in a comment is still a credential leak.
        "pattern": r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.",
        "type": "security",
        "severity": "critical",
        "title": "Hardcoded JWT Token Detected",
        "desc": "Embedded authentication token discovered in plaintext code.",
        "flags": 0,
        "code_only": False,
    },
    {
        "pattern": r"Access-Control-Allow-Origin\s*[:=]\s*['\"]?\*['\"]?",
        "type": "security",
        "severity": "high",
        "title": "Wildcard CORS Permitted",
        "desc": "Exposes internal resources to any requesting web domain.",
        "flags": re.IGNORECASE,
        "code_only": True,
    },
    {
        # code_only=False: TODO/FIXME live specifically inside comments — never strip them.
        "pattern": r"\b(TODO|FIXME|HACK|XXX)\b",
        "type": "quality",
        "severity": "low",
        "title": "Technical Debt / Temporary Placeholder",
        "desc": "Code contains TODO/FIXME comments that should be addressed before PR merge.",
        "flags": 0,
        "code_only": False,
    },
]


class StaticAnalyzer:
    """Deterministic regex-based static analysis engine (Shift-Left Phase 1).

    13 rules organized in 3 categories:
    - Python-specific (rules 1-6): import analysis, print detection, broad exceptions,
      DEBUG logging, missing imports (NameError), unreachable code.
    - SQL / Data (rules 7-9, 11): rowcount checks, PII in logs, LIMIT validation,
      SQL injection via string concatenation.
    - Polyglot universal (rules 10, 12-13): hardcoded secrets, method stubs,
      and language-agnostic security patterns (eval, strcpy, SSL bypass, etc.).

    Design decision: rules are co-located in a single method for auditability.
    Each rule is self-contained and independent — no shared state between rules.
    The polyglot patterns (rule 13) use a declarative list (_POLYGLOT_PATTERNS)
    for easy extension without modifying the analysis loop.
    """

    @staticmethod
    def analyze_file(path: str, content: str) -> List[dict]:
        """Run all 13 static analysis rules against a single file's diff content."""
        findings = []
        lines = content.splitlines()
        # ── Pre-processing ────────────────────────────────────────────────────

        def _extract_and_neutralize(l: str) -> str:
            content = l.split("|", 1)[-1] if "|" in l else l
            # If the line starts with minus (diff deletion), return empty to preserve indexing but ignore content
            if content.lstrip().startswith("-"):
                return ""
            return content

        line_numbers = []
        for idx, l in enumerate(lines):
            if "|" in l:
                match = re.match(r"^[+\-\s]*(\d+)\s*\|", l)
                if match:
                    line_numbers.append(int(match.group(1)))
                else:
                    line_numbers.append(idx + 1)
            else:
                line_numbers.append(idx + 1)

        raw_lines = [_extract_and_neutralize(l) for l in lines]
        raw_code = "\n".join(raw_lines)

        # ── 1. Unused imports ─────────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            # STRIP DIFF MARKERS (+/-) TO PREVENT BLIND SPOTS
            stripped = re.sub(r"^[+\-]+", "", raw_l.strip()).strip()
            m_import = re.match(r"^import\s+(\w+)", stripped)
            m_from = re.match(r"^from\s+\S+\s+import\s+(.+)", stripped)
            if m_import:
                name = m_import.group(1)
                other_lines = raw_lines[:i] + raw_lines[i + 1 :]
                if not any(re.search(r"\b" + re.escape(name) + r"\b", ol) for ol in other_lines):
                    findings.append(
                        StaticAnalyzer._build_finding(
                            path,
                            line_numbers[i],
                            "quality",
                            "low",
                            f"Unused import: {name}",
                            f"Module `{name}` is imported but never used.",
                            [stripped],
                        )
                    )
            elif m_from:
                names = [n.strip().split(" as ")[-1].strip() for n in m_from.group(1).split(",")]
                for name in names:
                    if not name or name == "*":
                        continue
                    other_lines = raw_lines[:i] + raw_lines[i + 1 :]
                    if not any(re.search(r"\b" + re.escape(name) + r"\b", ol) for ol in other_lines):
                        findings.append(
                            StaticAnalyzer._build_finding(
                                path,
                                line_numbers[i],
                                "quality",
                                "low",
                                f"Unused import: {name}",
                                f"`{name}` is imported but never used.",
                                [stripped],
                            )
                        )

        # ── 2. print() in production code ─────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r"\bprint\s*\(", raw_l.strip()) and not raw_l.strip().startswith("#"):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "quality",
                        "medium",
                        "print() used instead of logging",
                        "Production code should use the `logging` module, not `print()`.",
                        [raw_l.strip()],
                    )
                )

        # ── 3. Broad except clauses ───────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            stripped = re.sub(r"^[+\-]+", "", raw_l.strip()).strip()
            if re.match(r"^except\s*:", stripped) or re.match(r"^except\s+Exception\s*:", stripped):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "quality",
                        "medium",
                        "Broad exception handler",
                        "Catching `Exception` or using a bare `except:` hides real errors.",
                        [stripped],
                    )
                )

        # ── 4. DEBUG logging in production ────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r"level\s*=\s*logging\.DEBUG", raw_l) or re.search(r"level\s*=\s*DEBUG", raw_l):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "quality",
                        "medium",
                        "DEBUG logging level in production",
                        "Logging level is set to DEBUG, which generates excessive output.",
                        [raw_l.strip()],
                    )
                )

        # ── 5. Missing imports (NameError) ────────────────────────────────────
        for name, module in _COMMON_STDLIB_NAMES.items():
            # Require a code-usage pattern — Name(...), Name.attr, or Name[...] —
            # to avoid false positives where the class name appears inside a string
            # literal, comment, or docstring (e.g. "Anti-Path Traversal" contains
            # the word "Path" but is not a code reference to pathlib.Path).
            usage_pattern = r"\b" + re.escape(name) + r"\s*[(.[]"
            if re.search(usage_pattern, raw_code):
                if not re.search(r"import\s+.*\b" + re.escape(name) + r"\b", raw_code):
                    for i, raw_l in enumerate(raw_lines):
                        # Skip comment-only lines and empty/deleted lines
                        stripped = re.sub(r"^[+\-]+", "", raw_l.strip()).strip()
                        if stripped.startswith("#") or not stripped:
                            continue
                        if re.search(usage_pattern, raw_l):
                            findings.append(
                                StaticAnalyzer._build_finding(
                                    path,
                                    line_numbers[i],
                                    "bug",
                                    "high",
                                    f"NameError: `{name}` used but never imported",
                                    f"`{name}` is used but not imported. Add: from {module} import {name}",
                                    [stripped],
                                )
                            )
                            break

        # ── 6. Unreachable code ───────────────────────────────────────────────
        for i in range(len(raw_lines) - 1):
            curr = re.sub(r"^[+\-]+", "", raw_lines[i].strip()).strip()
            nxt = re.sub(r"^[+\-]+", "", raw_lines[i + 1].strip()).strip()
            if curr == "pass" and nxt.startswith("return "):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i + 1] if i + 1 < len(line_numbers) else line_numbers[i] + 1,
                        "bug",
                        "medium",
                        "Unreachable code after `pass`",
                        "The `return` statement after `pass` is dead code.",
                        [curr, nxt],
                    )
                )

        # ── 7. Rowcount check ─────────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r'cursor\.execute\s*\(\s*["\'](?:UPDATE|DELETE)', raw_l, re.I):
                lookahead = "\n".join(raw_lines[i + 1 : i + 6])
                if "rowcount" not in lookahead and "commit()" in lookahead:
                    findings.append(
                        StaticAnalyzer._build_finding(
                            path,
                            line_numbers[i],
                            "quality",
                            "medium",
                            "Missing rowcount check after UPDATE/DELETE",
                            "Executing UPDATE/DELETE without verifying affected rows.",
                            [raw_l.strip()],
                        )
                    )

        # ── 8. PII in log statements ──────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(
                r"log\.(info|debug|warning|error|critical)\s*\(.*\b(email|password|token|ip_address|phone|nif|ssn|iban|credit_card|cvv|address)\b",
                raw_l,
                re.I,
            ):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "security",
                        "medium",
                        "Possible PII exposed in logs",
                        "Logging sensitive data (PII) is a security and compliance risk.",
                        [raw_l.strip()],
                    )
                )

        # ── 9. Negative number guard for LIMIT/OFFSET ─────────────────────────
        for i, raw_l in enumerate(raw_lines):
            if re.search(r"LIMIT\s+\?", raw_l, re.I):
                lookback = "\n".join(raw_lines[max(0, i - 10) : i])
                if not re.search(r"if\s+.*limit.*[<>]|max\(|min\(", lookback, re.I):
                    findings.append(
                        StaticAnalyzer._build_finding(
                            path,
                            line_numbers[i],
                            "security",
                            "medium",
                            "Unvalidated LIMIT parameter in SQL query",
                            "Using LIMIT parameter without checking negative boundaries.",
                            [raw_l.strip()],
                        )
                    )

        # ── 10. Hardcoded Secrets ─────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            # Detects assignment of long strings to sensitive-looking variable names
            if re.search(
                r'\b(secret|api_key|password|auth_token|access_key|private_key)\b\s*[:=]\s*["\'][a-zA-Z0-9_\-\.]{16,}["\']',
                raw_l,
                re.I,
            ):
                if not re.search(r"config|settings|env|os\.environ|dotenv", raw_l, re.I):
                    findings.append(
                        StaticAnalyzer._build_finding(
                            path,
                            line_numbers[i],
                            "security",
                            "critical",
                            "Hardcoded secret detected",
                            "Secrets should be loaded from environment variables or a key vault, never hardcoded.",
                            [raw_l.strip()],
                        )
                    )

        # ── 11. SQL Injection Risk ────────────────────────────────────────────
        for i, raw_l in enumerate(raw_lines):
            # Path A: cursor.execute() called with an f-string or .format() — UNSAFE.
            # NOTE: `%` is intentionally excluded here. `%s` inside a SQL string is
            # a DB-API parameterized placeholder (SAFE). Only `f"..."` and `.format()`
            # construct the query via string interpolation and are truly dangerous.
            # The old-style `cursor.execute("query" % var)` format operator is caught
            # separately below (Path C).
            has_exec_concat = re.search(r'cursor\.execute\s*\(.*(f["\']|\.format\()', raw_l, re.I)

            # Path B: a SQL query variable built via string concatenation, e.g.:
            #   sql = "SELECT * FROM " + table_name
            #   cursor.execute(sql)
            has_var_concat = False
            clean_l = re.sub(r"^[+\-]+", "", raw_l.strip()).strip()
            # Skip comment lines entirely
            if not clean_l.startswith("#") and not clean_l.startswith("//"):
                if any(k in clean_l.upper() for k in ("SELECT", "UPDATE", "DELETE", "INSERT")):
                    if "+" in clean_l and ('"' in clean_l or "'" in clean_l):
                        # Only flag if there's an explicit SQL variable being assembled
                        if re.search(r"\b(sql|query|cmd|command)\b\s*[+]?=", clean_l, re.I):
                            has_var_concat = True

            # Path C: old-style Python `%` format operator applied to a SQL string
            # e.g. cursor.execute("SELECT ... WHERE id=%s" % user_id)  ← UNSAFE
            # Distinguished from safe %s placeholders because the % is OUTSIDE the string.
            has_format_op = bool(
                re.search(r'cursor\.execute\s*\(\s*(?:f?["\'][^"\']*["\'])\s*%\s*\w', raw_l, re.I)
            )

            if has_exec_concat or has_var_concat or has_format_op:
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "security",
                        "critical",
                        "Possible SQL Injection",
                        "Concatenating variables in SQL queries allows malicious code injection. Use parameterized queries.",
                        [clean_l or raw_l.strip()],
                    )
                )

        # ── 12. Method Stubs and Placeholder Logic ─────────────────────────────
        for i in range(len(raw_lines) - 1):
            curr = re.sub(r"^[+\-]+", "", raw_lines[i].strip()).strip()
            nxt = re.sub(r"^[+\-]+", "", raw_lines[i + 1].strip()).strip()

            # If line is a method definition and next line is a passive return/pass
            if curr.startswith("def ") and (
                nxt == "pass" or
                re.match(r"^return\s+(?:True|False|None|['\"]deprecated['\"]|['\"]todo['\"])\s*$", nxt, re.I)
            ):
                findings.append(
                    StaticAnalyzer._build_finding(
                        path,
                        line_numbers[i],
                        "quality",
                        "medium",
                        "Empty Method Stub detected",
                        "Method appears to be a passive placeholder returning a constant or `pass`.",
                        [curr, nxt],
                    )
                )

        # ── 13. Polyglot Universal Engine (Multi-language) ────────────────────
        in_multiline_docstring = None
        in_multiline_c_comment = False

        for i, raw_l in enumerate(raw_lines):
            clean = re.sub(r"^[+\-]+", "", raw_l.strip()).strip()

            clean_code_only = clean

            # Handle multiline C comments /* ... */
            if in_multiline_c_comment:
                if "*/" in clean_code_only:
                    clean_code_only = clean_code_only.split("*/", 1)[-1].strip()
                    in_multiline_c_comment = False
                else:
                    clean_code_only = ""
            elif "/*" in clean_code_only:
                if "*/" in clean_code_only:
                    clean_code_only = re.sub(r"/\*.*?\*/", "", clean_code_only).strip()
                else:
                    clean_code_only = clean_code_only.split("/*", 1)[0].strip()
                    in_multiline_c_comment = True

            # Handle docstrings (""" or ''')
            if in_multiline_docstring:
                if in_multiline_docstring in clean_code_only:
                    clean_code_only = clean_code_only.split(in_multiline_docstring, 1)[-1].strip()
                    in_multiline_docstring = None
                else:
                    clean_code_only = ""
            else:
                # Strip single-line triple quoted docstrings
                clean_code_only = re.sub(r'""".*?"""', "", clean_code_only)
                clean_code_only = re.sub(r"'''.*?'''", "", clean_code_only)
                if '"""' in clean_code_only:
                    clean_code_only = clean_code_only.split('"""', 1)[0].strip()
                    in_multiline_docstring = '"""'
                elif "'''" in clean_code_only:
                    clean_code_only = clean_code_only.split("'''", 1)[0].strip()
                    in_multiline_docstring = "'''"

            # Strip inline comments: Python (#), JS/Java/C# (//)
            clean_code_only = re.sub(r"\s*#.*$", "", clean_code_only).strip()
            clean_code_only = re.sub(r"\s*//.*$", "", clean_code_only).strip()

            for rule in _POLYGLOT_PATTERNS:
                # code_only=True  → match only against executable code (comments/docstrings stripped)
                # code_only=False → match against the full line (comments preserved)
                target = clean_code_only if rule.get("code_only", False) else clean
                if not target:
                    continue

                if rule.get("func_call_only"):
                    target_for_match = re.sub(r'"(?:\\.|[^"\\])*"', '""', target)
                    target_for_match = re.sub(r"'(?:\\.|[^'\\])*'", "''", target_for_match)
                else:
                    target_for_match = target

                if re.search(rule["pattern"], target_for_match, rule["flags"]):
                    findings.append(
                        StaticAnalyzer._build_finding(
                            path,
                            line_numbers[i],
                            rule["type"],
                            rule["severity"],
                            rule["title"],
                            rule["desc"],
                            [clean],
                        )
                    )

        return findings

    @staticmethod
    def _build_finding(path: str, line: int, typ: str, severity: str, title: str, desc: str, code: List[str]) -> dict:
        return {
            "file": path,
            "line": line,
            "type": typ,
            "severity": severity,
            "title": title,
            "description": desc,
            "vulnerable_code": code,
            "suggestion_code": [],
            "justification": desc,
            "_source": "static",
        }
