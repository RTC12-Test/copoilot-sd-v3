"""Monitor — detect failed GitHub Actions runs, gather console logs and changed files."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

import requests

from .config import SCAN_ROOTS, is_healable_file, logger


@dataclass
class FailedRun:
    run_id: int
    html_url: str
    head_branch: str
    head_sha: str
    head_repo: str
    head_owner: str
    console_logs: str = ""
    changed_files: list[str] = field(default_factory=list)
    job_failures: list[dict] = field(default_factory=list)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def get_ci_status_for_sha(token: str, repo_name: str, sha: str,
                          workflow_name: str = "CI Build") -> dict:
    """
    Return the conclusion ('success'/'failure'/None) and most recent run id
    for a given commit SHA on the given workflow, or {'conclusion': None}.

    Used to check whether a CI re-run of the auto-fix PR passed or failed.
    """
    owner, repo = repo_name.split("/", 1)
    api = f"https://api.github.com/repos/{owner}/{repo}"
    resp = requests.get(
        f"{api}/actions/runs",
        headers=_headers(token),
        params={"head_sha": sha, "status": "completed", "per_page": 5},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("Could not read CI status for %s: %s", sha, resp.status_code)
        return {"conclusion": None, "run_id": None}

    for run in resp.json().get("workflow_runs", []):
        name = (run.get("name") or "")
        if name == workflow_name or run.get("display_title", "").startswith("CI"):
            return {
                "conclusion": run.get("conclusion"),
                "run_id": run.get("id"),
                "status": run.get("status"),
            }

    # No matching workflow run found yet
    return {"conclusion": None, "run_id": None}


def gather_failed_run(token: str, repo_name: str, run_id: int) -> Optional[FailedRun]:
    """
    Fetch a failed workflow run, its console logs, job failures,
    and the list of changed files. Returns None if the run is not failed
    or cannot be accessed.
    """
    owner, repo = repo_name.split("/", 1)
    api = f"https://api.github.com/repos/{owner}/{repo}"

    # 1. Get run metadata
    logger.info("Fetching workflow run %d...", run_id)
    resp = requests.get(f"{api}/actions/runs/{run_id}", headers=_headers(token), timeout=30)
    resp.raise_for_status()
    run = resp.json()

    if run.get("conclusion") != "failure":
        logger.info("Run %d conclusion is %s — skipping", run_id, run.get("conclusion"))
        return None

    commit = run.get("head_commit") or {}
    head_sha = run.get("head_sha", "")
    head_branch = run.get("head_branch", "")
    head_repo = run.get("head_repository", {})
    head_owner = head_repo.get("full_name", repo_name) if head_repo else repo_name

    failed_run = FailedRun(
        run_id=run_id,
        html_url=run.get("html_url", ""),
        head_branch=head_branch,
        head_sha=head_sha,
        head_repo=head_repo.get("full_name", repo_name) if head_repo else repo_name,
        head_owner=owner,
    )

    # 2. Get failed jobs and their step-level failures
    logger.info("Fetching jobs for run %d...", run_id)
    jobs_resp = requests.get(
        f"{api}/actions/runs/{run_id}/jobs",
        headers=_headers(token),
        params={"per_page": 100},
        timeout=30,
    )
    jobs_resp.raise_for_status()
    jobs = jobs_resp.json().get("jobs", [])

    for job in jobs:
        if job.get("conclusion") != "failure":
            continue
        failed_steps = [
            s for s in job.get("steps", [])
            if s.get("conclusion") == "failure"
        ]
        failed_run.job_failures.append({
            "name": job.get("name", "unknown"),
            "html_url": job.get("html_url", ""),
            "failed_steps": [
                {"name": s.get("name", ""), "number": s.get("number", 0)}
                for s in failed_steps
            ],
        })

    # 3. Fetch console logs for failed jobs
    logger.info("Fetching console logs for %d failed job(s)...", len(failed_run.job_failures))
    log_lines = []
    for job_info in failed_run.job_failures:
        job_name = job_info["name"]
        # Find the job object to get the logs URL
        for job in jobs:
            if job.get("name") == job_name and job.get("conclusion") == "failure":
                logs_url = job.get("logs_url", "")
                if logs_url:
                    try:
                        logs_resp = requests.get(
                            logs_url,
                            headers={**_headers(token), "Accept": "application/vnd.github.v3+json"},
                            timeout=60,
                        )
                        if logs_resp.status_code == 200:
                            raw = logs_resp.text
                            log_lines.append(f"=== Job: {job_name} ===")
                            log_lines.append(_extract_relevant_log(raw))
                    except requests.RequestException as exc:
                        logger.warning("Failed to fetch logs for job %s: %s", job_name, exc)
                break

    failed_run.console_logs = "\n\n".join(log_lines)

    # 4. Get changed files in this run's commit
    logger.info("Fetching changed files for commit %s...", head_sha[:8])
    changed_resp = requests.get(
        f"{api}/commits/{head_sha}/files",
        headers=_headers(token),
        params={"per_page": 100},
        timeout=30,
    )
    if changed_resp.status_code == 200:
        files_data = changed_resp.json()
        failed_run.changed_files = [
            f["filename"]
            for f in files_data
            if f.get("status") in ("added", "modified", "renamed")
            and is_healable_file(f["filename"])
        ]
    else:
        logger.warning("Could not fetch changed files: %s", changed_resp.status_code)

    logger.info(
        "Run %d: branch=%s, failed_jobs=%d, changed_healable=%d",
        run_id, head_branch, len(failed_run.job_failures), len(failed_run.changed_files),
    )
    return failed_run


def _extract_relevant_log(raw: str) -> str:
    """
    Extract only the relevant error lines from raw CI logs.
    Strips verbose setup/install output, keeps error traces and
    the lines immediately around them.
    """
    lines = raw.splitlines()
    relevant = []
    error_keywords = re.compile(
        r"(Error|Exception|Traceback|FAILED|error:|fatal:|SyntaxError|"
        r"NameError|TypeError|ValueError|AttributeError|ImportError|"
        r"ModuleNotFoundError|KeyError|IndexError|ZeroDivisionError|"
        r"terraform.*error|yaml.*error|\.py:\d+)",
        re.IGNORECASE,
    )

    # Find lines with errors and include context (2 lines before, 5 after)
    i = 0
    while i < len(lines):
        if error_keywords.search(lines[i]):
            start = max(0, i - 2)
            end = min(len(lines), i + 6)
            context_block = lines[start:end]
            relevant.append("---")
            relevant.extend(context_block)
            i = end
        else:
            i += 1

    return "\n".join(relevant) if relevant else "\n".join(lines[-50:])


def filter_healable_files(changed_files: list[str]) -> list[str]:
    """Return only files that pass the safety fence."""
    return [f for f in changed_files if is_healable_file(f)]


def build_failure_summary(failed_run: FailedRun) -> str:
    """Build a human-readable summary of the failure for Copilot context."""
    parts = [
        f"## Failed Run #{failed_run.run_id}",
        f"**Branch:** {failed_run.head_branch}",
        f"**Commit:** {failed_run.head_sha[:8]}",
        f"**URL:** {failed_run.html_url}",
        "",
        "### Failed Jobs:",
    ]
    for job in failed_run.job_failures:
        parts.append(f"- **{job['name']}**")
        for step in job["failed_steps"]:
            parts.append(f"  - Step {step['number']}: {step['name']}")

    if failed_run.console_logs:
        parts.extend(["", "### Console Errors (relevant lines):", "```"])
        # Truncate logs to avoid overwhelming Copilot context
        logs = failed_run.console_logs[:8000]
        if len(failed_run.console_logs) > 8000:
            logs += "\n... (truncated)"
        parts.append(logs)
        parts.append("```")

    return "\n".join(parts)
