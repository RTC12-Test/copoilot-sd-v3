# Self-Healing CI Engine (Reusable)

A central **reusable CI engine + self-heal agent**. This repo defines the CI check workflows and the healer that other repos (holding `client_code/` and `infra/terraform/`) use.

## How It Works

```
TARGET REPO (client_code/ + infra/terraform/)
 ├─ ci.yml                  → calls reusable checks FROM THIS REPO
 │     └─ check-python.yml  (client_code/*.py)
 │     └─ check-terraform.yml (infra/terraform/*.tf)
 │     └─ check-yaml.yml    (infra/terraform/*.yaml)
 │           ↓  [CI FAILS]
 └─ self-heal.yml           → workflow_run on "CI Build" failure
       └─ clones this repo's healer → fixes client_code/ + infra/terraform/
            → opens a PR
```

- **This repo** = the engine. Holds the reusable workflows + the healer source (`src/`).
- **Target repo** = holds the actual `client_code/*.py` and `infra/terraform/*.{tf,yaml}` code and *calls* the CI jobs from here.
- Only **changed** files are scanned, fixed, and committed. One PR per failed run.

## Files in This Repo

| File | Purpose |
|------|---------|
| `.github/workflows/check-python.yml` | Reusable (`workflow_call`) Python check — `client_code/*.py` |
| `.github/workflows/check-terraform.yml` | Reusable Terraform check — `infra/terraform/*.tf` |
| `.github/workflows/check-yaml.yml` | Reusable YAML check — `infra/terraform/*.yaml` |
| `.github/workflows/ci.yml` | **Template** for the target repo's CI (calls the above via `uses:`) |
| `.github/workflows/self-heal.yml` | **Template** for the target repo's self-heal (runs `src/healer.py`) |
| `src/` | The healer agent (Python) |

## Setup

### Step 1 — This (engine) repo

No action required beyond keeping `src/` and the reusable workflows on a branch (e.g. `main`) that target repos reference.

### Step 2 — Each target repo (the one with client_code + infra)

Copy these two files into the target repo's `.github/workflows/`:

1. **`ci.yml`** — your target repo's CI. Make sure the `uses:` lines point at the engine repo + branch:
   ```yaml
   uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-python.yml@main
   uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-terraform.yml@main
   uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-yaml.yml@main
   ```

2. **`self-heal.yml`** — add secrets to the target repo:
   | Secret | Description |
   |--------|-------------|
   | `HEALER_TOKEN` | PAT with `repo`, `workflow`, `pull-requests: write` (used for the fixing PR) |
   | `COPILOT_OAUTH_TOKEN` | Copilot OAuth token (`gho_...`) for the Copilot SDK |
   | `AGENT_REPO` (optional) | Defaults to `RTC12-Test/copoilot-sd-v3`; set if healer source moves |

### Flow

1. Developer opens a PR touching `client_code/` or `infra/terraform/`.
2. `ci.yml` runs the reusable checks from this repo.
3. If a check fails, `self-heal.yml` triggers (`workflow_run`).
4. It clones `src/` from this repo, finds the broken files (only `.py` in `client_code/`, `.tf`/`.yaml` in `infra/terraform/`), asks Copilot for fixes, verifies them, and opens a PR titled `[Self-Heal] Fix errors in N file(s)`.

## Local Run

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_...   GH_TOKEN=gho_...   GITHUB_REPO=owner/repo CHANGE_BASE=origin/main
python src/healer.py
```

## Healer Scope (safety fence)

`src/healing/config.py` defines exactly what the healer may touch:

```python
SCAN_ROOTS = {
    "client_code":     {".py"},
    "infra/terraform": {".tf", ".yaml"},
}
```

Anything outside these is never read, fixed, or committed.
