"""A scripted LLM stand-in for the offline evaluation.

It implements the same strategy a careful engineer would, driven purely by
the structured change data the pipeline puts in the prompt:

  * field renames  -> rewrite reads of the old name to the new name
  * required params -> add the missing kwarg to the resource's create() call

It deliberately does NOT guess a fix for removed response fields — there is
no mechanical answer (drop the check? substitute a default?). Those land in
the eval as "needs a human", which is exactly the behavior we want to show.

If the prompt ever stops carrying the STRUCTURED CHANGE DATA block, this
fake fails loudly — which is the point: it tests the contract, not vibes.
"""

from __future__ import annotations

import ast
import json
import re


def _changes_from_prompt(user_prompt: str) -> list[dict]:
    match = re.search(
        r"STRUCTURED CHANGE DATA:\s*```json\s*(.*?)```", user_prompt, re.DOTALL
    )
    if not match:
        raise ValueError("fake LLM: no STRUCTURED CHANGE DATA block in prompt")
    return json.loads(match.group(1))


def _source_from_prompt(user_prompt: str) -> str:
    match = re.search(r"FILE:\s*```python\s*(.*?)```", user_prompt, re.DOTALL)
    if not match:
        raise ValueError("fake LLM: no FILE block in prompt")
    return match.group(1)


def _apply_renames(source: str, changes: list[dict]) -> str:
    renames = [c for c in changes if c["kind"] == "field_renamed"]
    if not renames:
        return source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            sl = node.slice
            inner = sl if isinstance(sl, ast.Constant) else getattr(sl, "value", sl)
            if isinstance(inner, ast.Constant):
                for c in renames:
                    if inner.value == c["field"]:
                        inner.value = c["new_field"]
        elif isinstance(node, ast.Attribute):
            for c in renames:
                if node.attr == c["field"]:
                    node.attr = c["new_field"]
    return ast.unparse(tree)


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") else word


def _apply_required_params(source: str, changes: list[dict]) -> str:
    required = [c for c in changes if c["kind"] == "request_param_required"]
    if not required:
        return source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "create":
            continue
        dotted = ast.unparse(node.func).lower()
        for c in required:
            path = c["location"].split(" ", 1)[1]
            resource = _singular(path.rstrip("/").rsplit("/", 1)[-1])
            if resource not in dotted.split("."):
                continue
            if all(kw.arg != c["field"] for kw in node.keywords):
                node.keywords.append(
                    ast.keyword(
                        arg=c["field"],
                        value=ast.Constant(value=f"SET_ME: required {c['field']}"),
                    )
                )
    return ast.unparse(tree)


def make_fake_llm():
    """Build an injectable client with the scripted strategy."""

    def client(system: str, user: str) -> str:
        changes = _changes_from_prompt(user)
        source = _source_from_prompt(user)
        source = _apply_renames(source, changes)
        source = _apply_required_params(source, changes)
        return source

    return client
