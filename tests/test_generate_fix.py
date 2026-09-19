"""Tests for the LLM patch step: fake client, Grok adapter, dry-run."""

from __future__ import annotations

import pytest

from agent.generate_fix import generate_fix, xai_client

NL = chr(10)
ORIGINAL = 'def read_charge(charge):' + NL + '    return charge["status"]' + NL
PATCHED = 'def read_charge(charge):' + NL + '    return charge["payment_status"]'


def test_strips_code_fences():
    fenced = "```python" + NL + PATCHED + NL + "```"
    result = generate_fix("f.py", ORIGINAL, [{"description": "d"}], [], client=lambda s, u: fenced)
    assert result.ok
    assert result.patched_source == PATCHED
    assert result.llm_used


def test_survives_llm_errors():
    def boom(system, user):
        raise ConnectionError("network down")

    result = generate_fix("f.py", ORIGINAL, [{"description": "d"}], [], client=boom)
    assert not result.ok
    assert "LLM call failed" in result.error


def test_dry_run_needs_no_client():
    result = generate_fix("f.py", ORIGINAL, [{"description": "d"}], [], client=None)
    assert result.ok
    assert result.patched_source is None
    assert not result.llm_used


class _FakeCompletions:
    """Records kwargs, returns a canned choice."""

    def __init__(self, store):
        self._store = store

    def create(self, **kwargs):
        self._store.append(kwargs)

        class _Msg:
            content = "```python" + NL + PATCHED + NL + "```"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _FakeOpenAI:
    calls = []

    def __init__(self, api_key, base_url):
        assert api_key == "test-key"
        assert base_url == "https://api.x.ai/v1"
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(_FakeOpenAI.calls)


def test_xai_client_builds_openai_compatible_call(monkeypatch):
    """The Grok adapter: real function, fake SDK — verifies wiring, not the network."""
    import agent.generate_fix as gf

    _FakeOpenAI.calls = []
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(gf, "_get_openai_module", lambda: type("openai", (), {"OpenAI": _FakeOpenAI}))

    client = xai_client()
    result = generate_fix("f.py", ORIGINAL, [{"description": "d"}], [], client=client)

    assert result.ok
    assert result.patched_source == PATCHED
    call_kwargs = _FakeOpenAI.calls[0]
    assert call_kwargs["model"] == "grok-4-fast"
    assert call_kwargs["messages"][0]["role"] == "system"


def test_xai_client_requires_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        xai_client()
