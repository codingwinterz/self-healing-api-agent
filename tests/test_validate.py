"""Tests for the safety gate: static checks on generated patches."""

from __future__ import annotations

from agent.validate import validate_patch

NL = chr(10)
ORIGINAL = 'def read_charge(charge):' + NL + '    return charge["status"]' + NL
PATCHED = 'def read_charge(charge):' + NL + '    return charge["payment_status"]'


def test_identical_patch_is_rejected():
    result = validate_patch(ORIGINAL, ORIGINAL)
    assert not result.ok
    assert "identical" in result.error


def test_syntax_error_is_rejected():
    result = validate_patch(ORIGINAL, "def broken(:")
    assert not result.ok
    assert "does not parse" in result.error


def test_deleted_public_function_is_rejected():
    result = validate_patch(ORIGINAL, "x = 1")
    assert not result.ok
    assert "read_charge" in result.error


def test_valid_rename_patch_passes():
    assert validate_patch(ORIGINAL, PATCHED).ok
