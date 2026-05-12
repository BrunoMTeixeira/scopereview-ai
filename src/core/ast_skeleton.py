"""AST Skeleton Extractor — Compresses code to signatures for token efficiency.

Extracts only structural elements (imports, class/function signatures, docstrings)
from source code, discarding function bodies. This produces a compact representation
that preserves the semantic architecture while reducing token consumption by 60-80%.

Used by the Requirements Agent, which needs to verify that functions/classes EXIST
and match acceptance criteria, but does NOT need to inspect implementation details.

For Python files: uses the built-in `ast` module (zero dependencies).
For other languages: falls back to regex-based signature extraction.
"""

import ast
import re
from typing import Dict

from ..core.logger import get_logger

log = get_logger("ASTSkeleton")


def skeletonize_python(source: str) -> str:
    """Extract structural skeleton from Python source code.

    Keeps: imports, class definitions, function signatures, decorators, docstrings.
    Removes: function bodies, inline comments, blank lines.

    Args:
        source: Raw Python source code.

    Returns:
        Compact skeleton string with preserved semantic structure.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        log.warning(f"Failed to parse Python AST at line {e.lineno}, offset {e.offset}: {e.msg}. Context: {repr(e.text)}")
        return _skeletonize_regex(source)

    lines = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.append(ast.get_source_segment(source, node) or _unparse_import(node))

        elif isinstance(node, ast.ClassDef):
            # Class definition with bases
            bases = ", ".join(
                ast.get_source_segment(source, b) or b.id
                for b in node.bases
                if hasattr(b, 'id') or ast.get_source_segment(source, b)
            )
            class_line = f"class {node.name}({bases}):" if bases else f"class {node.name}:"
            lines.append("")
            lines.append(class_line)

            # Class docstring
            docstring = ast.get_docstring(node)
            if docstring:
                short_doc = docstring.split("\n")[0].strip()
                lines.append(f'    """{short_doc}"""')

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Decorators
            for dec in node.decorator_list:
                dec_text = ast.get_source_segment(source, dec)
                if dec_text:
                    lines.append(f"    @{dec_text}" if _is_method(node, tree) else f"@{dec_text}")

            # Function signature
            sig = _build_signature(node, source)
            indent = "    " if _is_method(node, tree) else ""
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"{indent}{prefix} {sig}:")

            # Docstring (first line only)
            docstring = ast.get_docstring(node)
            if docstring:
                short_doc = docstring.split("\n")[0].strip()
                lines.append(f'{indent}    """{short_doc}"""')

            lines.append(f"{indent}    ...")

    # Module-level constants/assignments
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            segment = ast.get_source_segment(source, node)
            if segment and len(segment) < 120:
                lines.insert(0, segment)

    return "\n".join(lines)


def _unparse_import(node: ast.AST) -> str:
    """Fallback for import unparsing when source segment is unavailable."""
    try:
        return ast.unparse(node)
    except Exception:
        return "# import (unparseable)"


def _build_signature(node: ast.FunctionDef, source: str) -> str:
    """Build function signature string with type hints."""
    args = []
    all_args = node.args

    # Regular args
    for i, arg in enumerate(all_args.args):
        name = arg.arg
        annotation = ast.get_source_segment(source, arg.annotation) if arg.annotation else None
        default_idx = i - (len(all_args.args) - len(all_args.defaults))
        default = None
        if default_idx >= 0 and default_idx < len(all_args.defaults):
            default = ast.get_source_segment(source, all_args.defaults[default_idx])

        part = name
        if annotation:
            part = f"{name}: {annotation}"
        if default:
            part = f"{part} = {default}"
        args.append(part)

    # *args
    if all_args.vararg:
        args.append(f"*{all_args.vararg.arg}")

    # **kwargs
    if all_args.kwarg:
        args.append(f"**{all_args.kwarg.arg}")

    # Keyword-only args
    for i, arg in enumerate(all_args.kwonlyargs):
        name = arg.arg
        annotation = ast.get_source_segment(source, arg.annotation) if arg.annotation else None
        default = None
        if i < len(all_args.kw_defaults) and all_args.kw_defaults[i]:
            default = ast.get_source_segment(source, all_args.kw_defaults[i])
        part = f"{name}: {annotation}" if annotation else name
        if default:
            part = f"{part} = {default}"
        args.append(part)

    sig = f"{node.name}({', '.join(args)})"

    # Return annotation
    if node.returns:
        ret = ast.get_source_segment(source, node.returns)
        if ret:
            sig = f"{sig} -> {ret}"

    return sig


def _is_method(func_node: ast.FunctionDef, tree: ast.Module) -> bool:
    """Check if a function is a method inside a class."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if item is func_node:
                    return True
    return False


