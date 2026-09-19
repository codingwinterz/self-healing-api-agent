"""Step 4a: the safety gate. Nothing generated ships without passing this.

An LLM can produce code that doesn't even parse. Before any patch is
accepted we check it statically — never execute model output, just parse
it. This is the difference between "AI wrote my PR" and "AI wrote a PR
that can't break the build."
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class ValidationResult:
    ok: bool
    error: str | None = None  # human-readable reason when ok is False


def validate_patch(original_source: str, patched_source: str) -> ValidationResult:
    """Static checks on a generated patch. Never executes the code."""
    if patched_source == original_source:
        return ValidationResult(ok=False, error="patch is identical to the original")

    try:
        ast.parse(patched_source)
    except SyntaxError as exc:
        return ValidationResult(
            ok=False, error=f"patched file does not parse: {exc.msg} (line {exc.lineno})"
        )

    # The patch must stay importable Python: every top-level name the file
    # defined before should still exist, so callers elsewhere never break.
    try:
        old_names = {n.name for n in ast.parse(original_source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        new_names = {n.name for n in ast.parse(patched_source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    except SyntaxError:
        return ValidationResult(ok=False, error="internal: original file does not parse")

    missing = old_names - new_names
    if missing:
        return ValidationResult(
            ok=False,
            error=f"patch deleted top-level definitions: {', '.join(sorted(missing))}",
        )

    return ValidationResult(ok=True)
