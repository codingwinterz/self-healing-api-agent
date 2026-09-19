# Self-Healing API Integration Agent

[![CI](https://github.com/codingwinterz/self-healing-api-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/codingwinterz/self-healing-api-agent/actions/workflows/ci.yml)

An autonomous agent that watches a third-party API for breaking changes, finds every place in a codebase the change would bite, and opens a ready-to-review pull request with the fix — closing the loop between "the API changed" and "the code still works."

> **Measured on the included benchmark:** 7/10 seeded breaking changes mechanically resolved, 3/10 correctly escalated to a human (removed response fields — no mechanical fix exists), 0 invalid patches shipped. Run `python eval/run_eval.py` to reproduce.

Existing tools like Dependabot and Renovate bump package version numbers. They don't detect or fix the actual logic that breaks when an API's behavior changes underneath a pinned dependency — a renamed response field read in four places, a parameter that silently became required. This agent does.

## Results

Offline evaluation across two seeded changesets (10 breaking changes total), scored by a script that re-scans the patched output — not by eyeballing:

| Verdict | Count | Which changes |
|---|---|---|
| Resolved automatically | **7/10** | All renames, all newly-required params |
| Escalated to human | **3/10** | Removed response fields (no mechanical fix exists) |
| Invalid patches | **0** | Every proposed patch passed the validation gate |

The escalation behavior is deliberate, not a gap: when a response field disappears, the *right* fix depends on business intent, so the agent flags it for a human instead of guessing.

## How it works

```
spec v1 ──┐
          ├─ diff ──> BreakingChanges ──> AST usage scan ──> affected files+lines
spec v2 ──┘                                                     │
                                                                v
                                                LLM rewrite (one call per file)
                                                                │
                                                                v
                                                validation gate (parse + structure)
                                                                │
                                                                v
                                                PR / dry-run report
```

1. **Change detection** (`agent/diff_spec.py`) — diffs two versions of an OpenAPI spec. Detects renamed/removed response fields and newly-required request params. Renames are paired by fuzzy name matching, so `status -> payment_status` is reported as a rename (which has an obvious fix), not a removal.
2. **Usage scanning** (`agent/scan_repo.py`) — parses the target repo with Python's `ast` module and finds every read of an affected field (`charge["status"]`, `charge.get("status")`, `charge.status`) and every resource `.create()` call missing a now-required parameter, with exact line numbers.
3. **Fix generation** (`agent/generate_fix.py`) — one LLM call per affected file, given the structured change data and the found usages. The client is injected: real runs use Anthropic (default) or Grok (`--provider grok`, needs `XAI_API_KEY`), the offline eval uses a scripted stand-in through the same code path.
4. **Validation gate** (`agent/validate.py`) — generated code is *parsed*, never executed. Patches that don't parse, don't change anything, or delete public functions are rejected before they can reach a PR.
5. **Delivery** (`agent/open_pr.py`) — a dry-run markdown report locally, or a real GitHub PR with the full diagnosis, via PyGithub.

## Run it

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt

# 1. Tests
python -m pytest tests/ -v

# 2. Dry run — detects changes, scans the repo, writes output/pr_report.md
python -m agent.main --spec-v1 fixtures/stripe_spec_v1.json \
    --spec-v2 fixtures/stripe_spec_v2.json --repo fixtures/sample_repo

# 3. Offline evaluation (no API key needed)
python eval/run_eval.py

# 4. Full run with real LLM patches (needs ANTHROPIC_API_KEY, or --provider grok with XAI_API_KEY)
python -m agent.main --spec-v1 ... --spec-v2 ... --repo ... --fix

# 5. Open a real PR (also needs GITHUB_TOKEN)
python -m agent.main ... --fix --pr owner/repo
```

`fixtures/` contains two seeded changesets — spec versions before/after plus sample repos whose code each change breaks — so the whole pipeline demos deterministically, with no waiting on real-world API drift.

## Honest limitations

- **Name-based matching** can over-report: a same-named field in an unrelated context gets flagged. Deliberate trade-off — the report shows snippets, so a human filters false positives in seconds.
- **Single file at a time**: patches are per-file; cross-file refactors (e.g. updating a shared type) are future work.
- **Python repos only** (v1) — the AST scanner is CPython's `ast`; other languages need language-specific scanners.
- **Removals escalate, they don't guess** — by design.

## Why not just Dependabot?

Dependabot bumps version numbers when the *dependency* changes. This handles the case where the dependency is pinned and the *service behind it* changes — which is exactly when teams find out from a production incident instead of a changelog.
