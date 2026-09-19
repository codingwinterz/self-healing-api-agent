"""End-to-end orchestrator.

    spec v1 + spec v2 -> breaking changes -> affected usages -> patches -> report/PR

Run it:

    python -m agent.main --spec-v1 fixtures/stripe_spec_v1.json \
        --spec-v2 fixtures/stripe_spec_v2.json --repo fixtures/sample_repo

Add --fix to involve the LLM (--provider anthropic needs ANTHROPIC_API_KEY;
--provider grok needs XAI_API_KEY), and --pr owner/name
to open a real pull request (needs GITHUB_TOKEN). Without them the agent
runs in dry-run mode and writes output/pr_report.md instead.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .diff_spec import diff_specs, load_spec
from .generate_fix import generate_fix
from .open_pr import open_pull_request, write_dry_run_report
from .scan_repo import find_affected_usages


def _group_by_file(scan: dict) -> dict[str, dict]:
    """Collect the changes and usages that touch each affected file.

    Patching one file once with *all* its changes (instead of once per
    change) means one LLM call per file and no merge conflicts between
    our own patches.
    """
    per_file: dict[str, dict] = {}
    for change_index, usages in scan["usages"].items():
        change = scan["changes"][change_index]
        for usage in usages:
            entry = per_file.setdefault(usage["file"], {"changes": [], "usages": []})
            if not any(
                c["description"] == change["description"] for c in entry["changes"]
            ):
                entry["changes"].append(change)
            entry["usages"].append(usage)
    return per_file


def run_pipeline(spec_v1, spec_v2, repo_dir, client=None) -> dict:
    """Run the full pipeline. `client=None` keeps it in dry-run mode."""
    old, new = load_spec(spec_v1), load_spec(spec_v2)
    changes = diff_specs(old, new)
    if not changes:
        return {"status": "no_changes", "changes": [], "file_results": []}

    scan = find_affected_usages(repo_dir, changes)
    per_file = _group_by_file(scan)
    if not per_file:
        return {
            "status": "no_usages",
            "changes": scan["changes"],
            "scan": scan,
            "file_results": [],
        }

    file_results = []
    for rel_path, bundle in sorted(per_file.items()):
        source = (Path(repo_dir) / rel_path).read_text(encoding="utf-8")
        result = generate_fix(
            rel_path, source, bundle["changes"], bundle["usages"], client=client
        )
        file_results.append(
            {
                "file": result.file,
                "changes": result.changes,
                "usages": bundle["usages"],
                "ok": result.ok,
                "patched_source": result.patched_source,
                "validation": result.validation.__dict__ if result.validation else None,
                "error": result.error,
                "llm_used": result.llm_used,
            }
        )
    return {
        "status": "ok",
        "changes": scan["changes"],
        "scan": scan,
        "file_results": file_results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="self-healing-api-agent", description="Detect breaking API changes, find affected code, propose fixes."
    )
    parser.add_argument("--spec-v1", required=True, help="path to the old OpenAPI spec (JSON)")
    parser.add_argument("--spec-v2", required=True, help="path to the new OpenAPI spec (JSON)")
    parser.add_argument("--repo", required=True, help="path to the repo to scan")
    parser.add_argument(
        "--fix", action="store_true",
        help="generate patches with the LLM (needs the provider API key)",
    )
    parser.add_argument(
        "--provider", choices=["anthropic", "grok"], default="anthropic",
        help="which LLM writes the patches (default: anthropic; grok needs XAI_API_KEY)",
    )
    parser.add_argument(
        "--pr", metavar="OWNER/REPO",
        help="open a real PR on this GitHub repo (needs GITHUB_TOKEN)",
    )
    args = parser.parse_args(argv)

    client = None
    if args.fix:
        from .generate_fix import anthropic_client, xai_client

        if args.provider == "grok":
            client = xai_client()
        else:
            client = anthropic_client()

    result = run_pipeline(args.spec_v1, args.spec_v2, args.repo, client=client)

    if result["status"] == "no_changes":
        print("No breaking changes detected — nothing to do.")
        return 0

    print(f"Detected {len(result['changes'])} breaking change(s):")
    for change in result["changes"]:
        print(f"  - {change['description']}")

    if result["status"] == "no_usages":
        print("No affected usages found in the target repo.")
        return 0

    patched = sum(1 for r in result["file_results"] if r["patched_source"] is not None)
    total_usages = sum(len(r["usages"]) for r in result["file_results"])
    print(
        f"Found {total_usages} affected usage(s) across "
        f"{len(result['file_results'])} file(s); {patched} file(s) patched."
    )

    if args.pr:
        url = open_pull_request(args.pr, result["file_results"], result["changes"])
        print(f"Pull request: {url}")
    else:
        report = write_dry_run_report(
            result["file_results"], result["changes"], Path(args.repo).name
        )
        print(f"Dry-run report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
