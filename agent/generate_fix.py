"""Step 3: ask an LLM to rewrite affected files to match the new contract.

The prompt gives the model everything it needs and nothing else:
  1. the breaking changes (structured JSON, straight from the differ)
  2. the affected usages the scanner found (file, line, snippet)
  3. the full current source of the file

The model returns the complete rewritten file. We then hand it to
validate.py — this module never accepts its own output.

The LLM client is *injected* (a function: system prompt, user prompt ->
text). Real runs pass an Anthropic client (or a Grok/xAI client via
``--provider grok``); tests and the offline eval pass a scripted fake. Same code path either way, which is what makes the
offline eval an honest test of the pipeline rather than of the network.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable

from .validate import ValidationResult, validate_patch

LLMClient = Callable[[str, str], str]

SYSTEM_PROMPT = (
    "You are a careful senior engineer updating code after a third-party "
    "API breaking change. You return ONLY the complete rewritten Python "
    "file — no explanations, no markdown fences. You make the smallest "
    "possible changes: you never rename functions, never reformat "
    "unrelated code, never remove public functions."
)


def _build_prompt(changes: list[dict], usages: list[dict], source: str) -> str:
    change_lines = "\n".join(f"- {c['description']}" for c in changes)
    usage_lines = "\n".join(
        f"  - {u['file']}:{u['line']} ({u['access_style']}): {u['snippet']}"
        for u in usages
    )

    return (
        "A third-party API shipped these breaking changes:\n"
        f"{change_lines}\n\n"
        "STRUCTURED CHANGE DATA:\n"
        "```json\n" + json.dumps(changes, indent=2) + "\n```\n\n"
        "AFFECTED USAGES (structured):\n"
        "```json\n" + json.dumps(usages, indent=2) + "\n```\n\n"
        f"The scanner found these affected usages:\n{usage_lines}\n\n"
        "Rewrite the file below so it works with the new API contract. "
        "Keep every public function signature identical. If a field was "
        "renamed, switch reads to the new name. If a request parameter "
        "became required, pass it explicitly.\n\n"
        "FILE:\n```python\n" + source + "\n```"
    )


def _strip_code_fences(text: str) -> str:
    """Defensively remove markdown fences if the model added them anyway."""
    fenced = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return fenced.group(1).rstrip("\n") if fenced else text


@dataclass
class PatchResult:
    """Outcome of asking for (and validating) one file's rewrite."""

    file: str
    changes: list[dict]
    ok: bool
    patched_source: str | None
    validation: ValidationResult | None
    error: str | None = None
    llm_used: bool = False


def generate_fix(
    file_path: str,
    source: str,
    changes: list[dict],
    usages: list[dict],
    client: LLMClient | None = None,
) -> PatchResult:
    """Generate (and validate) a patched version of one file.

    `client=None` means dry-run mode: we stop after reporting what *would*
    be patched. Useful for demoing the scanner without spending API credits.
    """
    if client is None:
        return PatchResult(
            file=file_path,
            changes=changes,
            ok=True,
            patched_source=None,
            validation=None,
            llm_used=False,
        )

    try:
        raw = client(SYSTEM_PROMPT, _build_prompt(changes, usages, source))
    except Exception as exc:  # network, auth, rate limits...
        return PatchResult(
            file=file_path,
            changes=changes,
            ok=False,
            patched_source=None,
            validation=None,
            error=f"LLM call failed: {exc}",
        )

    patched = _strip_code_fences(raw)
    validation = validate_patch(source, patched)
    return PatchResult(
        file=file_path,
        changes=changes,
        ok=validation.ok,
        patched_source=patched if validation.ok else None,
        validation=validation,
        llm_used=True,
    )


def anthropic_client(model: str = "claude-sonnet-4-5") -> LLMClient:
    """Real client used in production runs. Requires ANTHROPIC_API_KEY."""
    import anthropic  # imported lazily so tests never need the package

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it, or run without --fix "
            "for a dry-run."
        )
    client = anthropic.Anthropic(api_key=api_key)

    def call(system: str, user: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    return call


def _get_openai_module():
    """Indirection so tests can substitute a fake `openai` module."""
    import openai

    return openai


def xai_client(model: str = "grok-4-fast") -> LLMClient:
    """Grok/xAI client — an OpenAI-compatible alternative patch-writer.

    Requires XAI_API_KEY. Uses the `openai` SDK pointed at https://api.x.ai/v1;
    xAI's API speaks the OpenAI chat protocol, so no special SDK is needed.
    """
    openai = _get_openai_module()

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is not set. Export it, or run with --provider anthropic."
        )
    client = openai.OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    def call(system: str, user: str) -> str:
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content

    return call
