"""Self-healing pipeline package — one module per step.

    healing.config          shared constants, logger, safety fence
    healing.monitor         detect failed CI runs, gather logs + changed files (webhook)
    healing.tracking        STEP 0  which client_code/ files changed (git, local mode)
    healing.detect          STEP 1+2 syntax / pyflakes / runtime checks
    healing.copilot_fixer   STEP 3+4 Copilot prompt + fix verification
    healing.pull_request    STEP 5   branch, commit, single PR
"""