def _skeletonize_regex(source: str) -> str:
    """Regex-based fallback for non-Python or unparseable files.

    Extracts lines that look like imports, class/function definitions.
    """
    patterns = [
        re.compile(r"^\s*(import |from .+ import )"),
        re.compile(r"^\s*(class |def |async def )"),
        re.compile(r"^\s*(public |private |protected |static |function )"),
        re.compile(r"^\s*(@\w+)"),
        re.compile(r"^\s*(export |const |let |var |interface |type )"),
    ]
    lines = []
    for line in source.splitlines():
        for pattern in patterns:
            if pattern.match(line):
                lines.append(line.rstrip())
                break
    return "\n".join(lines) if lines else source[:500]


def skeletonize_file(path: str, content: str) -> str:
    """Skeletonize a file based on its extension.

    Args:
        path: File path (used to detect language).
        content: Full file content.

    Returns:
        Skeleton string (compact representation).
    """
    lower_path = path.lower()
    if lower_path.endswith(".py"):
        skeleton = skeletonize_python(content)
    else:
        skeleton = _skeletonize_regex(content)

    original_lines = len(content.splitlines())
    skeleton_lines = len(skeleton.splitlines())
    reduction = round((1 - skeleton_lines / max(original_lines, 1)) * 100)
    log.info(
        "Skeleton '%s': %d → %d lines (-%d%%)",
        path, original_lines, skeleton_lines, reduction,
    )
    return skeleton


def skeletonize_map(mapa: Dict[str, str]) -> Dict[str, str]:
    """Skeletonize all files in a map.

    Args:
        mapa: Dictionary mapping file paths to full content.

    Returns:
        Dictionary mapping file paths to skeleton content.
    """
    return {path: skeletonize_file(path, content) for path, content in mapa.items()}


# ─── CODE COMPRESSOR ("AC-Aware Context Pruning") ─────────────────────────────
# Unlike the skeleton (signatures only), the compressor keeps essential
# implementation lines: SQL, returns, method calls, conditionals, string
# literals with key values. Removes intermediary assignments, logging,
# comments, and blank lines. Achieves ~40-50% reduction while preserving
# enough context for requirements verification.

# Regex patterns for lines we ALWAYS keep (structural + implementation)
_KEEP_PATTERNS = [
    re.compile(r"^\s*(import |from .+ import )"),       # imports
    re.compile(r"^\s*(class |def |async def )"),         # definitions
    re.compile(r"^\s*(@\w+)"),                           # decorators
    re.compile(r"^\s*return\b"),                          # return statements
    re.compile(r"^\s*(if |elif |else:)"),                 # conditionals
    re.compile(r"^\s*(with |for |while )"),               # context/loops
    re.compile(r"^\s*(try:|except |finally:)"),           # error handling
    re.compile(r"^\s*(raise )"),                          # exceptions
    re.compile(r".*\.(execute|commit|connect)\("),        # SQL operations
    re.compile(r".*self\.\w+\("),                         # method calls
    re.compile(r".*cursor\.\w+\("),                       # cursor operations
    re.compile(r'.*CREATE TABLE', re.IGNORECASE),        # DDL
    re.compile(r'.*INSERT INTO', re.IGNORECASE),         # DML
    re.compile(r'.*UPDATE .+ SET', re.IGNORECASE),       # DML
    re.compile(r'.*DELETE FROM', re.IGNORECASE),         # DML
    re.compile(r'.*SELECT .+ FROM', re.IGNORECASE),      # DQL
    re.compile(r'^\s*(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|OFFSET|JOIN|HAVING)\b', re.IGNORECASE),  # Multiline DQL
    re.compile(r".*=\s*\{"),                               # dict construction
    re.compile(r"^\s*'\w+':"),                             # dict key (single-q)
    re.compile(r'^\s*"\w+":'),                             # dict key (double-q)
]

