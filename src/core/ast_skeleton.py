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
    except SyntaxError:
        log.warning("Failed to parse Python AST; falling back to regex skeleton.")
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
