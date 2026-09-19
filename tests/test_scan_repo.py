"""Tests for the AST usage scanner, against the seeded sample repo."""

from __future__ import annotations

from pathlib import Path

from agent.diff_spec import (
    FIELD_RENAMED,
    REQUEST_PARAM_REQUIRED,
    RESPONSE_FIELD_REMOVED,
    BreakingChange,
)
from agent.scan_repo import find_affected_usages

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "fixtures" / "sample_repo"


def _scan(change: BreakingChange) -> list[dict]:
    scan = find_affected_usages(REPO, [change])
    assert scan["changes"][0]["description"] == change.description
    return scan["usages"][0]


def test_finds_subscript_reads_of_renamed_field():
    usages = _scan(
        BreakingChange(kind=FIELD_RENAMED, location="schema Charge", description="d", field="status", new_field="payment_status")
    )
    # checkout() line 15, refund_order() lines 39 and 41
    assert {(u["file"], u["line"]) for u in usages} == {
        ("payments.py", 15),
        ("payments.py", 39),
        ("payments.py", 41),
    }
    assert all(u["access_style"] == "subscript" for u in usages)


def test_finds_reads_of_removed_field():
    usages = _scan(
        BreakingChange(kind=RESPONSE_FIELD_REMOVED, location="schema Charge", description="d", field="captured")
    )
    assert [(u["file"], u["line"]) for u in usages] == [("payments.py", 23)]


def test_required_param_scan_ignores_other_resources():
    usages = _scan(
        BreakingChange(kind=REQUEST_PARAM_REQUIRED, location="POST /v1/customers", description="d", field="email")
    )
    # Only stripe.Customer.create() — not Refund.create or Charge.create.
    assert [(u["file"], u["line"]) for u in usages] == [("payments.py", 30)]
    assert usages[0]["access_style"] == "call_site"


def test_renamed_change_scans_old_and_new_names():
    usages = _scan(
        BreakingChange(kind=FIELD_RENAMED, location="schema Charge", description="d", field="amount", new_field="amount_due")
    )
    # charge["amount"] read in charge_amount_cents(); the `amount=` kwarg
    # on line 11 is a write, not a read, and must not be flagged.
    assert [(u["file"], u["line"]) for u in usages] == [("payments.py", 47)]


def test_scan_result_is_json_serializable():
    import json

    change = BreakingChange(kind=RESPONSE_FIELD_REMOVED, location="schema Charge", description="d", field="captured")
    scan = find_affected_usages(REPO, [change])
    json.dumps(scan)  # must not raise
