#!/usr/bin/env python3
"""Stage 2 non-skippable CI runner with isolated base comparison, build check, and 3-layer untrusted code protection."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import ast
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from typing import Mapping, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import (  # noqa: E402
    ALLOWED_STAGE1_SKIP_STATUSES,
    detect_ci_script_hazard_in_files,
    emit_json,
    extract_failed_gh_check_names,
    fail_with_code,
    find_stage2_test_failures,
    is_stage1_passed_or_skipped,
    prune_created_project_dirs,
    read_verified_gate_state,
    reset_stage5_state,
    resolve_session_root,
    run_subproc,
    sha256_file,
    unlink_symlinks_under,
    utc_now_iso,
    write_sealed_gate_state,
)

DEFAULT_CHECK_TIMEOUT_SECONDS = 120
GH_TIMEOUT_SECONDS = 30
SUMMARY_TAIL_LINES = 40

CONFLICT_MARKER_RE = re.compile(r"^(?:<{7}\s|={7}$|>{7}\s)", re.MULTILINE)
DEFAULT_NPM_TEST_PLACEHOLDER = "no test specified"

GH_FAILED_TOKENS: frozenset[str] = frozenset(
    {
        "FAILURE",
        "FAILED",
        "ERROR",
        "TIMED_OUT",
        "CANCELLED",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "STALE",
    }
)
GH_PENDING_TOKENS: frozenset[str] = frozenset(
    {
        "IN_PROGRESS",
        "QUEUED",
        "PENDING",
        "WAITING",
        "REQUESTED",
        "EXPECTED",
    }
)
GH_PASSED_TOKENS: frozenset[str] = frozenset(
    {
        "SUCCESS",
        "PASSED",
        "NEUTRAL",
        "SKIPPED",
    }
)


def find_python_test_files(workdir: Path) -> list[str]:
    matches: list[str] = []
    ignored = {".git", ".gemini", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
    for root, dirs, files in os.walk(workdir, topdown=True, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ignored and not (Path(root) / d).is_symlink()]
        rel_root = Path(root).relative_to(workdir)
        for fname in sorted(files):
            if (fname.startswith("test_") and fname.endswith(".py")) or fname.endswith("_test.py"):
                rel = (rel_root / fname).as_posix() if str(rel_root) != "." else fname
                matches.append(rel)
    return matches


def resolve_bin_in_env(binary: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(binary, path=env.get("PATH"))


def detect_pkg_manager(workdir: Path, env: Mapping[str, str]) -> str | None:
    candidates: list[tuple[str, str]] = [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
    ]
    for lock_name, mgr in candidates:
        if (workdir / lock_name).is_file() and resolve_bin_in_env(mgr, env):
            return mgr
    if resolve_bin_in_env("npm", env):
        return "npm"
    for _, mgr in candidates:
        if resolve_bin_in_env(mgr, env):
            return mgr
    return None


def discover_check_specs(
    workdir: Path,
    env: Mapping[str, str],
) -> list[tuple[str, list[str] | None, str]]:
    """Return list of (tool_label, command_argv_or_None, reason_if_none)."""
    specs: list[tuple[str, list[str] | None, str]] = []
    has_test = False
    has_lint = False
    has_typecheck = False
    has_build = False
    pkg_test_script = ""

    pkg_path = workdir / "package.json"
    if pkg_path.is_file():
        try:
            pkg_raw: object = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pkg_raw = None
        if isinstance(pkg_raw, dict) and isinstance(pkg_raw.get("scripts"), dict):
            scripts = {str(k): str(v) for k, v in pkg_raw["scripts"].items() if isinstance(v, str)}
            mgr = detect_pkg_manager(workdir, env)

            for t_key in ("test:ci", "test", "test:unit"):
                if t_key in scripts:
                    val = scripts[t_key]
                    if DEFAULT_NPM_TEST_PLACEHOLDER in val:
                        break
                    pkg_test_script = val
                    has_test = True
                    if mgr is None:
                        specs.append(("test", None, "env_unavailable: Node package manager (npm/pnpm/yarn) not found"))
                    else:
                        specs.append(("test", [mgr, "run", t_key], ""))
                    break

            for l_key in ("lint", "check:lint", "eslint"):
                if l_key in scripts:
                    has_lint = True
                    if mgr is None:
                        specs.append(("lint", None, "env_unavailable: Node package manager not found"))
                    else:
                        specs.append(("lint", [mgr, "run", l_key], ""))
                    break

            for tc_key in ("typecheck", "type-check", "check-types"):
                if tc_key in scripts:
                    has_typecheck = True
                    if mgr is None:
                        specs.append(("typecheck", None, "env_unavailable: Node package manager not found"))
                    else:
                        specs.append(("typecheck", [mgr, "run", tc_key], ""))
                    break

            for b_key in ("build", "check:build"):
                if b_key in scripts:
                    has_build = True
                    if mgr is None:
                        specs.append(("build", None, "env_unavailable: Node package manager not found"))
                    else:
                        specs.append(("build", [mgr, "run", b_key], ""))
                    break

    pyproject_path = workdir / "pyproject.toml"
    pyproject_text = ""
    if pyproject_path.is_file():
        try:
            pyproject_text = pyproject_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pyproject_text = ""

    py_tests = find_python_test_files(workdir)
    py_already_in_npm = any(tok in pkg_test_script for tok in ("pytest", "unittest", "python"))
    if (py_tests or "[tool.pytest" in pyproject_text) and not py_already_in_npm:
        pytest_bin = resolve_bin_in_env("pytest", env)
        tool_name = "test" if not has_test else "python_test"
        has_test = True
        if pytest_bin is not None:
            specs.append((tool_name, [pytest_bin, "-p", "no:cacheprovider", "-q"], ""))
        elif py_tests:
            specs.append((tool_name, [sys.executable, "-B", "-m", "unittest", *py_tests], ""))
        else:
            specs.append((tool_name, None, "env_unavailable: pytest is configured in pyproject.toml but not installed"))

    if not has_lint and "[tool.ruff" in pyproject_text:
        has_lint = True
        ruff_bin = resolve_bin_in_env("ruff", env)
        if ruff_bin is not None:
            specs.append(("lint", [ruff_bin, "check", "."], ""))
        else:
            specs.append(("lint", None, "env_unavailable: ruff configured in pyproject.toml but not installed"))

    if not has_typecheck and ("[tool.mypy" in pyproject_text or "[tool.pyright" in pyproject_text):
        has_typecheck = True
        mypy_bin = resolve_bin_in_env("mypy", env)
        pyright_bin = resolve_bin_in_env("pyright", env)
        if "[tool.mypy" in pyproject_text and mypy_bin is not None:
            specs.append(("typecheck", [mypy_bin, "."], ""))
        elif "[tool.pyright" in pyproject_text and pyright_bin is not None:
            specs.append(("typecheck", [pyright_bin], ""))
        else:
            specs.append(("typecheck", None, "env_unavailable: Python typechecker configured but binary not installed"))

    if (workdir / "go.mod").is_file():
        go_bin = resolve_bin_in_env("go", env)
        if not has_test:
            has_test = True
            specs.append(
                ("test", [go_bin, "test", "./..."], "")
                if go_bin
                else ("test", None, "env_unavailable: go binary not found")
            )
        if not has_lint:
            has_lint = True
            specs.append(
                ("lint", [go_bin, "vet", "./..."], "")
                if go_bin
                else ("lint", None, "env_unavailable: go binary not found")
            )
        if not has_build:
            has_build = True
            specs.append(
                ("build", [go_bin, "build", "./..."], "")
                if go_bin
                else ("build", None, "env_unavailable: go binary not found")
            )

    if (workdir / "Cargo.toml").is_file():
        cargo_bin = resolve_bin_in_env("cargo", env)
        if not has_test:
            has_test = True
            specs.append(
                ("test", [cargo_bin, "test"], "")
                if cargo_bin
                else ("test", None, "env_unavailable: cargo binary not found")
            )
        if not has_typecheck:
            has_typecheck = True
            specs.append(
                ("typecheck", [cargo_bin, "check"], "")
                if cargo_bin
                else ("typecheck", None, "env_unavailable: cargo binary not found")
            )
        if not has_build:
            has_build = True
            specs.append(
                ("build", [cargo_bin, "build"], "")
                if cargo_bin
                else ("build", None, "env_unavailable: cargo binary not found")
            )

    if not has_test:
        specs.append(("test", None, "not_configured: No test runner script or test suite detected"))
    if not has_lint:
        specs.append(("lint", None, "not_configured: No lint script configured"))
    if not has_typecheck:
        specs.append(("typecheck", None, "not_configured: No typecheck script configured"))
    if not has_build:
        specs.append(("build", None, "not_configured: No build script configured"))

    return specs


def _classify_rollup_entry(entry: Mapping[str, object]) -> tuple[str, str]:
    """Return (classification in 'failed'|'pending'|'passed', display_state)."""
    conclusion = str(entry.get("conclusion") or "").strip().upper()
    status = str(entry.get("status") or "").strip().upper()
    state = str(entry.get("state") or "").strip().upper()

    display = conclusion or status or state or "UNKNOWN"

    if conclusion in GH_FAILED_TOKENS or state in GH_FAILED_TOKENS or status in GH_FAILED_TOKENS:
        return "failed", display
    if status in GH_PENDING_TOKENS or state in GH_PENDING_TOKENS or conclusion in GH_PENDING_TOKENS:
        return "pending", status or state or conclusion
    if not conclusion and status and status not in ({"COMPLETED"} | GH_PASSED_TOKENS):
        return "pending", status
    if conclusion in GH_PASSED_TOKENS or state in GH_PASSED_TOKENS or status in GH_PASSED_TOKENS:
        return "passed", display
    return "pending", display


def evaluate_pr_meta_checks(
    untrusted_pr_meta: Mapping[str, object] | None,
    repo_dir: Path,
    pr_number: int | None,
) -> tuple[dict[str, object] | None, str]:
    meta_source = ""
    if isinstance(untrusted_pr_meta, dict):
        meta_source = str(untrusted_pr_meta.get("meta_source") or "").strip()
    is_offline = meta_source == "offline_file" or (
        meta_source == "" and isinstance(untrusted_pr_meta, dict) and pr_number is None
    )

    if isinstance(untrusted_pr_meta, dict) and "checks" in untrusted_pr_meta:
        checks_raw = untrusted_pr_meta.get("checks")
        if isinstance(checks_raw, list) and len(checks_raw) > 0:
            summary_lines: list[str] = []
            failed_checks: list[str] = []
            any_failed = False
            any_pending = False
            for entry in checks_raw:
                if isinstance(entry, dict):
                    cname = str(entry.get("name") or entry.get("context") or "check")
                    cls, disp = _classify_rollup_entry(entry)
                    summary_lines.append(f"{cname}: {disp}")
                    if cls == "failed":
                        any_failed = True
                        failed_checks.append(cname)
                    elif cls == "pending":
                        any_pending = True
                else:
                    summary_lines.append(str(entry))

            if any_failed:
                status = "failed"
                exit_code = 1
            elif any_pending:
                status = "pending"
                exit_code = 8
            else:
                status = "passed"
                exit_code = 0

            chk: dict[str, object] = {
                "tool": "gh_pr_checks",
                "cmd": ["pr_meta.checks"],
                "status": status,
                "exit_code": exit_code,
                "summary_lines": summary_lines[:SUMMARY_TAIL_LINES],
            }
            if failed_checks:
                chk["failed_checks"] = failed_checks
            return chk, "[gh_pr_checks (from pr_meta)]\n" + "\n".join(summary_lines) + "\n"

    if pr_number is not None and not is_offline and shutil.which("gh") is not None:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            res = subprocess.run(
                ["gh", "pr", "checks", str(pr_number)],
                cwd=str(repo_dir),
                env=env,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GH_TIMEOUT_SECONDS,
                check=False,
            )
            combined = (res.stdout + "\n" + res.stderr).strip()
            combined_lower = combined.lower()
            lines = [ln for ln in combined.splitlines() if ln.strip()]
            if res.returncode == 0:
                status = "passed"
            elif "no checks reported" in combined_lower:
                status = "not_configured"
            elif (
                "could not resolve" in combined_lower
                or "not logged into" in combined_lower
                or "authentication" in combined_lower
            ):
                status = "env_unavailable"
            elif res.returncode == 8 or (
                any(tok in combined_lower for tok in ("pending", "in_progress", "in progress", "queued", "waiting"))
                and not any(ftok in combined_lower for ftok in ("fail", "error", "cancel", "timed_out"))
            ):
                status = "pending"
            else:
                status = "failed"
            live_chk: dict[str, object] = {
                "tool": "gh_pr_checks",
                "cmd": ["gh", "pr", "checks", str(pr_number)],
                "status": status,
                "exit_code": res.returncode,
                "summary_lines": lines[-SUMMARY_TAIL_LINES:],
            }
            if status == "failed":
                live_failed_checks = extract_failed_gh_check_names(lines)
                if live_failed_checks:
                    live_chk["failed_checks"] = live_failed_checks
            return (
                live_chk,
                f"[gh pr checks {pr_number}] exit={res.returncode} status={status}\n{combined}\n",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return (
                {
                    "tool": "gh_pr_checks",
                    "cmd": ["gh", "pr", "checks", str(pr_number)],
                    "status": "env_unavailable",
                    "exit_code": None,
                    "summary_lines": [f"gh pr checks unavailable: {exc}"],
                },
                f"[gh pr checks] unavailable: {exc}\n",
            )

    return None, ""


def run_static_syntax_checks(workdir: Path, changed_files: Sequence[str]) -> tuple[dict[str, object], str]:
    targets = list(changed_files)
    if not targets:
        for root, dirs, files in os.walk(workdir, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {".git", ".gemini", "node_modules", ".venv", "dist", "build"}]
            rel_root = Path(root).relative_to(workdir)
            for fn in sorted(files):
                rel = (rel_root / fn).as_posix() if str(rel_root) != "." else fn
                targets.append(rel)
                if len(targets) >= 100:
                    break

    errors: list[str] = []
    checked_count = 0
    for rel in targets:
        fpath = workdir / rel
        if not fpath.is_file() or fpath.is_symlink():
            continue
        suffix = fpath.suffix.lower()
        if suffix not in {".py", ".json", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".astro", ".vue", ".go", ".rs"}:
            continue
        try:
            raw_bytes = fpath.read_bytes()
        except OSError as exc:
            errors.append(f"{rel}: read error ({exc})")
            continue
        if b"\x00" in raw_bytes:
            errors.append(f"{rel}: unexpected NUL byte in source file")
            continue
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"{rel}: invalid UTF-8 ({exc})")
            continue

        checked_count += 1
        if CONFLICT_MARKER_RE.search(text):
            errors.append(f"{rel}: unresolved git merge conflict marker detected")
        if suffix == ".py":
            try:
                ast.parse(text, filename=rel)
            except SyntaxError as exc:
                errors.append(f"{rel}:{exc.lineno}: Python SyntaxError: {exc.msg}")
        elif suffix == ".json" and Path(rel).name not in {"tsconfig.json"}:
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{rel}:{exc.lineno}: JSON syntax error: {exc.msg}")

    status = "failed" if errors else "passed"
    summary = errors[:SUMMARY_TAIL_LINES] if errors else [f"Read-only static syntax verification passed ({checked_count} files)."]
    log_block = "[static_syntax_check]\n" + "\n".join(summary) + "\n"
    return (
        {
            "tool": "static_syntax_check",
            "cmd": ["python-readonly-ast-and-syntax-scan"],
            "status": status,
            "exit_code": 1 if errors else 0,
            "summary_lines": summary,
        },
        log_block,
    )


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
UNITTEST_FAIL_ID_RE = re.compile(r"^(?:FAIL|ERROR):\s+(.+)$", re.MULTILINE)
PYTEST_SUMMARY_FAIL_ID_RE = re.compile(
    r"^(?:FAILED|ERROR)\s+([^\r\n]+?::[^\r\n]+?)(?:\s+-\s+.*)?$",
    re.MULTILINE,
)
PYTEST_COLLECT_ERR_ID_RE = re.compile(r"^ERROR\s+(\S+\.py)\b", re.MULTILINE)
PYTEST_INLINE_FAIL_ID_RE = re.compile(r"^(\S+\.py::\S+)\s+(?:FAILED|ERROR)\b", re.MULTILINE)
TAP_FAIL_ID_RE = re.compile(r"^\s*not ok\s+\d+(?:\s+-\s+|\s+)([^\r\n]+?)\s*$", re.MULTILINE)
TAP_SKIP_TODO_RE = re.compile(r"#\s*(?:SKIP|TODO)\b", re.IGNORECASE)
TAP_TIME_COMMENT_RE = re.compile(r"\s*#\s*time=[0-9.]+m?s.*$", re.IGNORECASE)
JEST_FAIL_ID_RE = re.compile(r"^\s*●\s+(.+?)\s*$", re.MULTILINE)
SPEC_CROSS_FAIL_ID_RE = re.compile(
    r"^\s*[✖✕×]\s+(.+?)(?:\s+\(?[0-9.]+\s*m?s\)?)?\s*$",
    re.MULTILINE,
)
VITEST_FAIL_ID_RE = re.compile(r"^\s*FAIL\s+(\S+\s+>\s+.+?)(?:\s+\[[^\]]*\])?\s*$", re.MULTILINE)
GO_FAIL_ID_RE = re.compile(r"^\s*---\s*FAIL:\s+(\S+)", re.MULTILINE)
CARGO_FAIL_ID_RE = re.compile(r"^test\s+(.+?)\s+\.\.\.\s+FAILED\s*$", re.MULTILINE)

UNITTEST_SUMMARY_COUNT_RE = re.compile(r"^FAILED\s*\(([^)]+)\)\s*$", re.MULTILINE)
PYTEST_SUMMARY_LINE_RE = re.compile(
    r"(?:^|=+\s*)([^\n=]*\b\d+\s+(?:failed|errors?)\b[^\n=]*)\s+in\s+[0-9.]+s",
    re.MULTILINE,
)
TAP_SUMMARY_COUNT_RE = re.compile(r"^(?:#|ℹ)\s*(?:fail|failed)\s+(\d+)\b", re.MULTILINE)
JEST_SUMMARY_COUNT_RE = re.compile(r"Tests:?\s+(?:[^\n]*?,\s*)?(\d+)\s+failed\b")
MOCHA_SUMMARY_COUNT_RE = re.compile(r"^\s*(\d+)\s+failing\b", re.MULTILINE)
CARGO_SUMMARY_COUNT_RE = re.compile(r"test result:\s*FAILED\.\s*\d+\s+passed;\s*(\d+)\s+failed\b")
DIAGNOSTIC_ERROR_LINE_RE = re.compile(
    r"^(?:(?:Assertion)?Error:|error(?:\[[A-Z0-9]+\])?:|FAIL\b|[✗✘]\s+|"
    r"\S+:\d+(?::\d+)?:\s*(?:[A-Z]\d+\s+|error\b)|.*\berror\s+TS\d+:)",
    re.MULTILINE,
)


def extract_failure_signature(raw_output: str, workdir: Path) -> tuple[list[str], int | None]:
    """Extract normalized failed test/check identifiers and total failure count from runner output."""
    cleaned = ANSI_ESCAPE_RE.sub("", raw_output)
    for prefix in dict.fromkeys((str(workdir.resolve()), str(workdir))):
        if prefix:
            cleaned = cleaned.replace(prefix, "<WORKDIR>")

    failed_ids: set[str] = set()
    for m in UNITTEST_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"unittest:{m.group(1).strip()}")
    for m in PYTEST_SUMMARY_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"pytest:{m.group(1).strip()}")
    for m in PYTEST_COLLECT_ERR_ID_RE.finditer(cleaned):
        failed_ids.add(f"pytest_collect:{m.group(1).strip()}")
    for m in PYTEST_INLINE_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"pytest:{m.group(1).strip()}")
    for m in TAP_FAIL_ID_RE.finditer(cleaned):
        raw_label = m.group(1).strip()
        if raw_label and not TAP_SKIP_TODO_RE.search(raw_label):
            label = TAP_TIME_COMMENT_RE.sub("", raw_label).strip()
            if label:
                failed_ids.add(f"tap:{label}")
    for m in JEST_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"jest:{m.group(1).strip()}")
    for m in SPEC_CROSS_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"spec:{m.group(1).strip()}")
    for m in VITEST_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"vitest:{m.group(1).strip()}")
    for m in GO_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"go:{m.group(1).strip()}")
    for m in CARGO_FAIL_ID_RE.finditer(cleaned):
        failed_ids.add(f"cargo:{m.group(1).strip()}")

    failure_count: int | None = None
    ut_matches = list(UNITTEST_SUMMARY_COUNT_RE.finditer(cleaned))
    pt_matches = list(PYTEST_SUMMARY_LINE_RE.finditer(cleaned))
    tap_matches = list(TAP_SUMMARY_COUNT_RE.finditer(cleaned))
    jest_matches = list(JEST_SUMMARY_COUNT_RE.finditer(cleaned))
    mocha_matches = list(MOCHA_SUMMARY_COUNT_RE.finditer(cleaned))
    cargo_matches = list(CARGO_SUMMARY_COUNT_RE.finditer(cleaned))

    if ut_matches:
        failure_count = sum(
            int(x)
            for m_ut in ut_matches
            for x in re.findall(r"\b(?:failures|errors)=(\d+)\b", m_ut.group(1))
        )
    elif pt_matches:
        failure_count = sum(
            int(x)
            for m_pt in pt_matches
            for x in re.findall(r"\b(\d+)\s+(?:failed|errors?)\b", m_pt.group(1))
        )
    elif tap_matches:
        failure_count = sum(int(m_t.group(1)) for m_t in tap_matches)
    elif jest_matches:
        failure_count = sum(int(m_j.group(1)) for m_j in jest_matches)
    elif mocha_matches:
        failure_count = sum(int(m_m.group(1)) for m_m in mocha_matches)
    elif cargo_matches:
        failure_count = sum(int(m_c.group(1)) for m_c in cargo_matches)
    elif failed_ids:
        failure_count = len(failed_ids)
    else:
        diag_hits = DIAGNOSTIC_ERROR_LINE_RE.findall(cleaned)
        if diag_hits:
            failure_count = len(diag_hits)

    return sorted(failed_ids), failure_count


def _as_optional_int(val: object) -> int | None:
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


def _compare_head_and_base_failures(
    head_chk: Mapping[str, object],
    base_chk: Mapping[str, object],
) -> tuple[bool, list[str]]:
    """Return (is_regression, newly_failed_ids) comparing head vs merge_base failure details."""
    raw_head_ids = head_chk.get("failed_tests")
    raw_base_ids = base_chk.get("failed_tests")
    head_ids = {str(x) for x in raw_head_ids} if isinstance(raw_head_ids, list) else set()
    base_ids = {str(x) for x in raw_base_ids} if isinstance(raw_base_ids, list) else set()

    head_count = _as_optional_int(head_chk.get("failure_count"))
    base_count = _as_optional_int(base_chk.get("failure_count"))

    if head_ids:
        new_ids = sorted(head_ids - base_ids)
        if new_ids:
            return True, new_ids
        if head_count is not None and base_count is not None and head_count > base_count:
            return True, []
        return False, []

    if base_ids:
        # Base executed identifiable tests while head collapsed before test execution (e.g. import/collection crash)
        return True, []

    if head_count is not None and base_count is not None:
        return head_count > base_count, []
    if head_count is not None and head_count > 0 and base_count is None:
        return True, []

    head_lines = head_chk.get("summary_lines")
    base_lines = base_chk.get("summary_lines")
    if isinstance(head_lines, list) and isinstance(base_lines, list):
        return head_lines != base_lines, []
    return False, []


def _apply_base_comparison_result(
    chk: dict[str, object],
    base_chk: Mapping[str, object],
    tool_name: str,
    merge_base_sha: str,
    log_chunks: list[str],
) -> None:
    base_st = str(base_chk.get("status") or "")
    if base_st in {"passed", "timed_out"}:
        chk["status"] = "failed"
        chk["failure_origin"] = "regression"
        log_chunks.append(
            f"[base_worktree:{tool_name}] Status '{base_st}' on merge_base_sha -> regression ('failed').\n"
        )
        return
    if base_st != "failed":
        chk["failure_origin"] = "unknown"
        return

    is_reg, new_failed_ids = _compare_head_and_base_failures(chk, base_chk)
    summary_existing = chk.get("summary_lines")
    if is_reg:
        chk["status"] = "failed"
        chk["failure_origin"] = "regression"
        if new_failed_ids:
            chk["new_failed_tests"] = new_failed_ids[:50]
        detail = (
            f"new failed tests {new_failed_ids[:5]}"
            if new_failed_ids
            else f"failure count increased ({base_chk.get('failure_count')} -> {chk.get('failure_count')})"
        )
        note = f"Note: '{tool_name}' introduced regression on top of preexisting base failures ({detail})."
        if isinstance(summary_existing, list):
            summary_existing.append(note)
        log_chunks.append(
            f"[base_worktree:{tool_name}] {detail} compared to merge_base_sha -> regression ('failed').\n"
        )
        return

    chk["status"] = "failed_preexisting"
    chk["failure_origin"] = "preexisting"
    note = f"Note: '{tool_name}' failed identically on merge_base_sha ({merge_base_sha[:12]}) -> failed_preexisting."
    if isinstance(summary_existing, list):
        summary_existing.append(note)
    log_chunks.append(
        f"[base_worktree:{tool_name}] Identical failures ({base_st}) on merge_base_sha -> 'failed_preexisting'.\n"
    )


def execute_single_command(
    tool: str,
    cmd: Sequence[str],
    workdir: Path,
    env: Mapping[str, str],
    timeout_seconds: int = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> tuple[dict[str, object], str]:
    exec_env = dict(env)
    exec_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(workdir),
            env=exec_env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = f"Executable not found: {cmd[0]} ({exc})"
        return (
            {
                "tool": tool,
                "cmd": list(cmd),
                "status": "env_unavailable",
                "exit_code": None,
                "summary_lines": [msg],
            },
            f"[{tool}] {' '.join(cmd)}\n{msg}\n",
        )
    except subprocess.TimeoutExpired:
        msg = f"Check '{tool}' timed out after {timeout_seconds}s: {' '.join(cmd)}"
        return (
            {
                "tool": tool,
                "cmd": list(cmd),
                "status": "timed_out",
                "exit_code": None,
                "summary_lines": [msg],
            },
            f"[{tool}] {' '.join(cmd)}\n{msg}\n",
        )

    combined = (proc.stdout + ("\n" if proc.stdout and proc.stderr else "") + proc.stderr).strip()
    lines = [ln for ln in combined.splitlines() if ln.strip()]

    if proc.returncode == 0:
        status = "passed"
    elif proc.returncode == 127 or ("command not found" in combined.lower() and proc.returncode != 1):
        status = "env_unavailable"
    else:
        status = "failed"

    entry: dict[str, object] = {
        "tool": tool,
        "cmd": list(cmd),
        "status": status,
        "exit_code": proc.returncode,
        "summary_lines": lines[-SUMMARY_TAIL_LINES:] if lines else [f"Exited with code {proc.returncode}"],
    }
    if status == "failed":
        failed_ids, failure_count = extract_failure_signature(combined, workdir)
        if failed_ids:
            entry["failed_tests"] = failed_ids[:200]
        if failure_count is not None:
            entry["failure_count"] = failure_count

    log_section = (
        f"=== [{tool}] cmd={' '.join(cmd)} exit={proc.returncode} status={status} ===\n"
        f"{combined}\n\n"
    )
    return entry, log_section


def reverify_failures_against_base(
    *,
    session_dir: Path,
    repo_dir: Path,
    merge_base_sha: str,
    checks_out: list[dict[str, object]],
    env: Mapping[str, str],
    bind_repo_deps: bool,
    allow_exec: bool,
    timeout_seconds: int,
    log_chunks: list[str],
) -> None:
    """Re-run failed local checks against merge_base_sha in a temporary detached base_worktree."""
    failed_entries = [
        chk
        for chk in checks_out
        if chk.get("status") == "failed" and str(chk.get("tool") or "") not in {"gh_pr_checks", "static_syntax_check"}
    ]
    if not failed_entries or not merge_base_sha or not repo_dir.is_dir():
        return

    base_wt = session_dir / "base_worktree"
    base_links: list[Path] = []
    wt_added = False

    try:
        wt_res = run_subproc(
            ["git", "worktree", "add", "--detach", str(base_wt), merge_base_sha],
            cwd=repo_dir,
            timeout=30,
        )
        if wt_res is None or wt_res.returncode != 0 or not base_wt.is_dir():
            log_chunks.append(
                f"[base_worktree] Could not materialize merge_base_sha {merge_base_sha}; treating failures as regression.\n"
            )
            for chk in failed_entries:
                chk["failure_origin"] = "regression"
            return

        wt_added = True
        if not allow_exec:
            base_hazard, base_reasons = detect_ci_script_hazard_in_files(base_wt)
            if base_hazard:
                log_chunks.append(
                    f"[base_worktree] Blocked base execution due to CI script hazard: {base_reasons}\n"
                )
                for chk in failed_entries:
                    chk["failure_origin"] = "regression"
                return

        if bind_repo_deps:
            for dep_name in ("node_modules", ".venv"):
                src_dep = repo_dir / dep_name
                dst_dep = base_wt / dep_name
                if src_dep.is_dir() and not dst_dep.exists() and not dst_dep.is_symlink():
                    os.symlink(src_dep, dst_dep)
                    base_links.append(dst_dep)

        base_specs_map: dict[str, list[str]] = {
            t_name: cmd_argv
            for t_name, cmd_argv, _ in discover_check_specs(base_wt, env)
            if cmd_argv is not None
        }

        for chk in failed_entries:
            tool_name = str(chk.get("tool") or "")
            base_cmd = base_specs_map.get(tool_name)
            if base_cmd is None:
                chk["failure_origin"] = "regression"
                log_chunks.append(
                    f"[base_worktree:{tool_name}] Not present on merge_base_sha -> classified as regression.\n"
                )
                continue

            base_chk, base_log = execute_single_command(
                f"base:{tool_name}",
                base_cmd,
                base_wt,
                env,
                timeout_seconds=timeout_seconds,
            )
            log_chunks.append(base_log)
            _apply_base_comparison_result(chk, base_chk, tool_name, merge_base_sha, log_chunks)
    finally:
        for link_p in base_links:
            try:
                if link_p.is_symlink():
                    link_p.unlink()
            except OSError:
                pass
        unlink_symlinks_under(base_wt)
        if wt_added and repo_dir.is_dir():
            run_subproc(["git", "worktree", "remove", "--force", str(base_wt)], cwd=repo_dir, timeout=30)
            run_subproc(["git", "worktree", "prune"], cwd=repo_dir, timeout=30)
        shutil.rmtree(base_wt, ignore_errors=True)


def compute_overall_status(
    checks: Sequence[Mapping[str, object]],
    block_status: str | None,
) -> str:
    statuses = [str(c.get("status") or "") for c in checks]
    if "failed" in statuses:
        return "failed"
    if block_status == "blocked_fork":
        return "blocked_fork"
    if block_status == "blocked_untrusted_script":
        return "blocked_untrusted_script"
    if "timed_out" in statuses:
        return "timed_out"
    if "pending" in statuses:
        return "pending"
    if "failed_preexisting" in statuses:
        return "failed_preexisting"
    if "passed" in statuses:
        return "passed"
    if "env_unavailable" in statuses:
        return "env_unavailable"
    return "not_configured"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_checks.py",
        description="Execute Stage 2 CI checks with base failure comparison and 3-layer untrusted code protection.",
    )
    parser.add_argument("--session", default=None, help="Session directory path.")
    parser.add_argument("--workdir", default=None, help="Standalone workdir path (never writes temp files inside workdir).")
    parser.add_argument(
        "--allow-exec",
        action="store_true",
        help="Explicitly allow local dynamic execution even on untrusted fork PRs or hazardous CI scripts.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        help=f"Per-check timeout in seconds (default: {DEFAULT_CHECK_TIMEOUT_SECONDS}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    timeout_seconds = int(args.timeout)
    if timeout_seconds < 1:
        fail_with_code(f"Invalid --timeout '{timeout_seconds}': must be >= 1.", 2)

    session_dir: Path | None = None
    state: dict[str, object] = {}
    standalone_root: Path | None = None
    standalone_created_dirs: list[str] = []

    if args.session is not None:
        session_dir = Path(str(args.session)).expanduser().resolve()
        state = read_verified_gate_state(session_dir)

        s1_scan = state.get("stage1_scan")
        if not isinstance(s1_scan, dict) or s1_scan.get("executed") is not True:
            fail_with_code(
                "Stage 2 blocked: impact_scan.py --session must be executed before Stage 1/Stage 2.",
                2,
            )

        stage1 = state.get("stage1")
        s1_status = stage1.get("status") if isinstance(stage1, dict) else None
        if not is_stage1_passed_or_skipped(state):
            revoked = bool(state.get("skip_test_revoked", False))
            reason_suffix = (
                f" (--skip-test revoked: {state.get('skip_test_revoked_reason')})"
                if revoked
                else ""
            )
            fail_with_code(
                f"Stage 2 blocked: Stage 1 gate status must be OK or EXEMPT_NO_TESTABLE_CODE "
                f"(or NG/SKIPPED_BY_FLAG with active --skip-test; current: '{s1_status}'){reason_suffix}.",
                2,
                extra={
                    "stage1_status": s1_status,
                    "skip_test": bool(state.get("skip_test", False)),
                    "skip_test_revoked": revoked,
                },
            )

        mode = str(state.get("mode") or "")
        workdir = Path(str(state.get("workdir") or "")).resolve()
        repo_dir = Path(str(state.get("repo_dir") or workdir)).resolve()
        merge_base_sha = str(state.get("merge_base_sha") or "")
        review_base_sha = str(state.get("review_base_sha") or "")
        head_sha = str(state.get("head_sha") or "")
        is_fork_pr = bool(state.get("is_fork_pr", False))
        ci_script_hazard = bool(state.get("ci_script_hazard", False))
        lockfile_modified = bool(state.get("lockfile_modified", False))
        pr_num_raw = state.get("pr_number")
        pr_number = pr_num_raw if isinstance(pr_num_raw, int) else None
        untrusted_meta = state.get("untrusted_pr_meta")
        untrusted_pr_meta = untrusted_meta if isinstance(untrusted_meta, dict) else None
        ch_raw = state.get("changed_files")
        changed_files = [str(x) for x in ch_raw] if isinstance(ch_raw, list) else []
        log_path = session_dir / "ci_checks.log"
    elif args.workdir is not None:
        workdir = Path(str(args.workdir)).expanduser().resolve()
        if not workdir.is_dir():
            fail_with_code(f"--workdir does not exist: {workdir}", 2)
        mode = "standalone"
        repo_dir = workdir
        merge_base_sha = ""
        review_base_sha = ""
        head_sha = ""
        is_fork_pr = False
        ci_script_hazard, _ = detect_ci_script_hazard_in_files(workdir)
        lockfile_modified = False
        pr_number = None
        untrusted_pr_meta = None
        changed_files = []
        standalone_root, _, standalone_created_dirs = resolve_session_root("global", workdir)
        log_path = standalone_root / f".standalone-ci-{secrets.token_hex(6)}.log"
    else:
        fail_with_code("Specify either --session <dir> or --workdir <dir>.", 2)

    env = dict(os.environ)
    env["CI"] = "true"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    checks_out: list[dict[str, object]] = []
    log_chunks: list[str] = [f"# yharuto-code-review Stage 2 CI Log ({utc_now_iso()})\n"]

    block_dynamic = (is_fork_pr or ci_script_hazard) and not bool(args.allow_exec)
    block_status: str | None = None

    try:
        if block_dynamic:
            if is_fork_pr:
                block_status = "blocked_fork"
                execution_policy = "blocked_untrusted_fork"
                reason_tag = "is_fork_pr=true"
            else:
                block_status = "blocked_untrusted_script"
                execution_policy = "blocked_untrusted_script"
                reason_tag = "ci_script_hazard=true"

            specs = discover_check_specs(workdir, env)
            for tool, cmd, none_reason in specs:
                if cmd is not None:
                    msg = (
                        f"Blocked local execution of '{' '.join(cmd)}' because {reason_tag} "
                        "(pass --allow-exec only after explicit human verification)."
                    )
                    checks_out.append(
                        {
                            "tool": tool,
                            "cmd": cmd,
                            "status": block_status,
                            "exit_code": None,
                            "summary_lines": [msg],
                        }
                    )
                    log_chunks.append(f"[{tool}] {block_status.upper()}: {msg}\n")
                else:
                    st = "env_unavailable" if none_reason.startswith("env_unavailable:") else "not_configured"
                    checks_out.append(
                        {
                            "tool": tool,
                            "cmd": [],
                            "status": st,
                            "exit_code": None,
                            "summary_lines": [none_reason],
                        }
                    )
                    log_chunks.append(f"[{tool}] {st}: {none_reason}\n")

            if pr_number is not None or (untrusted_pr_meta is not None and "checks" in untrusted_pr_meta):
                pr_chk, pr_log = evaluate_pr_meta_checks(
                    untrusted_pr_meta=untrusted_pr_meta,
                    repo_dir=repo_dir,
                    pr_number=pr_number,
                )
                if pr_chk is not None:
                    checks_out.append(pr_chk)
                    log_chunks.append(pr_log)

            static_chk, static_log = run_static_syntax_checks(workdir, changed_files)
            checks_out.append(static_chk)
            log_chunks.append(static_log)
        else:
            created_links: list[Path] = []
            bind_repo_deps = (
                (workdir != repo_dir)
                and (not is_fork_pr)
                and (not ci_script_hazard)
                and (not lockfile_modified)
            )
            execution_policy = "detached_worktree_bound_deps" if bind_repo_deps else "local_exec"

            try:
                if bind_repo_deps:
                    path_prefixes: list[str] = []
                    node_bin = repo_dir / "node_modules" / ".bin"
                    if node_bin.is_dir():
                        path_prefixes.append(str(node_bin))
                    if (repo_dir / "node_modules").is_dir():
                        env["NODE_PATH"] = str(repo_dir / "node_modules")

                    for venv_name in (".venv", "venv"):
                        venv_dir = repo_dir / venv_name
                        venv_bin = venv_dir / "bin"
                        if venv_bin.is_dir():
                            path_prefixes.append(str(venv_bin))
                            env["VIRTUAL_ENV"] = str(venv_dir)
                            break

                    if path_prefixes:
                        existing_path = env.get("PATH", "")
                        env["PATH"] = os.pathsep.join(path_prefixes + ([existing_path] if existing_path else []))

                    for dep_name in ("node_modules", ".venv"):
                        src_dep = repo_dir / dep_name
                        dst_dep = workdir / dep_name
                        if src_dep.is_dir() and not dst_dep.exists() and not dst_dep.is_symlink():
                            os.symlink(src_dep, dst_dep)
                            created_links.append(dst_dep)

                    if session_dir is not None and created_links:
                        state["temp_symlinks"] = [str(p) for p in created_links]
                        write_sealed_gate_state(session_dir, state)

                specs = discover_check_specs(workdir, env)
                for tool, cmd, none_reason in specs:
                    if cmd is None:
                        st = "env_unavailable" if none_reason.startswith("env_unavailable:") else "not_configured"
                        checks_out.append(
                            {
                                "tool": tool,
                                "cmd": [],
                                "status": st,
                                "exit_code": None,
                                "summary_lines": [none_reason],
                            }
                        )
                        log_chunks.append(f"[{tool}] {st}: {none_reason}\n")
                    else:
                        chk_entry, chk_log = execute_single_command(
                            tool,
                            cmd,
                            workdir,
                            env,
                            timeout_seconds=timeout_seconds,
                        )
                        checks_out.append(chk_entry)
                        log_chunks.append(chk_log)

                reverify_base_sha = (
                    review_base_sha
                    if (mode == "local" and review_base_sha and review_base_sha != head_sha)
                    else merge_base_sha
                )
                if (
                    session_dir is not None
                    and mode in {"pr", "range", "local"}
                    and reverify_base_sha
                    and any(c.get("status") == "failed" for c in checks_out)
                ):
                    reverify_failures_against_base(
                        session_dir=session_dir,
                        repo_dir=repo_dir,
                        merge_base_sha=reverify_base_sha,
                        checks_out=checks_out,
                        env=env,
                        bind_repo_deps=bind_repo_deps,
                        allow_exec=bool(args.allow_exec),
                        timeout_seconds=timeout_seconds,
                        log_chunks=log_chunks,
                    )

                if pr_number is not None or (untrusted_pr_meta is not None and "checks" in untrusted_pr_meta):
                    pr_chk, pr_log = evaluate_pr_meta_checks(
                        untrusted_pr_meta=untrusted_pr_meta,
                        repo_dir=repo_dir,
                        pr_number=pr_number,
                    )
                    if pr_chk is not None:
                        checks_out.append(pr_chk)
                        log_chunks.append(pr_log)
            finally:
                for link_path in created_links:
                    try:
                        if link_path.is_symlink():
                            link_path.unlink()
                    except OSError:
                        pass
                if session_dir is not None and state.get("temp_symlinks"):
                    state["temp_symlinks"] = []
                    write_sealed_gate_state(session_dir, state)

        overall_status = compute_overall_status(checks_out, block_status=block_status)

        skip_test_revoked = False
        skip_test_revoked_reason = ""
        if session_dir is not None:
            s1_state = state.get("stage1")
            was_stage1_bypassed = isinstance(s1_state, dict) and (
                s1_state.get("bypassed_by_skip_test") is True
                or (bool(state.get("skip_test", False)) and s1_state.get("status") in ALLOWED_STAGE1_SKIP_STATUSES)
            )
            failed_test_tools = find_stage2_test_failures(checks_out)
            if was_stage1_bypassed and failed_test_tools:
                skip_test_revoked = True
                tools_csv = ", ".join(failed_test_tools)
                skip_test_revoked_reason = (
                    f"Stage 2 test check(s) failed ({tools_csv}); --skip-test cannot bypass failing tests."
                )
                log_chunks.append(f"[skip_test_guard] REVOKED: {skip_test_revoked_reason}\n")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(log_chunks), encoding="utf-8")
        log_sha = sha256_file(log_path)

        if session_dir is not None:
            state["stage2"] = {
                "executed": True,
                "overall_status": overall_status,
                "execution_policy": execution_policy,
                "checks": checks_out,
                "log_path": str(log_path),
                "log_sha256": log_sha,
                "updated_at": utc_now_iso(),
            }
            state["stage5"] = reset_stage5_state()
            if skip_test_revoked:
                state["skip_test_revoked"] = True
                state["skip_test_revoked_reason"] = skip_test_revoked_reason
                s1_prev = state.get("stage1")
                if isinstance(s1_prev, dict):
                    s1_next = dict(s1_prev)
                    s1_next["bypassed_by_skip_test"] = False
                    s1_next["status"] = "NG"
                    prev_gaps = s1_next.get("gaps_count")
                    if not isinstance(prev_gaps, int) or isinstance(prev_gaps, bool) or prev_gaps < 1:
                        s1_next["gaps_count"] = 1
                    prev_summary = str(s1_next.get("summary") or "").strip()
                    s1_next["summary"] = (
                        f"{prev_summary} / {skip_test_revoked_reason}"
                        if prev_summary
                        else skip_test_revoked_reason
                    )
                    s1_next["updated_at"] = utc_now_iso()
                    state["stage1"] = s1_next
            write_sealed_gate_state(session_dir, state)
    finally:
        if args.workdir is not None:
            try:
                if log_path.exists():
                    log_path.unlink()
            except OSError:
                pass
            if standalone_root is not None and standalone_created_dirs:
                prune_created_project_dirs(standalone_root, standalone_created_dirs)

    result_payload: dict[str, object] = {
        "overall_status": overall_status,
        "execution_policy": execution_policy,
        "checks": checks_out,
        "log_sha256": log_sha,
    }
    if skip_test_revoked:
        result_payload["skip_test_revoked"] = True
        result_payload["skip_test_revoked_reason"] = skip_test_revoked_reason
        result_payload["required_output_mode"] = "stage1_ng"
    emit_json(result_payload)
    # Stage 2 returns exit 0 even when checks fail (when skip_test_revoked is true, Stage 3-5 gates enforce stage1_ng).
    return 0


if __name__ == "__main__":
    sys.exit(main())
