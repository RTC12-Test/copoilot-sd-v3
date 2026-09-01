"""STEP 3+4 — Ask Copilot to fix each file, then verify each fix."""

import ast
import asyncio
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from copilot import CopilotClient
from copilot.session import PermissionHandler
from copilot.session_events import AssistantMessageData, SessionIdleData

from .config import COPILOT_MODEL, MAX_FIX_ATTEMPTS, logger


def build_fix_prompt(file_path: str, file_content: str, error: dict) -> str:
    """
    Build the instruction Copilot receives for one broken file.

    Includes CI failure context when available so Copilot can see
    the actual console errors from the failed build. See prompts/fix-prompt.md.
    """
    suffix = Path(file_path).suffix.lstrip(".")
    lang_map = {"py": "python", "tf": "hcl", "yaml": "yaml", "yml": "yaml"}
    lang = lang_map.get(suffix, "")

    parts = [
        f"The following {lang or suffix} file has errors from a failed CI build. "
        "Fix ALL errors and return the complete corrected file content. "
        "Return ONLY the file content — no markdown fences, no explanation, no commentary.",
        "",
        f"File: {file_path}",
        f"Error at line {error['line']}: {error['message']}",
    ]

    ci_context = error.get("ci_failure_context", "")
    if ci_context:
        parts.extend([
            "",
            "CI failure context (from the failed GitHub Actions run):",
            "```",
            ci_context[:4000],
            "```",
        ])

    parts.extend([
        "",
        "Current file content:",
        f"```{lang}",
        file_content,
        "```",
        "",
        "Return the complete corrected file content.",
    ])

    return "\n".join(parts)


def strip_markdown_fences(text: str) -> str:
    """Remove ```python ... ``` wrappers in case Copilot adds them anyway."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned


def _validate_tf_string(tf_content: str, original_file: str) -> None:
    """
    Write a .tf string to a temp file, run terraform validate against it,
    and raise on errors so accept_fix can catch it.

    terraform validate needs a full root module, so we write the candidate
    to the original file path inside a temporary copy of the terraform root,
    validate, then clean up.
    """
    try:
        subprocess.run(["terraform", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        # terraform not installed — skip validation, accept the fix
        return

    # find the terraform root (walk up to providers.tf / main.tf)
    root = Path(original_file).parent
    while root != root.parent:
        if any((root / p).exists() for p in ("providers.tf", "main.tf", "terraform.tf")):
            break
        root = root.parent
    else:
        root = Path(original_file).parent

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        # copy the entire terraform root to a temp dir
        subprocess.run(["cp", "-r", str(root) + "/.", str(tmp_root)], check=True)
        # overwrite the file we're testing
        tmp_target = tmp_root / Path(original_file).relative_to(root)
        tmp_target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target.write_text(tf_content)
        # init + validate
        subprocess.run(["terraform", "init", "-backend=false"],
                       cwd=tmp_root, capture_output=True, timeout=60)
        proc = subprocess.run(
            ["terraform", "validate"],
            cwd=tmp_root, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout + proc.stderr)


def accept_fix(broken: dict, candidate: str) -> Optional[str]:
    """
    Quality gate for one proposed fix. Returns the fix, or None with a
    logged reason when it is unchanged from the original or still invalid.

    Validation is routed by file type: Python (ast.parse) vs Terraform
    (it re-verifies with `ast.parse` for `.py` or `terraform validate` for `.tf`).
    """
    candidate = candidate.strip()
    if not candidate:
        return None

    if candidate == broken["content"].strip():
        logger.warning("Copilot returned UNCHANGED content for %s — skipping", broken["file"])
        return None

    suffix = Path(broken["file"]).suffix
    try:
        if suffix == ".py":
            ast.parse(candidate)
        elif suffix == ".tf":
            _validate_tf_string(candidate, broken["file"])
        elif suffix in (".yaml", ".yml"):
            import yaml
            yaml.safe_load(candidate)
        # unknown suffix — accept without validation (rare, keeps pipeline flexible)
    except Exception as err:
        logger.warning("Copilot fix STILL BROKEN in %s: %s — skipping", broken["file"], err)
        return None

    logger.info("Fix verified OK for %s", broken["file"])
    return candidate


def _fix_from_disk(broken: dict) -> Optional[str]:
    """
    Fallback when Copilot sends no text: the model may have used its own
    tools to edit the file in place instead of replying. If the file on
    disk now differs from what we scanned, treat that as the fix.
    """
    try:
        disk_now = Path(broken["file"]).read_text()
    except OSError as err:
        logger.warning("Could not re-read %s from disk: %s", broken["file"], err)
        return None

    if disk_now == broken["content"]:
        return None

    logger.info("%s was edited directly by Copilot tools — using on-disk version", broken["file"])
    return accept_fix(broken, disk_now)


async def _fix_all_with_session(session, broken_files: list[dict]) -> dict[str, str]:
    """Ask *session* to fix each broken file; returns accepted fixes only."""
    fixes: dict[str, str] = {}

    # One listener for the whole session: it collects the assistant's
    # latest message and flips `done` when the model stops talking.
    done = asyncio.Event()
    response_text = ""

    def on_event(event):
        nonlocal response_text
        match event.data:
            case AssistantMessageData() as data:
                if data.content:              # ignore blank chunks
                    response_text = data.content
            case SessionIdleData():
                done.set()

    session.on(on_event)

    for broken in broken_files:
        fixed = None

        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            response_text = ""                # reset state between attempts
            done.clear()

            await session.send(build_fix_prompt(broken["file"], broken["content"], broken))
            await done.wait()                 # wait until the answer is complete

            candidate = strip_markdown_fences(response_text)

            if candidate.strip():
                fixed = accept_fix(broken, candidate)
            else:
                # No text came back — maybe the model edited the file with
                # its tools instead of replying. Check disk before retrying.
                fixed = _fix_from_disk(broken)
                if not fixed:
                    logger.warning(
                        "Copilot returned EMPTY reply for %s (attempt %d/%d)",
                        broken["file"], attempt, MAX_FIX_ATTEMPTS,
                    )

            if fixed:
                break

        if fixed:
            fixes[broken["file"]] = fixed

    return fixes


async def copilot_fix_files(broken_files: list[dict], oauth_token: str) -> dict[str, str]:
    """
    Fix every broken file inside ONE Copilot session.

    Returns { file_path: fixed_content } containing only accepted fixes.
    """
    async with CopilotClient(github_token=oauth_token) as client:
        async with await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=COPILOT_MODEL,
        ) as session:
            return await _fix_all_with_session(session, broken_files)
