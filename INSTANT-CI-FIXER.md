# Instant CI Fixer — GitHub App + webhook → Central repo

Event-driven, **sub-minute** detection of child repo CI failures. No polling,
children stay fully passive. The central repo (`copilot-central`) is the single
control point.

## Architecture

```
 child repo CI fails
      │  GitHub (platform) sends webhook
      ▼
 GitHub App (org install) ── workflow_run `completed` + conclusion=failure
      │  POST webhook (signed)
      ▼
 ci-fixer-webhook receiver (tiny hosted service)
      │  validates signature, filters failures, dedups
      ▼ POST https://api.github.com/repos/{central}/dispatches
 repository_dispatch "ci-fixer"
      ▼
 gh-aw fix workflow (org-ci-fixer.dispatch) → analyzes + opens PR in child repo
```

Key facts that make this work (verified against GitHub docs):
- A **GitHub App** installed on the org (select "All repositories") receives
  `workflow_run` webhooks for **every** child repo automatically. Children need
  ***no code*** — webhooks are a platform feature, not child CI logic.
- `workflow_run` fires `action: completed` with `conclusion: failure` the moment
  a child's workflow finishes. Requires GitHub App **"Actions: Read"** permission.
- The receiver forwards to the central repo's `repository_dispatch`, which the
  gh-aw workflow consumes (`github.event.client_payload.*`).

## What this replaces

The 5-minute `schedule` polling workflow (`org-ci-fixer.aw.md`). Decide whether to
keep it as a safety net or delete it — see below.

## Components

| Path (central repo) | Purpose |
|---|---|
| `github-app/app.yml` | GitHub App manifest (webhook + permissions) |
| `github-app/receiver/server.py` | Webhook receiver (stdlib Python, no deps) |
| `.github/workflows/org-ci-fixer.dispatch.md` + `.lock.yml` | gh-aw fix workflow (dispatch-triggered) |
| `.github/workflows/org-ci-fixer.aw.md` (+ optional lock) | old 5-min schedule fallback |

## Setup

### 1. Create the GitHub App

Go to `https://github.com/settings/apps/new` (or create via the manifest
workflow using `github-app/app.yml` as the manifest). Configure:

- **Webhook URL**: the public URL of your hosted receiver (below).
- **Webhook secret**: a strong random secret (share with the receiver).
- **Repository permissions**:
  - `Actions`: **Read** (required for `workflow_run`)
  - `Checks`: **Read** (for `check_run` fallback)
  - `Contents`, `Pull requests`, `Issues`: **Write** (to open fix PRs)
  - `Metadata`: **Read**
- **Subscribe to events**: `workflow_run`, `check_run`, `workflow_job`.
- Install on the **organization**, choosing **"All repositories"** so every child
  (and future child) is covered automatically — this is your dynamic repo list.
  The app is *not* installed on the central repo (it's the one that consumes).

### 2. Host the receiver

`server.py` is dependency-free Python (stdlib only). Run it anywhere you can run
Python with a public HTTPS endpoint (container, Cloud Run, Fly.io, Railway,
serverless function). Env vars:

| Env | Required | Description |
|---|---|---|
| `WEBHOOK_SECRET` | yes | Same secret set on the GitHub App |
| `CENTRAL_OWNER` | yes | Owner of the central repo |
| `GITHUB_TOKEN` | yes | Token with write on the central repo (to dispatch) — a fine-grained PAT scoped to `copilot-central` "Actions + Metadata" is enough |
| `CENTRAL_REPO` | no | Default `copilot-central` |
| `DISPATCH_EVENT_TYPE` | no | Default `ci-fixer` (must match workflow trigger) |
| `PORT` | no | Default `8080` |

Run: `WEBHOOK_SECRET=... GITHUB_TOKEN=... CENTRAL_OWNER=... python3 server.py`

### 3. Compile the fix workflows

```bash
gh aw compile .github/workflows/org-ci-fixer.dispatch.md   # prod path
gh aw compile .github/workflows/org-ci-fixer.aw.md          # schedule fallback (if kept)
```

Commit both `.md` and `.lock.yml`.

### 4. Engine credentials

The fix workflow runs the Copilot/claude/codex/gemini engine. For Copilot, either
set `permissions.copilot-requests: write` (org centralized Copilot billing) or a
`COPILOT_GITHUB_TOKEN` secret. The fix workflow creates PRs in child repos, so the
action token identity needs cross-repo write (`GH_AW_GITHUB_TOKEN` / GitHub App).

## Receiving behavior (receiver)

- Verifies `X-Hub-Signature-256` (HMAC-SHA256) against `WEBHOOK_SECRET`.
- On `workflow_run` `completed` with `conclusion` in
  {`failure`, `timed_out`, `startup_failure`, `cancelled`} → dispatches.
- On `check_run` `completed` with hard failure → dispatches (fallback signal).
- **Dedups** by `repo:run` so a run is dispatched once.
- Sends `repository`, `run_id`, `head_branch`, `head_sha`, `conclusion`, event to
  the central repo's dispatch.

## Verified

- Receiver: signature verification, failure filtering (success/in_progress
  ignored), per-run dedup, and dispatch payload all unit-tested locally. ✅
- Both gh-aw workflows compile cleanly (`gh aw compile`). ✅

## Decide: keep the schedule fallback?

The `org-ci-fixer.aw.md` 5-minute scan is now optional. Options:

- **Keep both**: schedule is a cheap safety net in case the webhook/receiver
  misses an event (e.g. receiver down). Risk: occasional redundant scan after a
  fix already landed — acceptable since dispatch is per-run and the scan only
  fixes still-failing repos.
- **Delete schedule**: purely event-driven; single source of truth. If the
  receiver is down, failures are missed until it's back + a manual dispatch.

Recommendation: keep the schedule as a light safety net at a *long* interval
(e.g. hourly), retargeting only still-failing repos.
