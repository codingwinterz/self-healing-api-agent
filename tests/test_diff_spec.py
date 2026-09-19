"""Tests for the spec differ — the engine the whole project rests on."""

from __future__ import annotations

import json
from pathlib import Path

from agent.diff_spec import (
    FIELD_RENAMED,
    REQUEST_PARAM_REQUIRED,
    RESPONSE_FIELD_REMOVED,
    BreakingChange,
    diff_specs,
    load_spec,
)

ROOT = Path(__file__).resolve().parents[1]
STRIPE_V1 = ROOT / "fixtures" / "stripe_spec_v1.json"
STRIPE_V2 = ROOT / "fixtures" / "stripe_spec_v2.json"


def _stripe_changes():
    return diff_specs(load_spec(STRIPE_V1), load_spec(STRIPE_V2))


def test_detects_all_four_seeded_changes():
    changes = _stripe_changes()
    kinds = sorted(c.kind for c in changes)
    assert kinds == sorted(
        [
            FIELD_RENAMED,  # amount -> amount_due
            FIELD_RENAMED,  # status -> payment_status
            RESPONSE_FIELD_REMOVED,  # captured
            REQUEST_PARAM_REQUIRED,  # email
        ]
    )


def test_rename_is_paired_with_new_name():
    renames = {c.field: c.new_field for c in _stripe_changes() if c.kind == FIELD_RENAMED}
    assert renames == {"amount": "amount_due", "status": "payment_status"}


def test_removal_and_required_param_are_described():
    changes = _stripe_changes()
    descriptions = " | ".join(c.description for c in changes)
    assert "'captured'" in descriptions
    assert "'email'" in descriptions and "required" in descriptions


def test_identical_specs_yield_no_changes():
    assert diff_specs(load_spec(STRIPE_V1), load_spec(STRIPE_V1)) == []


def test_breaking_changes_are_json_serializable():
    data = json.dumps([c.to_dict() for c in _stripe_changes()])
    assert json.loads(data)[0]["kind"] in {FIELD_RENAMED, RESPONSE_FIELD_REMOVED, REQUEST_PARAM_REQUIRED}


def test_rename_pairing_uses_similarity_not_order():
    old = {"components": {"schemas": {"Thing": {"properties": {"x_status": {}}}}}}
    new = {"components": {"schemas": {"Thing": {"properties": {"x_payment_status": {}}}}}}
    changes = diff_specs(old, new)
    assert len(changes) == 1
    assert changes[0].kind == FIELD_RENAMED
    assert (changes[0].field, changes[0].new_field) == ("x_status", "x_payment_status")


def test_breaking_change_dataclass_shape():
    change = BreakingChange(kind=FIELD_RENAMED, location="schema Charge", description="d", field="a", new_field="b")
    assert change.to_dict()["new_field"] == "b"