# ─── Dynamic AC Term Extraction ──────────────────────────────────────────
# Instead of hardcoded _KEY_LITERALS, we extract key terms directly from
# the Acceptance Criteria text. This makes the compressor self-configuring
# and domain-agnostic — works for GDPR, payments, auth, or any domain.

# Patterns to extract requirement-relevant terms from AC text
_AC_QUOTED_STRING = re.compile(r"['\"`]([A-Za-z0-9_@.]+)['\"`]")
_AC_UPPER_CONST = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
_AC_SNAKE_IDENT = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){1,})\b")
_AC_PLAIN_WORD = re.compile(r"\b([a-zA-Z]{4,})\b")  # Capture generic lowercase terms (e.g. 'limit')
_AC_NUMERIC = re.compile(r"\b(\d{4,})\b")

# Base terms that are always relevant (structural/universal)
_BASE_LITERALS = frozenset([
    "success", "error", "status",
])

# Common English words to exclude from extracted terms
_STOPWORDS = frozenset([
    "must", "should", "shall", "with", "from", "that", "this",
    "have", "each", "after", "before", "only", "into", "value",
    "user", "data", "system", "table", "function", "format",
    "operation", "single", "multiple", "unique", "standard",
    "accepted", "rejected", "contains", "include", "personal",
    "create", "update", "delete", "query", "return", "support",
    "field", "column", "text", "type", "integer", "primary",
    "default", "foreign", "not_null", "para", "como", "deve", "este",
    "esta", "seja", "pelos", "pelas", "uma", "um", "uns", "umas",
])


def extract_ac_terms(work_items: list) -> set:
    """Extract requirement-relevant terms from Acceptance Criteria text.

    Scans all work items' acceptance criteria and extracts:
    - Quoted strings: 'ANONYMIZED', "GDPR_DELETED", `000000000`
    - UPPER_CASE constants: LOGIN_SUCCESS, BULK_SUSPEND
    - snake_case identifiers: gdpr_request_id, audit_logs, is_active
    - Numeric patterns: 000000000 (4+ digits)

    Args:
        work_items: List of work item dicts with 'acceptance_criteria' field.

    Returns:
        Set of extracted terms to use as dynamic key literals.
    """
    terms = set(_BASE_LITERALS)

    for wi in work_items:
        ac_text = wi.get("acceptance_criteria", "") or ""
        desc_text = wi.get("description", "") or ""
        full_text = f"{ac_text} {desc_text}"

        # Extract quoted strings: 'ANONYMIZED', "deleted_", `GDPR_DELETED`
        for match in _AC_QUOTED_STRING.finditer(full_text):
            term = match.group(1)
            if len(term) >= 3:
                terms.add(term)

        # Extract UPPER_CASE constants: LOGIN_SUCCESS, BULK_SUSPEND
        for match in _AC_UPPER_CONST.finditer(full_text):
            term = match.group(1)
            if term.lower() not in _STOPWORDS:
                terms.add(term)

        # Extract snake_case identifiers: gdpr_request_id, audit_logs
        for match in _AC_SNAKE_IDENT.finditer(full_text):
            term = match.group(1)
            if term not in _STOPWORDS and len(term) >= 4:
                terms.add(term)

        # Extract plain lowercase terms (important for non-structured text like 'limit' or 'offset')
        for match in _AC_PLAIN_WORD.finditer(full_text):
            term = match.group(1).lower()
            if term not in _STOPWORDS and len(term) >= 4:
                terms.add(term)

        # Extract numeric patterns: 000000000
        for match in _AC_NUMERIC.finditer(full_text):
            terms.add(match.group(1))

    log.info(
        "AC Term Extraction: %d terms from %d work items: %s",
        len(terms), len(work_items),
        ", ".join(sorted(terms)[:15]) + ("..." if len(terms) > 15 else ""),
    )
    return terms


