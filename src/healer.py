"""
Self-Healing Agent — GitHub Actions CI Monitor + Copilot Fixer

Detects failed CI runs via GitHub webhooks (workflow_run event), gathers
console logs and changed files, asks Copilot to fix them, and raises a PR.

Architecture:
  1. MONITOR  (monitor.py)   — fetch failed run info, console logs, changed files
  2. DETECT   (detect.py)    — validate changed files locally
  3. FIX      (copilot_fixer.py) — ask Copilot to repair broken files
  4. SHIP     (pull_request.py) — branch, commit, single PR

Usage:
  Set environment variables:
    GITHUB_TOKEN  - GitHub PAT with repo + workflow permissions
    GH_TOKEN      - GitHub OAuth token for Copilot SDK (gho_...)
    GITHUB_REPO   - owner/repo to monitor

  Optional:
    FAILED_RUN_ID - workflow run ID to heal (set by webhook trigger)
    SOURCE_BRANCH - branch that failed (defaults to repo default branch)

  python src/healer.py
"""

import os
import sys
import logging

from healing.pull_request import BuildHealer
from healing.monitor import gather_failed_run, filter_healable_files, build_failure_summary
from healing.config import logger


def main():
    token = os.environ.get("GITHUB_TOKEN")
    oauth_token = os.environ.get("GH_TOKEN")
    repo_name = os.environ.get("GITHUB_REPO")

    if not token or not oauth_token or not repo_name:
        logger.error(
            "Set GITHUB_TOKEN, GH_TOKEN, and GITHUB_REPO environment variables."
        )
        raise SystemExit(1)

    run_id = os.environ.get("FAILED_RUN_ID")

    if run_id:
        # --- Agent mode: triggered by workflow_run webhook ---
        run_id = int(run_id)
        logger.info("Agent triggered by workflow_run webhook — run_id=%d", run_id)

        failed_run = gather_failed_run(token, repo_name, run_id)
        if failed_run is None:
            logger.info("Run %d is not failed or inaccessible — nothing to do.", run_id)
            return

        if not failed_run.changed_files:
            logger.info("No healable files changed in run %d — nothing to fix.", run_id)
            return

        logger.info(
            "Run %d failed on branch '%s' — %d healable file(s) changed",
            run_id, failed_run.head_branch, len(failed_run.changed_files),
        )

        healer = BuildHealer(token, oauth_token, repo_name)
        healer.run_from_failure(failed_run)

    else:
        # --- Manual mode: scan local repo (original behavior) ---
        logger.info("No FAILED_RUN_ID set — running local scan mode")
        healer = BuildHealer(token, oauth_token, repo_name)
        healer.run()


if __name__ == "__main__":
    main()
