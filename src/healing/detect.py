"""STEP 1+2 — Check files for syntax errors, undefined names, terraform errors, crashes."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

from .config import RUN_TIMEOUT_SECONDS, logger

# Cache terraform results so we only init+validate once per root module
_terraform_cache: dict[str, Optional[dict]] = {}


def find_broken_files(files: list[Path]) -> list[dict]:
    """
    Check the given files and return ONE entry per broken file:

        { "file": str, "line": int, "message": str, "content": str }

    Checks are routed by file type, cheapest first:
      .py  — syntax (ast) → undefined names (pyflakes) → run the file
      .tf  — terraform init + validate (one call per root module)
    A file that fails an earlier check skips the later ones.
    """
    broken = []
    logger.info("Checking %d changed file(s)...", len(files))

    # Separate files by type
    py_files = [f for f in files if f.suffix == ".py"]
    tf_files = [f for f in files if f.suffix == ".tf"]
    yaml_files = [f for f in files if f.suffix in (".yaml", ".yml")]

    # --- Python checks (per-file) ---
    for filepath in py_files:
        source = filepath.read_text()
        problem = (
            _syntax_problem(filepath, source)
            or _undefined_name_problem(filepath)
            or _runtime_problem(filepath)
        )
        if problem:
            logger.info("  BROKEN %s:%d — %s", filepath, problem["line"], problem["message"])
            broken.append({
                "file": str(filepath),
                "line": problem["line"],
                "message": problem["message"],
                "content": source,
            })
        else:
            logger.info("  OK     %s", filepath)

    # --- Terraform checks (one validate per root module) ---
    if tf_files:
        tf_problems = _terraform_problems(tf_files)
        # Check ALL .tf files on disk, not just the ones that changed
        all_tf = [f for f in files if f.suffix == ".tf"]
        for filepath in all_tf:
            source = filepath.read_text()
            problem = tf_problems.get(str(filepath))
            if problem:
                logger.info("  BROKEN %s:%d — %s", filepath, problem["line"], problem["message"])
                broken.append({
                    "file": str(filepath),
                    "line": problem["line"],
                    "message": problem["message"],
                    "content": source,
                })
            else:
                logger.info("  OK     %s", filepath)

    # --- YAML checks (per-file) ---
    for filepath in yaml_files:
        source = filepath.read_text()
        problem = _yaml_problem(filepath, source)
        if problem:
            logger.info("  BROKEN %s:%d — %s", filepath, problem["line"], problem["message"])
            broken.append({
                "file": str(filepath),
                "line": problem["line"],
                "message": problem["message"],
                "content": source,
            })
        else:
            logger.info("  OK     %s", filepath)

    return broken


def _find_tf_root(start: Path) -> Path:
    """Walk up from *start* to the nearest directory containing providers.tf or main.tf."""
    d = start
    while d != d.parent:
        if any((d / p).exists() for p in ("providers.tf", "main.tf", "terraform.tf")):
            return d
        d = d.parent
    return start


def _terraform_problems(tf_files: list[Path]) -> dict[str, dict]:
    """
    Run `terraform init` + `terraform validate -json` once per root module,
    then map diagnostics back to ALL `.tf` files under that root (not just
    the ones that were changed).

    Returns { file_path_str: {"line": int, "message": str} } for broken files only.
    """
    # Check if terraform is available
    try:
        subprocess.run(["terraform", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.warning("terraform CLI not installed — skipping terraform check")
        return {}

    # Group files by root module
    roots: dict[Path, list[Path]] = {}
    for f in tf_files:
        root = _find_tf_root(f)
        roots.setdefault(root, []).append(f)

    results: dict[str, dict] = {}

    for root_dir, _files_in_root in roots.items():
        cache_key = str(root_dir)
        if cache_key in _terraform_cache:
            diagnostics = _terraform_cache[cache_key]
        else:
            diagnostics = _run_terraform_validate(root_dir)
            _terraform_cache[cache_key] = diagnostics

        if diagnostics is None:
            continue

        # Collect ALL .tf files under this root (not just the changed ones)
        all_tf_in_root = sorted(
            p for p in root_dir.rglob("*.tf") if p.suffix == ".tf"
        )

        for diag in diagnostics:
            diag_file = diag.get("file", "")
            if not diag_file:
                continue
            diag_abs = (root_dir / diag_file).resolve()
            for f in all_tf_in_root:
                try:
                    if f.resolve() == diag_abs:
                        rel = str(f)
                        if rel not in results:
                            results[rel] = diag
                        break
                except Exception:
                    pass

    return results


def _run_terraform_validate(tf_dir: Path) -> Optional[list[dict]]:
    """
    Run `make ENV=dev terraform/validate` in *tf_dir* via the Makefile.
    Also captures errors from `terraform init` which catches syntax errors
    across all modules. Falls back to direct terraform commands if make fails.
    Returns a list of error dicts [{"file": str, "line": int, "message": str}],
    or None if the module is valid.
    """
    import os as _os
    env = _os.environ.get("ENV", "dev")

    all_errors: list[dict] = []

    # Run terraform init first — it catches syntax errors in all files
    init = subprocess.run(
        ["terraform", "init", "-backend=false"],
        cwd=tf_dir, capture_output=True, text=True, timeout=60,
    )
    if init.returncode != 0:
        all_errors.extend(_parse_terraform_text(init.stdout + init.stderr))

    # Also run validate — it catches additional errors like unclosed strings
    # that init doesn't report. Filter out "Module not installed" cascading errors.
    proc = subprocess.run(
        ["terraform", "validate", "-json"],
        cwd=tf_dir, capture_output=True, text=True, timeout=30,
    )
    json_errors = _parse_terraform_json(proc.stdout, proc.stderr)
    if json_errors:
        all_errors.extend(json_errors)

    return all_errors if all_errors else None


def _parse_terraform_json(stdout: str, stderr: str = "") -> Optional[list[dict]]:
    """Parse terraform validate -json output into a list of error dicts."""
    try:
        result = json.loads(stdout)
        if result.get("valid", True):
            return None

        errors = []
        for diag in result.get("diagnostics", []):
            if diag.get("severity") != "error":
                continue
            summary = diag.get("summary", "invalid terraform")
            # Skip cascading errors from init failure
            if "Module not installed" in summary:
                continue
            detail = diag.get("detail", "")
            message = f"{summary}: {detail}" if detail else summary
            rng = diag.get("range", {})
            start = rng.get("start", {})
            errors.append({
                "file": rng.get("filename", ""),
                "line": int(start.get("line", 1)),
                "message": message,
            })
        return errors if errors else None

    except Exception:
        return _parse_terraform_text(stdout + stderr)


def _parse_terraform_text(output: str) -> Optional[list[dict]]:
    """Fallback parser for terraform init stderr — extracts file + line errors."""
    # Strip ANSI escape codes from terraform output
    clean = re.sub(r'\x1b\[[0-9;]*m', '', output)
    errors = []
    lines = clean.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        file_match = re.search(r'on (\S+) line (\d+)', line)
        if file_match:
            summary = ""
            # Look backwards for the "Error:" line
            for j in range(i - 1, max(i - 5, -1), -1):
                if "Error:" in lines[j]:
                    summary = lines[j].strip().replace("Error: ", "")
                    break
            # Skip cascading errors — they only appear because of primary syntax errors
            if "Module not installed" in summary:
                i += 1
                continue
            errors.append({
                "file": file_match.group(1),
                "line": int(file_match.group(2)),
                "message": summary or "terraform error",
            })
        i += 1
    return errors if errors else None


def _syntax_problem(filepath: Path, source: str) -> Optional[dict]:
    """Return {'line', 'message'} if the file cannot be parsed, else None."""
    try:
        ast.parse(source, filename=str(filepath))
        return None
    except SyntaxError as err:
        return {"line": err.lineno or 1, "message": f"SyntaxError: {err.msg}"}


def _yaml_problem(filepath: Path, source: str) -> Optional[dict]:
    """Return {'line', 'message'} if the YAML is invalid or has duplicate keys."""
    try:
        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def check_unique_keys(loader, node):
            keys = []
            for key_node, _ in node.value:
                key = loader.construct_object(key_node)
                if key in keys:
                    raise yaml.YAMLError(
                        f"duplicate key: {key} (line {key_node.start_mark.line + 1})"
                    )
                keys.append(key)
            return loader.construct_mapping(node)

        UniqueKeyLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, check_unique_keys)

        yaml.load(source, Loader=UniqueKeyLoader)
        return None
    except yaml.YAMLError as err:
        line = getattr(err, "problem_mark", None)
        line_num = line.line + 1 if line is not None else 1
        message = str(err).split("\n")[0] if str(err) else "invalid YAML"
        return {"line": line_num, "message": f"YAML error: {message}"}


def _undefined_name_problem(filepath: Path) -> Optional[dict]:
    """
    Static analysis (pyflakes): catch bugs that don't crash when the file
    runs — e.g. an undefined variable inside an 'if' branch that is never
    taken. Only 'undefined name' findings count; style warnings like
    'imported but unused' are ignored. Skips silently if pyflakes is not
    installed.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(filepath)],
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        logger.warning("pyflakes not installed — skipping static check")
        return None

    match = re.search(r":(\d+):\d+:? undefined name '([^']+)'", proc.stdout)
    if not match:
        return None
    return {
        "line": int(match.group(1)),
        "message": f"undefined name '{match.group(2)}'",
    }


def _runtime_problem(filepath: Path) -> Optional[dict]:
    """Run the file and return {'line', 'message'} if it crashes, else None."""
    proc = subprocess.run(
        ["python3", str(filepath)],
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if proc.returncode == 0:
        return None

    stderr = proc.stderr.strip()

    error_match = re.search(r"(\w+Error|\w+Exception):\s*(.+)", stderr)
    line_match = re.search(r'File "[^"]+", line (\d+)', stderr)

    if not error_match:
        # Crash we could not parse — report the last stderr line as-is.
        last_line = stderr.splitlines()[-1] if stderr else "non-zero exit"
        return {"line": 1, "message": last_line}

    line_num = int(line_match.group(1)) if line_match else 1
    return {
        "line": line_num,
        "message": f"{error_match.group(1)}: {error_match.group(2)}",
    }
