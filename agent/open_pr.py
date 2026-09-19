"""Step 5: deliver the result — a dry-run report locally, or a real PR.

Two delivery modes, deliberately split:

  * write_dry_run_report()  — writes output/pr_report.md. This is the
    artifact you show people: "here are the changes, here is the affected
    code, here is what a fix would look like." No credentials needed.

  * open_pull_request()     — the real thing. Creates a branch, commits
    the patched files, opens a PR linking the provider changelog.
    Requires GITHUB_TOKEN and a repo you have push access to.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

CHANGELOG_URL = "https://docs.example.com/payments/changelog"


def _branch_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:40] or "change"


def render_report(file_results: list[dict], all_changes: list[dict], repo_name: str) -> str:
    """Build the markdown report / PR body from per-file patch results."""
    lines = [
        f"# Automated fix proposal — {repo_name}",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by "
        f"the self-healing API agent.",
        "",
        "## Detected breaking changes",
        "",
    ]
    for change in all_changes:
        lines.append(f"- {change['description']} (`{change['location']}`)")
    lines.append("")

    for result in file_results:
        lines.append(f"## `{result['file']}`")
        lines.append("")
        lines.append("*Affected usages found by the scanner:*")
        lines.append("")
        for usage in result["usages"]:
            lines.append(f"- line {usage['line']} — `{usage['snippet']}`")
        lines.append("")
        validation = result.get("validation")
        if result.get("patched_source") is not None:
            lines.append("*Proposed fix:*")
            lines.append("")
            lines.append("```python")
            lines.append(result["patched_source"])
            lines.append("```")
        elif validation is not None and not validation["ok"]:
            lines.append(f"*Proposed fix rejected by validation:* {validation['error']}")
        else:
            lines.append(
                "*No patch generated* (dry-run mode, or the LLM was not invoked)."
            )
        lines.append("")

    if len(file_results) < len(all_changes):
        lines.append("## Changes with no affected usages found")
        lines.append("")
        patched_changes = {
            c["description"] for r in file_results for c in r["changes"]
        }
        for change in all_changes:
            if change["description"] not in patched_changes:
                lines.append(f"- {change['description']} — nothing to patch (yet).")
        lines.append("")

    lines.append("---")
    lines.append(f"Source changelog: {CHANGELOG_URL}")
    lines.append("")
    lines.append(
        "> A human reviews and merges this. The agent removes the "
        "*notice-and-diagnose* work, not the final judgment call."
    )
    return "\n".join(lines)


def write_dry_run_report(
    file_results: list[dict],
    all_changes: list[dict],
    repo_name: str,
    out_dir: str | Path = "output",
) -> Path:
    """Write the markdown report and return its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "pr_report.md"
    report_path.write_text(
        render_report(file_results, all_changes, repo_name), encoding="utf-8"
    )
    return report_path


def open_pull_request(
    repo_full_name: str,
    file_results: list[dict],
    all_changes: list[dict],
    github_token: str | None = None,
    base_branch: str | None = None,
) -> str:
    """Create a branch, commit patched files, open a PR. Returns the PR URL."""
    from github import Github, GithubException  # lazy: tests don't need it

    token = github_token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set.")

    gh = Github(token)
    repo = gh.get_repo(repo_full_name)
    base_branch = base_branch or repo.default_branch
    base_sha = repo.get_branch(base_branch).commit.sha

    first_change = (
        all_changes[0]["description"] if all_changes else "third-party API change"
    )
    head_branch = f"agent/fix-{_branch_slug(first_change)}"

    try:
        repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_sha)
    except GithubException:
        pass  # branch already exists from a previous run — reuse it

    for result in file_results:
        if result.get("patched_source") is not None:
            repo.create_file(
                path=result["file"],
                message=f"fix: adapt to API change — {first_change[:80]}",
                content=result["patched_source"],
                branch=head_branch,
            )

    pr = repo.create_pull(
        title=f"Fix breaking API change: {first_change[:60]}",
        body=render_report(file_results, all_changes, repo_full_name),
        head=head_branch,
        base=base_branch,
    )
    return pr.html_url
