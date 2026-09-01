# Self-Healing Build Pipeline (`src/healer.py`)

One script that detects broken Python, Terraform, and YAML files in
`client_code/` and `infra/terraform/`, asks **GitHub Copilot** to repair
them, verifies the repairs, and opens a **single pull request** with
every accepted fix. The Copilot prompt is extracted separately in
`prompts/fix-prompt.md`.

---

## 1. The pipeline at a glance

```
            ┌──────────────────────────────────────────────────────┐
            │                     healer.py                        │
            └──────────────────────────────────────────────────────┘

  git status (client_code/ + infra/terraform/)
        │
        ▼
  ┌─────────────┐  only YOUR changed files
  │ 0. TRACK    │  (modified / staged / renamed / untracked)
  └─────────────┘
        │
        ▼
  ┌───────────┐   ast.parse      ┌───────────────┐  pyflakes     ┌──────────────┐  run file,   ┌────────────┐
  │ 1. SCAN   │────────────────►│ 2a. SYNTAX     │─clean?───────►│ 2b. STATIC   │─clean?──────►│ 2c. RUNTIME│
  │           │                  │     CHECK      │               │ (undefined   │              │    CHECK   │
  └───────────┘                  └───────────────┘               │  names only) │              └────────────┘
        │  broken files: {file, line, message, content}          └──────────────┘
        │
        │  .tf files → terraform init + validate (one call per root module)
        │  .yaml files → yaml.safe_load + duplicate key check
        ▼
  ┌─────────────────┐  prompt per file  ┌──────────────────────┐
  │ 3. COPILOT FIX  │──────────────────►│ Copilot session      │
  │                 │◄──────────────────│ (gpt-5, one session) │
  └─────────────────┘  corrected code   └──────────────────────┘
        │  candidate fixes
        ▼
  ┌──────────────┐  empty / unchanged / still-broken → rejected
  │ 4. VERIFY    │  .py → ast.parse, .tf → terraform validate, .yaml → yaml.safe_load
  └──────────────┘
        │  accepted fixes {path: new_content}
        ▼
  ┌──────────────┐  branch self-heal/run-<timestamp> + ONE PR
  │ 5. SHIP PR   │
  └──────────────┘
```

## 2. Repo layout

```
copilot-sdk-v2/
├── client_code/             ← Python files (broken ones get healed)
│   └── ...
├── infra/terraform/         ← Terraform + YAML files (broken ones get healed)
│   ├── providers.tf         ← terraform + provider config
│   ├── infra.tf             ← module calls (s3, cloudwatch, ecs-cluster)
│   ├── variables.tf         ← input variables
│   ├── outputs.tf           ← output values
│   ├── config_env/dev/      ← YAML config per environment
│   │   └── config.yaml
│   ├── Makefile             ← terraform/init, terraform/configure, terraform/validate
│   └── modules/             ← reusable terraform modules
│       ├── config/          ← reads YAML → HCL config via yamldecode()
│       ├── s3/              ← S3 bucket resources
│       ├── cloudwatch/      ← CloudWatch log group, metric filter, alarm
│       └── ecs-cluster/     ← ECS cluster resource
├── src/
│   ├── healer.py            ← thin entry point (`python src/healer.py`)
│   └── healing/             ← the pipeline, one module per step
│       ├── config.py         ← constants, logger, scan roots, safety fence
│       ├── tracking.py       ← STEP 0  which files changed (git)
│       ├── detect.py         ← STEP 1+2 syntax / pyflakes / runtime / terraform / yaml checks
│       ├── copilot_fixer.py  ← STEP 3+4 prompt build + fix verification
│       └── pull_request.py   ← STEP 5  branch, commit, single PR
├── prompts/
│   └── fix-prompt.md        ← the exact Copilot prompt, extracted for review
├── .github/workflows/
│   ├── ci.yml               ← orchestrator: yaml → terraform → python (all run on failure)
│   ├── check-yaml.yml       ← reusable workflow: YAML syntax + duplicate key check
│   ├── check-terraform.yml  ← reusable workflow: terraform init + validate
│   ├── check-python.yml     ← reusable workflow: python syntax + runtime check
│   └── self-heal.yml        ← triggered on CI failure, runs healer
├── PIPELINE.md              ← you are here — full walkthrough
└── requirements.txt         ← PyGitHub + github-copilot-sdk + pyflakes + pyyaml
```

## 3. Configuration