# Patterns for lines we ALWAYS skip
_SKIP_PATTERNS = [
    re.compile(r"^\s*#"),                                # comments
    re.compile(r"^\s*$"),                                 # blank lines
    re.compile(r"^\s*(log\.|logging\.|print\()"),        # logging/print
    re.compile(r"^\s*pass\s*$"),                          # bare pass
]


def compress_code(source: str, ac_terms: set = None) -> str:
    """Compress code to 'AC-aware pruned view' — keeps implementation-critical lines.

    Keeps: imports, definitions, SQL, returns, conditionals, method calls,
    string literals with AC-derived key values, error handling.
    Removes: comments, blank lines, logging, intermediary variable assignments.
    Groups consecutive removed lines into a single '# ...(N lines)' marker.

    Args:
        source: Raw source code.
        ac_terms: Set of requirement-relevant terms extracted from ACs.
                  If None, only structural patterns are used (no literal matching).

    Returns:
        Compressed code with essential implementation lines preserved.
    """
    key_literals = ac_terms or set()
    lines = source.splitlines()
    result = []
    omitted_count = 0

    for line in lines:
        stripped = line.rstrip()

        # Always skip comments, blank lines, logging
        if any(p.match(stripped) for p in _SKIP_PATTERNS):
            omitted_count += 1
            continue

        # Always keep structural/implementation lines
        keep = any(p.match(stripped) or p.search(stripped) for p in _KEEP_PATTERNS)

        # Keep lines containing AC-derived key literals
        if not keep and key_literals:
            for literal in key_literals:
                if literal in stripped:
                    keep = True
                    break

        # Keep docstrings
        if not keep and ('"""' in stripped or "'''" in stripped):
            keep = True

        if keep:
            # Flush omitted marker before adding kept line
            if omitted_count > 0:
                result.append(f"    # ... ({omitted_count} lines)")
                omitted_count = 0
            result.append(stripped)
        else:
            omitted_count += 1

    # Final omitted marker
    if omitted_count > 0:
        result.append(f"    # ... ({omitted_count} lines)")

    return "\n".join(result)


def compress_file(path: str, content: str, ac_terms: set = None) -> str:
    """Compress a file's code to AC-aware pruned view.

    Args:
        path: File path (for logging).
        content: Full file content.
        ac_terms: Set of requirement-relevant terms from ACs.

    Returns:
        Compressed code string.
    """
    compressed = compress_code(content, ac_terms=ac_terms)

    original_lines = len(content.splitlines())
    compressed_lines = len(compressed.splitlines())
    reduction = round((1 - compressed_lines / max(original_lines, 1)) * 100)
    log.info(
        "Compress '%s': %d → %d lines (-%d%%)",
        path, original_lines, compressed_lines, reduction,
    )
    return compressed


def compress_map(
    mapa: Dict[str, str],
    ac_terms: set = None,
) -> Dict[str, str]:
    """Compress all files in a map to AC-aware pruned view.

    Args:
        mapa: Dictionary mapping file paths to full content.
        ac_terms: Set of requirement-relevant terms from ACs.

    Returns:
        Dictionary mapping file paths to compressed content.
    """
    return {
        path: compress_file(path, content, ac_terms=ac_terms)
        for path, content in mapa.items()
    }
