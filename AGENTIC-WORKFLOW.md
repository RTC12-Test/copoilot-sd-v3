# GitHub Agentic Workflow — Org CI Fixer (schedule)

The **decided solution**: a central-repo **GitHub Agentic Workflow** that scans the
org on a schedule, detects failing child CI jobs, fixes them, and opens labelled PRs.
Everything lives inside GitHub — **no self-hosted runner, no cloud connector**.

## What it does

1. Every **5 minutes** (the GitHub schedule minimum) on a schedule, the agent scans
   the org for repos carrying a **`.ci-fixer.yml`** marker (dynamic repo discovery —
   no hardcoded list).
2. For each marked repo, it finds failing workflow runs, pulls the failure logs,
   and **auto-detects the failure type from the logs** (never hardcoded job names).
3. It clones the repo, analyzes the root cause, and applies a universal fix
   (Python, Terraform, YAML, Docker, Node, etc.).
4. It opens a **fix PR in the child repo** labelled `ci_*` so the central repo can
   recognize and route it. **The agent never merges** — a human reviews and merges
   the PR into the base branch.
5. Child repos are **fully passive** — they just have normal CI + the marker. Their
   CI runs on `pull_request` (not pushes to main), so fix PRs are validated by CI
   before a human merges them.

## The schedule reality (important)

- **GitHub `schedule` is best-effort, not a real cron.** Runs can be delayed minutes
  to tens of minutes under load, and if a previous run is still going, queued runs
  can be dropped (only the latest survives). This is platform behavior, not
  misconfiguration.
- Minimum interval is **5 minutes**; this workflow uses **5 minutes** (the best
  GitHub allows).
- Because schedule alone is coarse, a manual **`workflow_dispatch`** is also exposed
  for on-demand runs (useful for testing or immediate fixes).
## BEFORE you deploy — the `.lock.yml` requirement

GitHub runs the **compiled** `.lock.yml`, not the `.md` directly. You must compile:

```bash
gh extension install github/gh-aw            # once
gh aw compile .github/workflows/org-ci-fixer.aw.md
# -> .github/workflows/org-ci-fixer.aw.lock.yml
```

Commit **both** the `.md` and the `.lock.yml`. Edit the `.md`, recompile, commit the
regenerated lock file — never hand-edit the lock file.

## Engine selection: Copilot (default) or Claude

The engine is **bound at compile time** — a single `.lock.yml` is either Copilot or
Claude; gh-aw cannot switch engines mid-run. So "use Copilot now, Claude optionally"
is a config toggle (recompile when switching), not automatic per-run.

- **Copilot (default, current):** `engine: copilot` in frontmatter. Auth:
  - `permissions.copilot-requests: write` (org centralized Copilot billing), OR
  - a `COPILOT_GITHUB_TOKEN` secret (PAT with `copilot` scope).
- **Switch to Claude:** change `engine: copilot` → `engine: claude`, add an
  `ANTHROPIC_API_KEY` secret, recompile. Also, to go back, remove/change the engine
  and recompile.
- Per-run manual choice is available via the `provider` `workflow_dispatch` input.

## PR label convention (`ci_*`)

Fix PRs are how the central repo recognizes automated remediation:

- **Static `ci_fix`** is applied automatically to every fix PR (from safe-output
  config).
- Each fix PR also gets a **language label `ci_<language>`** so the central repo can
  recognize and route it: `ci_python`, `ci_terraform`, `ci_yaml`, `ci_docker`,
  `ci_node`, `ci_js`, `ci_ts`, `ci_generic`. A multi-language fix adds multiple labels.
- Labels not yet present in a child repo are auto-created by the PR tool.

The central repo should watch for PRs whose labels start with `ci_` (e.g. a branch
protections/automation trigger on label `ci_fix` / `ci_*`, or a `label` webhook).

## Review, don't auto-merge

The agent **only opens** fix PRs — it does **not** merge, auto-merge, approve, or
request its own merge. The handoff is:

1. Agent opens `fix/ci-failure-<desc>` → `main` (or the repo's `base_branch`), with
   the `ci_*` labels.
2. The child's `pull_request` CI runs on that PR — reviewers see the checks.
3. A **human reviews and merges** the PR into the base branch.

Nothing auto-merges. If you want strictness, enable branch protection on `main`
(required checks + required review); the agent-created PRs will then need a human
approval before merge, which is exactly the desired flow.

## Setup on the central repo (`copilot-central`)

### Variables (repository variables — read by the compiled workflow as `env.*`)
| Name | Value |
|---|---|
| `MONITOR_ORG` | Your GitHub org name |
| `MONITOR_REPOS` | Optional comma-separated repos (empty = scan whole org) |
| `MAX_FIXES` | Optional max PRs per run (default 5) |

### Secrets / permissions
| Name | Purpose |
|---|---|
| `COPILOT_GITHUB_TOKEN` or `permissions.copilot-requests: write` | Copilot engine auth |
| `ANTHROPIC_API_KEY` (only if using Claude) | Claude engine auth |
| `GH_AW_GITHUB_TOKEN` / `GITHUB_TOKEN` | Cross-repo write identity (create PRs in child repos) |

## Triggering

- `schedule` — every 5 minutes (minimum; always-on)
- `workflow_dispatch` — manual from the Actions tab (on-demand / testing)

## The child marker file

Every child repo you want monitored just needs a `.ci-fixer.yml` at its root, plus
normal CI. **Child CI should run on `pull_request` (not on pushes to `main`)**, so
that agent-created fix PRs are validated by CI before a human merges them. (Both
the Python and Terraform child workflows already do this.)

```yaml
# .ci-fixer.yml
scan_paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "*.tf"
  - "modules/**/*.tf"
ignore_paths:
  - ".terraform/"
```

## Notes / caveats

- `target-repo: "*"` + `allowed-repos: ["*"]` lets the agent open PRs in any child
  repo; tighten `allowed-repos` to your actual org repos in production.
- PRs created do not trigger CI by default; the
  `github-token-for-extra-empty-commit: app` setting makes CI fire on the new PR.
