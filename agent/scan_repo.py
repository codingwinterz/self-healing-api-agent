"""Step 2 of the pipeline: find every place a breaking change would bite.

Two scanning strategies, chosen by the kind of breaking change:

  * Removed/renamed RESPONSE fields — we look for code that *reads* the
    field off an object: ``charge["status"]``, ``charge.get("status")``,
    or the attribute style ``charge.status``.

  * Newly REQUIRED request params — we look for call sites of the
    endpoint's create method and flag the ones missing the parameter.
    The endpoint's resource name comes from the spec path
    (``/v1/customers`` -> ``customer``), so ``stripe.Customer.create()``
    matches but unrelated calls don't.

We use Python's built-in ``ast`` module — it parses source code into a
tree of objects, so we can ask "is this subscript the string 'status'?"
without fragile text matching. Line numbers come for free, which is what
makes the generated PR explanations precise.

Known limitation (documented, not hidden): matching is name-based, so a
same-named field in an unrelated context gets flagged too. We prefer
over-reporting to missing a break — the PR shows snippets, so a human
filters false positives in seconds.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .diff_spec import (
    FIELD_RENAMED,
    REQUEST_PARAM_REQUIRED,
    RESPONSE_FIELD_REMOVED,
)


@dataclass
class Usage:
    """One place in the repo that a breaking change affects."""

    file: str
    line: int
    snippet: str  # the actual source line, for the PR description
    access_style: str  # "subscript", "get_call", "attribute", or "call_site"


def _iter_field_reads(tree: ast.AST, field: str):
    """Yield (node, style) for every read of `field` on any object."""
    for node in ast.walk(tree):
        # charge["status"]  ->  a Subscript whose slice is a string constant.
        # (On Python 3.9+ the slice IS the constant; on older versions it
        # was wrapped in ast.Index, which the getattr fallback covers.)
        if isinstance(node, ast.Subscript):
            sl = node.slice
            inner = sl if isinstance(sl, ast.Constant) else getattr(sl, "value", sl)
            if isinstance(inner, ast.Constant) and inner.value == field:
                yield node, "subscript"
        # charge.get("status")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value == field:
                    yield node, "get_call"
        # charge.status  (attribute style, e.g. SDK objects)
        elif isinstance(node, ast.Attribute) and node.attr == field:
            yield node, "attribute"


def _dotted_name(node: ast.AST) -> str:
    """Build 'stripe.Customer.create' from an Attribute chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _resource_from_path(path: str) -> str:
    """/v1/customers -> 'customer' (last non-parameter path segment, de-pluralized)."""
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    word = segments[-1] if segments else ""
    return word[:-1] if word.endswith("s") else word


def _find_calls_missing_param(tree: ast.AST, resource: str, param: str):
    """Yield (node, style) for .create() calls on `resource` missing `param`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            dotted = _dotted_name(node.func).lower()
            if resource not in dotted.split("."):
                continue
            if node.func.attr != "create":
                continue
            keywords = {kw.arg for kw in node.keywords if kw.arg is not None}
            if param not in keywords:
                yield node, "call_site"


def _scan_source(source: str, change) -> list[Usage]:
    """Run the right strategy for one change against one file's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # not our job to lint the target repo

    source_lines = source.splitlines()
    usages: list[Usage] = []

    if change.kind in (RESPONSE_FIELD_REMOVED, FIELD_RENAMED):
        matches = _iter_field_reads(tree, change.field)
    elif change.kind == REQUEST_PARAM_REQUIRED:
        path = change.location.split(" ", 1)[1]  # "POST /v1/customers" -> path
        matches = _find_calls_missing_param(tree, _resource_from_path(path), change.field)
    else:
        matches = iter(())

    for node, style in matches:
        lineno = getattr(node, "lineno", 1)
        line = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
        usages.append(
            Usage(file="", line=lineno, snippet=line.strip(), access_style=style)
        )
    return usages


def field_reads(source: str, field: str) -> list[tuple[int, str]]:
    """Public helper: (line, access_style) for every read of `field` in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        (getattr(node, "lineno", 1), style)
        for node, style in _iter_field_reads(tree, field)
    ]


def calls_missing_param(source: str, api_path: str, param: str) -> list[int]:
    """Public helper: lines of resource .create() calls missing `param`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        getattr(node, "lineno", 1)
        for node, _ in _find_calls_missing_param(
            tree, _resource_from_path(api_path), param
        )
    ]


def find_affected_usages(repo_dir: str | Path, changes) -> dict:
    """For each breaking change, find every usage it affects.

    Returns {"changes": [...], "usages": {change_index: [usage, ...]}}.
    Usages are plain dicts so the whole result is JSON-serializable
    (handy for reports, PR bodies, prompts, and tests).
    """
    repo = Path(repo_dir)
    py_files = sorted(repo.rglob("*.py"))

    usages_by_change: dict[int, list[dict]] = {}
    for index, change in enumerate(changes):
        found: list[dict] = []
        for path in py_files:
            source = path.read_text(encoding="utf-8")
            for usage in _scan_source(source, change):
                usage.file = str(path.relative_to(repo))
                found.append(usage.__dict__)
        usages_by_change[index] = sorted(found, key=lambda u: (u["file"], u["line"]))

    return {"changes": [c.to_dict() for c in changes], "usages": usages_by_change}
