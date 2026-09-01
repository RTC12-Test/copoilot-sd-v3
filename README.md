# Self-Healing CI Master Repo

This is the **master repo** — it holds the **logic** (reusable CI workflows + the self-heal agent source). Child repos hold the **actual application/infrastructure code** (`client_code/` + `infra/terraform/`) and **call** this repo's reusable workflows and healer.

## Architecture

```
copoilot-sd-v3  (MASTER — logic only)
 ├─ .github/workflows/check-*.yml   reusable checks (workflow_call)
 ├─ src/                            the healer agent (Python)
 └─ Makefile, requirements.txt      healer dependencies
        ▲  referenced via `uses:` / clone
        │
child_repo  (holds the CODE)
 ├─ client_code/*.py                Python app code
 ├─ infra/terraform/*.tf,.yaml      Terraform + YAML infra
 ├─ .github/workflows/ci.yml        calls master's check-*.yml via `uses:`
 └─ .github/workflows/self-heal.yml clones master's src/, runs healer
```

- **Master** carries no application code — it only *provides* the reusable checks and the healer.
- **Child** carries `client_code/` + `infra/terraform/` and *consumes* the master's logic.

## What This (Master) Repo Contains

| Path | Purpose |
|------|---------|
| `.github/workflows/check-python.yml` | Reusable (`workflow_call`) Python check — `client_code/*.py` |
| `.github/workflows/check-terraform.yml` | Reusable Terraform check — `infra/terraform/*.tf` |
| `.github/workflows/check-yaml.yml` | Reusable YAML check — `infra/terraform/*.yaml` |
| `src/healer.py` + `src/healing/` | The self-heal agent (monitor → detect → fix → PR) |
| `Makefile`, `requirements.txt` | Healer shell + Python deps |

## How a Child Repo Uses This Repo

### 1. CI (child's `ci.yml`)
```yaml
jobs:
  yaml:
    uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-yaml.yml@main
  terraform:
    needs: yaml
    uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-terraform.yml@main
  python:
    needs: terraform
    uses: RTC12-Test/copoilot-sd-v3/.github/workflows/check-python.yml@main
```

Because the reusable checks run against the **calling repo's checkout**, the child repo must hold the code it is validating.

### 2. Self-heal (child's `self-heal.yml`)
```yaml
- name: Fetch healer source from master
  run: |
    AGENT_REPO="${{ secrets.AGENT_REPO || 'RTC12-Test/copoilot-sd-v3' }}"
    git clone --depth 1 "https://github.com/${AGENT_REPO}.git" /tmp/self-healer
    mkdir -p ./src
    cp -r /tmp/self-healer/src/healing ./src/healing
    cp /tmp/self-healer/src/healer.py ./src/healer.py
- name: Run self-healer
  env:
    GITHUB_TOKEN: ${{ secrets.HEALER_TOKEN }}
    GH_TOKEN: ${{ secrets.COPILOT_OAUTH_TOKEN }}
    GITHUB_REPO: ${{ github.repository }}
    FAILED_RUN_ID: ${{ github.event.workflow_run.id }}
  run: python src/healer.py
```

## Healer Flow (matches your requirements)

1. **Monitor** `workflow_run`: triggers when "CI Build" completes.
2. **Ignore passes**: `if: workflow_run.conclusion == 'failure'` skips green runs.
3. **On failure**: `monitor.gather_failed_run()` gathers the run console logs + changed files; `healer.py` fixes broken `client_code/*.py` and `infra/terraform/*.tf|yaml` via Copilot; `pull_request.py` opens a single PR.

## Master Repo Setup / Secrets

The master only needs its reusable workflows + `src/` published on the branch children reference (e.g. `main`). Each **child** repo needs `HEALER_TOKEN`, `COPILOT_OAUTH_TOKEN`, and optionally `AGENT_REPO`.