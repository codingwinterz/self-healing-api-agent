"""Step 1 of the pipeline: compare two versions of an OpenAPI spec.

A spec is just nested JSON, so diffing it means walking two dictionaries
side by side and recording anything that would break an existing integration:

  - a response field that disappeared   -> your code reads it, you crash
  - a renamed field                     -> same crash, but a migration exists
  - a request param that became required -> your calls now get 400 errors

Every finding is a small structured object (a dataclass) so the later
pipeline steps can consume it without re-parsing any JSON.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# The three kinds of breaking change we detect in v1.
RESPONSE_FIELD_REMOVED = "response_field_removed"
FIELD_RENAMED = "field_renamed"
REQUEST_PARAM_REQUIRED = "request_param_required"

# If an old field vanished and a new field appeared with a name this similar,
# we report a rename (which has an obvious fix) instead of a removal.
RENAME_SIMILARITY_THRESHOLD = 0.6


@dataclass
class BreakingChange:
    """One thing that changed in the API and would break calling code."""

    kind: str
    location: str  # e.g. "schema Charge" or "POST /v1/customers request body"
    description: str  # human-readable, used in reports and PR text
    field: str | None = None  # the field/param that changed
    new_field: str | None = None  # only set for renames

    def to_dict(self) -> dict:
        return asdict(self)


def load_spec(path: str | Path) -> dict:
    """Load an OpenAPI spec from a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pair_renames(
    removed: list[str], added: list[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """Match removed fields to newly added fields that look like renames.

    Returns (rename_pairs, fields_that_are_gone_for_good).
    """
    renames: list[tuple[str, str]] = []
    unmatched_new = list(added)

    for old_field in removed:
        best_match, best_score = None, 0.0
        for new_field in unmatched_new:
            score = difflib.SequenceMatcher(None, old_field, new_field).ratio()
            # A new name that contains the old one ("status" -> "payment_status")
            # is almost certainly a rename, even though the strings differ a lot.
            if old_field in new_field or new_field in old_field:
                score += 0.25
            if score > best_score:
                best_match, best_score = new_field, score

        if best_match is not None and best_score >= RENAME_SIMILARITY_THRESHOLD:
            renames.append((old_field, best_match))
            unmatched_new.remove(best_match)

    still_removed = [f for f in removed if f not in {old for old, _ in renames}]
    return renames, still_removed


def _diff_response_schemas(old: dict, new: dict) -> list[BreakingChange]:
    """Compare components.schemas: response object shapes."""
    changes: list[BreakingChange] = []
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})

    for schema_name, old_schema in old_schemas.items():
        if schema_name not in new_schemas:
            continue  # a removed schema is a bigger break than v1 handles
        old_props = old_schema.get("properties", {})
        new_props = new_schemas[schema_name].get("properties", {})
        old_fields = set(old_props)
        new_fields = set(new_props)

        renames, gone = _pair_renames(
            sorted(old_fields - new_fields), sorted(new_fields - old_fields)
        )

        for old_field, new_field in renames:
            changes.append(
                BreakingChange(
                    kind=FIELD_RENAMED,
                    location=f"schema {schema_name}",
                    description=(
                        f"Response field '{old_field}' was renamed to "
                        f"'{new_field}' in schema {schema_name}."
                    ),
                    field=old_field,
                    new_field=new_field,
                )
            )
        for field in gone:
            changes.append(
                BreakingChange(
                    kind=RESPONSE_FIELD_REMOVED,
                    location=f"schema {schema_name}",
                    description=(
                        f"Response field '{field}' was removed from "
                        f"schema {schema_name}."
                    ),
                    field=field,
                )
            )

    return changes


def _diff_request_bodies(old: dict, new: dict) -> list[BreakingChange]:
    """Compare every endpoint's request body: params that became required."""
    changes: list[BreakingChange] = []
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})

    for path, old_methods in old_paths.items():
        new_methods = new_paths.get(path, {})
        for method, old_op in old_methods.items():
            new_op = new_methods.get(method, {})
            old_body = (
                old_op.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            new_body = (
                new_op.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            if not old_body or not new_body:
                continue

            old_required = set(old_body.get("required", []))
            new_required = set(new_body.get("required", []))
            new_props = set(new_body.get("properties", {}))

            for param in sorted(new_required - old_required):
                if param in new_props:
                    changes.append(
                        BreakingChange(
                            kind=REQUEST_PARAM_REQUIRED,
                            location=f"{method.upper()} {path}",
                            description=(
                                f"Request parameter '{param}' is now required "
                                f"for {method.upper()} {path}. Calls without "
                                f"it will be rejected."
                            ),
                            field=param,
                        )
                    )

    return changes


def diff_specs(old: dict, new: dict) -> list[BreakingChange]:
    """Diff two parsed OpenAPI specs and return every breaking change."""
    return _diff_response_schemas(old, new) + _diff_request_bodies(old, new)
