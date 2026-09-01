"""STEP 0 — Track which healable files (client_code/, infra/terraform/) changed, via git."""

import os
import subprocess
from pathlib import Path

from .config import SCAN_ROOTS, is_healable_file, logger


def _all_scan_files() -> list[Path]:
    """Every healable file currently on disk across all SCAN_ROOTS."""
    out = []
    for root, exts in SCAN_ROOTS.items():
        root_path = Path(root)
        if root_path.exists():
            out += sorted(p for p in root_path.rglob("*") if p.suffix in exts)
    return out


def _split_changed_and_deleted(entries: list[tuple[str, str]]) -> tuple[set[Path], set[Path]]:
    """
    Turn raw (status_code, path) pairs into two sets of healable paths:

      changed  — added / modified / RENAMED files (a rename is tracked by its
                 NEW name, so renamed files are still healed)
      deleted  — removed files (nothing left to heal, so they are skipped)

    status codes come from git: D=deleted, R=rename, A=added, M=modified...
    """
    changed: set[Path] = set()
    deleted: set[Path] = set()

    for status, raw in entries:
        if not is_healable_file(raw):
            continue
        if "D" in status:
            deleted.add(Path(raw))
        else:
            changed.add(Path(raw))

    return changed, deleted


def _paths_from_git_status(output: str) -> tuple[set[Path], set[Path]]:
    """Parse `git status --porcelain` output into (changed, deleted)."""
    entries = []
    for line in output.splitlines():
        # porcelain format: "XY path" — X=staged state, Y=worktree state
        status, raw = line[:2], line[3:].strip().strip('"')
        if " -> " in raw:                 # renames: "old -> new" → keep NEW
            raw = raw.split(" -> ", 1)[1]
        entries.append((status, raw))
    return _split_changed_and_deleted(entries)


def _paths_from_git_diff(output: str) -> tuple[set[Path], set[Path]]:
    """Parse `git diff --name-status` output into (changed, deleted)."""
    entries = []
    for line in output.splitlines():
        # name-status format: "<STATUS>\t<path>" and for renames
        # "R100\t<old>\t<new>" — the path we want is always the LAST field
        parts = line.split("\t")
        if len(parts) >= 2:
            entries.append((parts[0], parts[-1].strip()))
    return _split_changed_and_deleted(entries)


def _existing_only(paths: list[Path]) -> list[Path]:
    """
    Final safety net: drop anything missing from disk so the scanner can
    never hit FileNotFoundError (e.g. a file deleted after git ran).
    """
    kept = []
    for path in paths:
        if path.is_file():
            kept.append(path)
        else:
            logger.warning("%s disappeared before scanning — skipping", path)
    return kept


def get_changed_files() -> list[Path]:
    """
    Return the healable files you changed (client_code/, infra/terraform/),
    discovered from git.

      Mode 1 — LOCAL:  uncommitted edits/new files/renames (git status)
      Mode 2 — CI:     when there are no local edits and CHANGE_BASE is set
                       (e.g. origin/main), use the commits between
                       CHANGE_BASE and HEAD instead
      Fallback — if git fails OR tracking finds nothing (e.g. everything is
                 already committed), return every scan-root file so nothing
                 broken is missed

    Renamed files are healed under their NEW name. Deleted files are
    skipped — there is nothing left on disk to heal.
    """
    pathspecs = list(SCAN_ROOTS)
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", *pathspecs],
            capture_output=True,
            text=True,
            timeout=10,
        )
        proc.check_returncode()
        changed, deleted = _paths_from_git_status(proc.stdout)
    except Exception as err:
        logger.warning("git status failed (%s) — falling back to ALL files", err)
        return _all_scan_files()

    base = os.environ.get("CHANGE_BASE", "").strip()
    if base.endswith("/") or base.endswith("..."):
        # e.g. a workflow expression resolved to just "origin/" — unusable
        logger.warning("CHANGE_BASE=%r looks invalid — ignoring CI diff mode", base)
        base = ""

    if not changed and not deleted and base:
        logger.info("No local edits — diffing %s...HEAD (CI mode)", base)
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-status", f"{base}...HEAD", "--", *pathspecs],
                capture_output=True,
                text=True,
                timeout=10,
            )
            proc.check_returncode()
        except Exception as err:
            logger.warning(
                "git diff against %r failed (%s) — falling back to ALL files",
                base, err,
            )
            return _all_scan_files()

        changed, deleted = _paths_from_git_diff(proc.stdout)

    for path in sorted(deleted):
        logger.info("Skipping %s — deleted", path)

    if not changed:
        # Tracking came up empty (e.g. everything already committed and
        # pushed). Fall back to a full scan so broken files are still healed.
        logger.warning(
            "No changed files found via git — falling back to scanning ALL files"
        )
        return _all_scan_files()

    return _existing_only(sorted(changed))
