"""STEP 5 — Commit all fixes, open ONE pull request, and iteratively close
the loop until CI passes or max iterations are reached (PyGitHub)."""

import os
import time
import asyncio
from pathlib import Path

from github import Github, Auth, GithubException

from .config import (
    SCAN_ROOTS, is_healable_file, logger,
    MAX_CI_ITERATIONS, CI_RECHECK_SECONDS, CI_CHECK_TIMEOUT_SECONDS,
)
from .tracking import get_changed_files
from .detect import find_broken_files
from .copilot_fixer import copilot_fix_files


class BuildHealer:
    def __init__(self, token: str, oauth_token: str, repo_name: str):
        self.token = token
        self.oauth_token = oauth_token
        self.github = Github(auth=Auth.Token(token))
        self.repo = self.github.get_repo(repo_name)
        logger.info("Initialised healer for repo %s", repo_name)

    def _fix_from_monitored_run(self, failed_run) -> tuple[dict, list[dict]]:
        """
        Detect broken changed files from a FailedRun, ask Copilot for fixes,
        and return (fixes, broken_files). Returns ({}, []) if nothing to do.
        """
        from .monitor import build_failure_summary
        from pathlib import Path

        changed_files = []
        for f in failed_run.changed_files:
            p = Path(f)
            if p.is_file():
                changed_files.append(p)
            else:
                logger.warning("Changed file %s not found on disk — skipping", f)

        if not changed_files:
            logger.info("No healable files found on disk — nothing to do.")
            return {}, []

        logger.info("Checking %d changed file(s) from failed run...", len(changed_files))
        broken_files = find_broken_files(changed_files)
        if not broken_files:
            logger.info("Changed files all passed local checks — nothing to heal.")
            return {}, []

        # Enrich broken files with CI failure context so Copilot sees real errors.
        failure_summary = build_failure_summary(failed_run)
        for bf in broken_files:
            bf["ci_failure_context"] = failure_summary

        logger.info("Found %d broken file(s). Asking Copilot for fixes...", len(broken_files))
        fixes = asyncio.run(copilot_fix_files(broken_files, self.oauth_token))

        if not fixes:
            logger.warning("No usable fixes generated. Nothing to commit.")
            return {}, []

        return fixes, broken_files

    def run_from_failure(self, failed_run) -> None:
        """
        Agent mode: fix the changed files from a failed run, open a PR, then
        iteratively re-check CI; if the re-run still fails, re-analyze and
        update the same PR until it passes or MAX_CI_ITERATIONS is reached.
        """
        fixes, broken_files = self._fix_from_monitored_run(failed_run)
        if not fixes:
            return

        failure_summary = broken_files[0].get("ci_failure_context", "")
        logger.info("Creating PR with %d fixed file(s)...", len(fixes))
        pr = self._create_fix_pr(fixes, broken_files, failed_run.head_branch, failure_summary)
        logger.info("Done! PR: %s", pr.html_url)

        # ---- Close the loop: re-check CI and iterate until it passes ----
        self._iterate_on_pr(pr, failed_run.head_sha)

    def _iterate_on_pr(self, pr, initial_sha: str) -> None:
        """
        After opening a fix PR, wait for its CI re-run. If it succeeds, stop.
        If it fails, re-fetch the new failure, re-fix, update the same PR
        branch, and repeat up to MAX_CI_ITERATIONS.

        The actual iteration state lives on disk in the local repo (the branch
        is checked out), so each round re-runs the changed-file detection.
        """
        from .monitor import get_ci_status_for_sha

        current_sha = initial_sha
        for attempt in range(1, MAX_CI_ITERATIONS + 1):
            logger.info(
                "[iter %d/%d] Waiting for CI re-run on %s...",
                attempt, MAX_CI_ITERATIONS, current_sha[:8],
            )
            conclusion = self._wait_for_ci(current_sha)

            if conclusion == "success":
                logger.info("CI passed — healing complete. PR: %s", pr.html_url)
                return
            if conclusion is None:
                logger.warning("CI status unknown or not found — stopping iterations.")
                return

            # CI failed again — pull the new failing run's details.
            logger.info("[iter %d/%d] CI still failing — re-investigating.", attempt, MAX_CI_ITERATIONS)
            failed_run = self._recheck_run_details(current_sha)
            if failed_run is None:
                logger.warning("Could not retrieve failing run details — stopping.")
                return

            fixes, broken_files = self._fix_from_monitored_run(failed_run)
            if not fixes:
                logger.warning("[iter %d/%d] No new fixes found — giving up.", attempt, MAX_CI_ITERATIONS)
                return

            logger.info("[iter %d/%d] Applying %d more fix(es) to %s", attempt, MAX_CI_ITERATIONS,
                        len(fixes), pr.head.ref)
            self._update_pr_branch(fixes, pr)
            current_sha = pr.head.sha

        logger.warning("Max iterations (%d) reached without CI passing.", MAX_CI_ITERATIONS)

    def _wait_for_ci(self, sha: str) -> str | None:
        """Poll CI for *sha* until it completes or times out. Returns 'success',
        'failure', or None if unknown/timed out."""
        from .monitor import get_ci_status_for_sha

        deadline = time.time() + CI_CHECK_TIMEOUT_SECONDS
        while time.time() < deadline:
            state = get_ci_status_for_sha(self.token, self.repo.full_name, sha)
            if state.get("status") == "completed":
                conclusion = state.get("conclusion")
                logger.info("CI for %s concluded: %s", sha[:8], conclusion)
                return "success" if conclusion == "success" else "failure"
            if state.get("conclusion"):
                conclusion = state["conclusion"]
                logger.info("CI for %s concluded: %s", sha[:8], conclusion)
                return "success" if conclusion == "success" else "failure"
            time.sleep(CI_RECHECK_SECONDS)
        logger.warning("Timed out waiting for CI on %s", sha[:8])
        return None

    def _recheck_run_details(self, sha: str):
        """Fetch the most recent failed run for a commit and return a FailedRun."""
        from .monitor import get_ci_status_for_sha, gather_failed_run

        state = get_ci_status_for_sha(self.token, self.repo.full_name, sha)
        run_id = state.get("run_id")
        if not run_id:
            return None
        return gather_failed_run(self.token, self.repo.full_name, run_id)

    def _create_fix_pr(self, fixes: dict[str, str], all_errors: list[dict],
                       source_branch: str = None, failure_summary: str = ""):
        """Open ONE PR with all fixes. Returns the PR object."""
        repo = self.repo
        target_branch = source_branch or repo.default_branch
        base = repo.get_branch(target_branch)

        branch = f"self-heal/run-{int(time.time())}"
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base.commit.sha)

        self._commit_fixes_to_branch(fixes, branch)

        files_list = "\n".join(f"- `{p}`" for p in fixes.keys())
        error_summary = "\n".join(
            f"- `{e['file']}:{e['line']}` — {e['message']}" for e in all_errors
        )

        body_parts = [
            "## Automated Remediation\n",
            f"**Fixed files:**\n{files_list}\n",
            f"**Errors:**\n{error_summary}\n",
        ]
        if failure_summary:
            body_parts.append(failure_summary + "\n")
        body_parts.append("Fix generated by **GitHub Copilot SDK** and auto-committed.")

        return repo.create_pull(
            title=f"[Self-Heal] Fix errors in {len(fixes)} file(s)",
            body="\n".join(body_parts),
            head=branch,
            base=target_branch,
        )

    def _update_pr_branch(self, fixes: dict[str, str], pr) -> None:
        """Commit additional fixes directly onto an existing PR's branch."""
        self._commit_fixes_to_branch(fixes, pr.head.ref)

    def _commit_fixes_to_branch(self, fixes: dict[str, str], branch: str) -> None:
        """Write the given fixes onto *branch* (create or update each file)."""
        repo = self.repo
        for path, new_content in fixes.items():
            if not is_healable_file(path):
                logger.warning("Refusing to touch %s — not in any scan root", path)
                continue
            try:
                file_data = repo.get_contents(path, ref=branch)
                if isinstance(file_data, list):
                    continue
                repo.update_file(
                    path=path,
                    message=f"self-heal: fix errors in {path}",
                    content=new_content,
                    sha=file_data.sha,
                    branch=branch,
                )
            except GithubException:
                repo.create_file(
                    path=path,
                    message=f"self-heal: fix errors in {path}",
                    content=new_content,
                    branch=branch,
                )
            # Also refresh the local working-tree file so subsequent iteration
            # rounds re-detect against the FIXED content, not the original.
            try:
                local = Path(path)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(new_content)
            except OSError as err:
                logger.warning("Could not refresh local file %s: %s", path, err)

    def run(self):
        """Local mode: run the full pipeline end-to-end."""
        changed_files = get_changed_files()
        if not changed_files:
            logger.info("No changed files in scan roots — nothing to do.")
            return

        broken_files = find_broken_files(changed_files)
        if not broken_files:
            logger.info("Changed files are all OK. Nothing to heal!")
            return

        logger.info("Found %d broken file(s). Asking Copilot for fixes...", len(broken_files))
        fixes = asyncio.run(copilot_fix_files(broken_files, self.oauth_token))

        if not fixes:
            logger.warning("No usable fixes generated. Nothing to commit.")
            return

        target = os.environ.get("SOURCE_BRANCH") or self.repo.default_branch
        logger.info("Creating single PR with %d fixed file(s), targeting %s...", len(fixes), target)
        pr = self._create_fix_pr(fixes, broken_files, os.environ.get("SOURCE_BRANCH"))
        logger.info("Done! PR: %s", pr.html_url)
