"""Shared configuration and the multi-root safety fence."""

import logging
from pathlib import Path, PurePosixPath

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("self-healer")

# Configuration — change these values to retune the whole pipeline

# Folders the healer may touch, with the file types inside each.
# Add a new folder here to let the healer scan/heal it too.
SCAN_ROOTS = {
    "client_code": {".py"},
    "infra/terraform": {".tf", ".yaml"},
}

RUN_TIMEOUT_SECONDS = 10         # max seconds any scanned file may run
COPILOT_MODEL = "gpt-5"          # model used to generate fixes
MAX_FIX_ATTEMPTS = 2             # tries per file before giving up on it

# Monitor settings
MONITOR_LOG_MAX_CHARS = 8000     # max CI console log chars sent to Copilot
HEALABLE_EXTENSIONS = {".py", ".tf", ".yaml", ".yml"}  # all file types the agent handles

# Close-the-loop iteration settings
MAX_CI_ITERATIONS = 3            # how many fix→re-check cycles before giving up
CI_RECHECK_SECONDS = 30          # how often to poll the CI run status
CI_CHECK_TIMEOUT_SECONDS = 1800  # max seconds to wait for a CI re-run to finish


def is_healable_file(path: str) -> bool:
    """
    Safety fence: True only when *path* lives INSIDE one of the SCAN_ROOTS
    and matches that root's file type (e.g. .py under client_code/,
    .tf under infra/terraform/).

    Every read/write path passes through this check, so the healer can
    never touch anything outside the configured roots.
    """
    p = PurePosixPath(path)
    if len(p.parts) < 2:
        return False
    for root, exts in SCAN_ROOTS.items():
        r = PurePosixPath(root)
        if (
            p.suffix in exts
            and len(p.parts) > len(r.parts)
            and p.parts[: len(r.parts)] == r.parts
        ):
            return True
    return False
