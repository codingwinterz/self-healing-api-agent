"""Offline evaluation harness.

Runs the complete pipeline — real differ, real scanner, real validator —
against both seeded changesets, with a scripted LLM standing in for the
model. Every patch is then scored for resolution:

  renamed   -> old field reads gone AND new field reads present
  removed   -> old field reads gone
  required  -> no remaining resource .create() call missing the param

Verdicts: resolved | not_resolved | no_op (change had no usages in the
sample repo).

Run:  python eval/run_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.main import run_pipeline
from agent.scan_repo import calls_missing_param, field_reads

from fake_llm import make_fake_llm

CHANGESETS = [
    {
        "name": "payments 2024-06-20 -> 2025-01-27",
        "spec_v1": ROOT / "fixtures" / "stripe_spec_v1.json",
        "spec_v2": ROOT / "fixtures" / "stripe_spec_v2.json",
        "repo": ROOT / "fixtures" / "sample_repo",
    },
    {
        "name": "catalog 2025-06-15 -> 2025-09-01",
        "spec_v1": ROOT / "fixtures" / "catalog_spec_v1.json",
        "spec_v2": ROOT / "fixtures" / "catalog_spec_v2.json",
        "repo": ROOT / "fixtures" / "sample_repo2",
    },
]


def _total_reads(field: str | None, patched_sources: dict[str, str]) -> int:
    if not field:
        return 0
    return sum(len(field_reads(src, field)) for src in patched_sources.values())


def _verdict(change: dict, patched_sources: dict[str, str], usage_count: int) -> str:
    if usage_count == 0:
        return "no_op"

    kind = change["kind"]
    if kind == "field_renamed":
        old_gone = _total_reads(change["field"], patched_sources) == 0
        new_present = _total_reads(change["new_field"], patched_sources) > 0
        return "resolved" if old_gone and new_present else "not_resolved"
    if kind == "response_field_removed":
        gone = _total_reads(change["field"], patched_sources) == 0
        return "resolved" if gone else "not_resolved"
    if kind == "request_param_required":
        path = change["location"].split(" ", 1)[1]
        still_missing = sum(
            len(calls_missing_param(src, path, change["field"]))
            for src in patched_sources.values()
        )
        return "resolved" if still_missing == 0 else "not_resolved"
    return "not_resolved"


def main() -> int:
    rows: list[dict] = []
    resolved = failed = no_ops = validation_failures = 0

    for changeset in CHANGESETS:
        result = run_pipeline(
            changeset["spec_v1"],
            changeset["spec_v2"],
            changeset["repo"],
            client=make_fake_llm(),
        )
        assert result["status"] == "ok", (
            f"{changeset['name']}: unexpected pipeline status {result['status']}"
        )

        patched_sources = {
            r["file"]: r["patched_source"]
            for r in result["file_results"]
            if r["patched_source"] is not None
        }
        validation_failures += sum(
            1
            for r in result["file_results"]
            if r["validation"] is not None and not r["validation"]["ok"]
        )

        scan = result["scan"]
        for index, change in enumerate(result["changes"]):
            usage_count = len(scan["usages"][index])
            verdict = _verdict(change, patched_sources, usage_count)
            resolved += verdict == "resolved"
            failed += verdict == "not_resolved"
            no_ops += verdict == "no_op"
            rows.append(
                {
                    "changeset": changeset["name"],
                    "change": change["description"],
                    "usages": usage_count,
                    "verdict": verdict,
                }
            )

    for row in rows:
        print(f"[{row['verdict']:>12}] ({row['usages']} usages) {row['change']}")

    total = len(rows)
    print(
        f"\nResolved {resolved}/{total} seeded breaking changes "
        f"({failed} flagged for a human, {no_ops} had no usages in the "
        f"sample repos, {validation_failures} validation failures)."
    )

    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    (out / "eval_results.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "resolved": resolved,
                "total": total,
                "flagged_for_human": failed,
                "no_op": no_ops,
                "validation_failures": validation_failures,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Results written to {out / 'eval_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