| Constant | Default | Purpose |
|---|---|---|
| `SCAN_ROOTS` | `{"client_code": {".py"}, "infra/terraform": {".tf", ".yaml"}}` | Folders the healer touches and which file types live in each |
| `RUN_TIMEOUT_SECONDS` | `10` | Max seconds any scanned file may run |
| `COPILOT_MODEL` | `gpt-5` | Model used to generate fixes |
| `MAX_FIX_ATTEMPTS` | `2` | Retries per file before giving up |

### Environment variables (required to run)

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub PAT with repo + workflow permissions |
| `GH_TOKEN` | GitHub OAuth token for the Copilot SDK (`gho_...`) |
| `GITHUB_REPO` | Repository to heal, as `owner/repo` |
| `SOURCE_BRANCH` | *Optional* branch to target; defaults to the repo's default branch |

```bash
export GITHUB_TOKEN=ghp_xxx
export GH_TOKEN=gho_xxx
export GITHUB_REPO=owner/repo
python src/healer.py
```

## 4. Step-by-step walkthrough

### STEP 0 — Track your changes (`get_changed_files`)

Before touching anything, the healer asks git what changed. Two modes:

| Mode | When | Command |
|---|---|---|
| **Local** | you run `python src/healer.py` yourself | `git status --porcelain -- client_code infra/terraform` |
| **CI** | `CHANGE_BASE` is set (self-heal.yml sets it) | `git diff --name-only $CHANGE_BASE...HEAD -- client_code infra/terraform` |
| **Fallback** | git fails, *or* tracking finds nothing (everything already committed) | scan all `.py` + `.tf` + `.yaml` in both roots |

The CI checkout is a clean copy, so `git status` would always be empty there —
that is why self-heal.yml exports `CHANGE_BASE=origin/<default-branch>` (with
`||` fallbacks down to a literal `main`) and the healer diffs commits since
the base instead. If the diff ever fails, the healer logs a warning and
falls back to every file instead of crashing the run.

Either way it keeps only matching file types under each root. **Untouched files
are never scanned or fixed**, so a run only heals the work in front of you.
**Renamed files are still healed** — git reports a rename as
`R<score> old -> new`, and the healer tracks the NEW name. **Deleted files**
are the only thing skipped, since there is nothing left on disk to heal.

### STEP 1+2 — Check changed files (`find_broken_files`)

Every changed file is checked by type, cheapest first:

**.py files** — three layers:
1. **Syntax check** (`_syntax_problem`) — `ast.parse()` the source.
   No code runs; a `SyntaxError` immediately marks the file broken.
2. **Static check** (`_undefined_name_problem`) — pyflakes scans without
   executing, catching undefined variables even inside branches that never
   run (e.g. `if 1 == 9: print(d)`). Style warnings like "imported but
   unused" are ignored; only `undefined name` counts as broken.
3. **Runtime check** (`_runtime_problem`) — executes the file with a
   10-second timeout and parses stderr for crashes:
   - `(\w+Error|\w+Exception):\s*(.+)` → error type + message
   - `File "...", line (\d+)` → line number

**.tf files** — terraform CLI (one `init` + `validate` per root module):
1. **Terraform init** (`terraform init -backend=false`) — catches syntax
   errors across all modules (unclosed blocks, missing braces).
2. **Terraform validate** (`terraform validate -json`) — catches additional
   errors like unclosed strings. "Module not installed" cascading errors
   are filtered out.
3. Both parsers strip ANSI escape codes and map terraform's relative paths
   back to project-root paths. Diagnostics are matched to ALL `.tf` files
   on disk, not just the ones that changed.
4. Skips silently if the `terraform` CLI is not installed.

**.yaml files** — two layers:
1. **Syntax check** (`_yaml_problem`) — `yaml.safe_load()` parses the source.
2. **Duplicate key check** — custom `UniqueKeyLoader` raises on duplicate
   mapping keys (YAML silently takes the last value, which hides bugs).

Each broken file produces one record:

```python
{"file": "client_code/math.py", "line": 1,
 "message": "ZeroDivisionError: division by zero", "content": "<full source>"}
```

### STEP 3 — Ask Copilot to fix (`build_fix_prompt`, `copilot_fix_files`)

All fixes share **one** Copilot session (`CopilotClient.create_session`)
with `PermissionHandler.approve_all`, so no manual approval pauses the run.

For each broken file a prompt is sent that says:
*"Fix ALL errors, return ONLY the complete corrected file."*

A single event listener collects the reply:

- `AssistantMessageData` → latest assistant text (kept in `response_text`)
- `SessionIdleData` → model finished; an `asyncio.Event` releases the wait

### STEP 4 — Verify each fix (`accept_fix`, `strip_markdown_fences`)

A candidate fix must pass type-specific validation or it is dropped:

| File type | Validation | Rejects |
|---|---|---|
| `.py` | `ast.parse()` | Syntax errors in the "fix" |
| `.tf` | `terraform validate` (copies full root module to temp dir) | Invalid terraform |
| `.yaml` | `yaml.safe_load()` + `UniqueKeyLoader` | Syntax errors or duplicate keys |

Additional rejection gates:

| Gate | Rejects |
|---|---|
| Non-empty | Copilot returned nothing |
| Different from original | Copilot echoed the input back |

`strip_markdown_fences` removes stray ```` ```python ```` wrappers first.

If a reply comes back **empty** (the model sometimes answers by editing the
file with its own tools instead of sending text), the healer:
1. checks whether the file on disk changed — if Copilot fixed it in place,
   that on-disk version is used;
2. otherwise retries the prompt once (`MAX_FIX_ATTEMPTS = 2`);
3. only then gives up on that file and moves on to the rest.

### STEP 5 — Ship one PR (`BuildHealer.raise_fix_pr`)

1. Create branch `self-heal/run-<unix-timestamp>` from the target branch
   (`SOURCE_BRANCH` env var, else the repo default).
2. For each accepted fix: `update_file` if the file exists on GitHub,
   otherwise `create_file`.
3. Open **one** PR titled `[Self-Heal] Fix errors in N file(s)` listing every
   fixed file and its original error.

## 5. Safety rules

**Rule 1 — scan roots only**, fenced by one helper:

```python
def is_healable_file(path: str) -> bool:
    # True only when *path* sits inside a SCAN_ROOT and matches its
    # file type (e.g. .py under client_code/, .tf/.yaml under infra/terraform/).
```

Every read/write path passes through this check — the healer can never
touch anything outside the configured roots.

**Rule 2 — changed files first**: `get_changed_files()` filters by git
status, so files you did not touch are never scanned, fixed, or committed.
If tracking finds *nothing* (e.g. all your changes are already committed),
it falls back to a full scan of `client_code/` + `infra/terraform/` so
broken files are still healed.

Result: the healer can never read, modify, or commit anything outside
`client_code/` and `infra/terraform/`, and never touches untouched files
inside them.

## 6. Example run

```
2026-08-25 09:00:00 [INFO] Checking 5 changed file(s)...
2026-08-25 09:00:00 [INFO]   BROKEN client_code/app.py:9 — SyntaxError: '(' was never closed
2026-08-25 09:00:01 [INFO]   OK     client_code/config_parser.py
2026-08-25 09:00:10 [INFO]   BROKEN infra/terraform/modules/s3/main.tf:11 — Unclosed configuration block
2026-08-25 09:00:10 [INFO]   OK     infra/terraform/infra.tf
2026-08-25 09:00:11 [INFO]   OK     infra/terraform/config_env/dev/config.yaml
...
2026-08-25 09:00:20 [INFO] Fix verified OK for client_code/app.py
2026-08-25 09:00:35 [INFO] Fix verified OK for infra/terraform/modules/s3/main.tf
2026-08-25 09:00:36 [INFO] Creating single PR with 2 fixed file(s)...
2026-08-25 09:00:37 [INFO] Done! PR: https://github.com/owner/repo/pull/42
```

## 7. GitHub Actions wiring (`.github/workflows/`)

CI is modularized into reusable workflows. All checks run independently —
a failure in one does not block the others.

```
  PR touching client_code/ or infra/terraform/
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ ci.yml — "CI Build"                                     │
│                                                         │
│   yaml (check-yaml.yml)                                 │
│     └─ YAML syntax + duplicate key check                │
│                                                         │
│   terraform (check-terraform.yml)  needs: yaml          │
│     └─ terraform init + validate via Makefile            │
│                                                         │
│   python (check-python.yml)  needs: terraform           │
│     └─ syntax + runtime check on changed .py files      │
│                                                         │
│   All jobs run even if predecessors fail (always())     │
│   (skips self-heal/* branches so it can't loop)        │
└─────────────────────────────────────────────────────────┘
        │ any job fails?
        ▼
┌─────────────────────────────────────────────────────────┐
│ self-heal.yml — "Self-Heal Build Failure"               │
│   runs only when CI Build failed                        │
│   checks out the failing branch, sets                   │
│   CHANGE_BASE=origin/<default-branch>,                  │
│   then: python src/healer.py                            │
│   → Copilot fixes the changed broken files, ONE PR      │
└─────────────────────────────────────────────────────────┘
```

Required repository secrets: `HEALER_TOKEN` (PAT) and
`COPILOT_OAUTH_TOKEN`.
