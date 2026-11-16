#!/usr/bin/env python3
"""Stage 3 added/modified-line AI Slop and structural risk signal scanner (7 canonical rules)."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import (  # noqa: E402
    emit_json,
    fail_with_code,
    read_verified_gate_state,
    run_subproc,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
    write_sealed_gate_state,
)

TOTAL_TIMEOUT_SECONDS = 30.0
FILE_TIMEOUT_SECONDS = 5.0
GIT_TIMEOUT_SECONDS = 20

ALL_RULE_IDS: tuple[str, ...] = (
    "STRINGIFIED_CODE_OR_EVAL",
    "DUPLICATE_OR_OVERLAPPING_LISTENER",
    "MOCK_DEFENSIVE_GUARD",
    "TYPE_BYPASS_OR_SILENT_FALLBACK",
    "EMPTY_OR_BROAD_CATCH",
    "DEEP_NESTING_OR_COMPLEX_FLOW",
    "MISLEADING_OR_PASS_THROUGH",
)

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".gemini",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".astro",
        "coverage",
        "target",
        "vendor",
    }
)

LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "bun.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "go.sum",
        "Gemfile.lock",
        "composer.lock",
    }
)

SCANNABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".astro",
        ".vue",
        ".svelte",
        ".html",
        ".py",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".php",
        ".cs",
    }
)

TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:__tests__|__mocks__|tests?|specs?|fixtures?)/"
    r"|(?:\.(?:test|spec|mock)\.[A-Za-z0-9]+$)"
    r"|(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.(?:py|go|rs))$",
    re.IGNORECASE,
)

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Rule 1: STRINGIFIED_CODE_OR_EVAL
TO_STRING_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.toString\s*\(\s*\)")
BENIGN_TO_STRING_CALLERS: frozenset[str] = frozenset(
    {
        "err",
        "error",
        "e",
        "url",
        "uri",
        "params",
        "searchParams",
        "id",
        "count",
        "num",
        "number",
        "port",
        "date",
        "now",
        "buf",
        "buffer",
        "val",
        "value",
        "idx",
        "index",
        "status",
        "code",
    }
)
EVAL_JS_RE = re.compile(
    r"(?:\beval\s*\(|\bnew\s+Function\s*\(|\b(?:setTimeout|setInterval)\s*\(\s*['\"`]|"
    r"(?:set:html|dangerouslySetInnerHTML)\s*=\s*\{[^}]*(?:<script|\.toString\s*\())"
)
EVAL_PY_RE = re.compile(r"(?<![.\w])(?:eval|exec)\s*\(")

# Rule 2: DUPLICATE_OR_OVERLAPPING_LISTENER
LISTENER_RE = re.compile(
    r"(?:\baddEventListener\s*\(\s*['\"](?:message|storage|resize|keydown|keyup|fullscreenchange|visibilitychange|theme)['\"]"
    r"|\bwindow\.onmessage\s*="
    r"|\bnew\s+(?:BroadcastChannel|MutationObserver|ResizeObserver)\s*\("
    r"|\bmatchMedia\s*\([^)]*\)\.(?:addEventListener|addListener)\s*\("
    r"|\.(?:on|subscribe)\s*\(\s*['\"](?:message|theme|storage|auth|sync)['\"])"
)

# Rule 3: MOCK_DEFENSIVE_GUARD
MOCK_GUARD_RE = re.compile(
    r"(?:typeof\s+[A-Za-z_$][\w$.?]*\.(?:classList|toggle|add|remove|contains|querySelector|querySelectorAll|"
    r"getElementById|setAttribute|removeAttribute|getAttribute|appendChild|removeChild|addEventListener|"
    r"removeEventListener|style|dataset|focus|blur|click)\b"
    r"|\b[A-Za-z_$][\w$]*\.classList\?\.(?:toggle|add|remove|contains)\s*\()"
)
SSR_GLOBAL_GUARD_RE = re.compile(r"typeof\s+(?:window|document|navigator|globalThis)\s*[!=]==")

# Rule 4: TYPE_BYPASS_OR_SILENT_FALLBACK
TYPE_BYPASS_RE = re.compile(
    r"(?:\bas\s+any\b|:\s*any\b|@ts-ignore\b|@ts-nocheck\b|#\s*type:\s*ignore(?!\[)|\.forEach\s*\(\s*async\b)"
)

# Rule 5: EMPTY_OR_BROAD_CATCH
INLINE_EMPTY_CATCH_RE = re.compile(
    r"(?:\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?://[^\n]*)?(?:/\*.*?\*/\s*)?\}"
    r"|\.catch\s*\(\s*\([^)]*\)\s*=>\s*(?:\{\s*\}|null|undefined)\s*\))"
)
CATCH_OPEN_RE = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*$")
PY_BROAD_EXCEPT_RE = re.compile(r"^\s*except(?:\s+(?:Exception|BaseException)(?:\s+as\s+\w+)?)?\s*:\s*(?:pass|\.\.\.)?\s*$")

# Rule 6: DEEP_NESTING_OR_COMPLEX_FLOW
CONTROL_FLOW_KEYWORD_RE = re.compile(r"^\s*(?:if|else\s+if|elif|for|while|switch|match|try)\b")
NESTED_TERNARY_RE = re.compile(r"\?[^:?;\n\"'`]+\?[^:\n\"'`]+:")
QUADRATIC_REDUCE_RE = re.compile(r"\.reduce\s*\([^)]*=>\s*\(?[\{\[]\s*\.\.\.")

# Rule 7: MISLEADING_OR_PASS_THROUGH
PURE_NAMED_FUNC_RE = re.compile(
    r"(?:\b(?:async\s+)?function\s+((?:get|is|has|can|should|validate|check)[A-Z_][\w$]*)\s*\("
    r"|\bconst\s+((?:get|is|has|can|should|validate|check)[A-Z_][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
    r"|^\s*(?:async\s+)?def\s+((?:get|is|has|can|should|validate|check)_[a-z0-9_]+)\s*\()"
)
SIDE_EFFECT_CALL_RE = re.compile(
    r"(?:\bfetch\s*\(|\baxios\.(?:post|put|patch|delete)\b|\.(?:insert|update|delete|save|destroy| bulkCreate)\s*\("
    r"|\b(?:localStorage|sessionStorage)\.(?:setItem|removeItem|clear)\s*\("
    r"|\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\b)"
)
PASS_THROUGH_FN_RE = re.compile(
    r"^\s*(?:export\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{\s*return\s+([A-Za-z_$][\w$.]*)\s*\(\2\)\s*;?\s*\}\s*$"
)


def run_git(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    return run_subproc(cmd, cwd=cwd, timeout=GIT_TIMEOUT_SECONDS)


def should_skip_file(rel_path: str) -> str | None:
    norm = rel_path.replace("\\", "/").lstrip("./")
    p = Path(norm)
    if any(seg in IGNORED_DIRS for seg in p.parts):
        return "ignored_directory"
    if p.name in LOCKFILE_NAMES or p.name.endswith(".min.js") or p.name.endswith(".bundle.js"):
        return "generated_or_lockfile"
    if TEST_FILE_RE.search(norm):
        return "test_or_mock_file"
    if p.suffix.lower() not in SCANNABLE_EXTENSIONS:
        return "non_code_extension"
    return None


def parse_added_lines_by_file(diff_text: str) -> dict[str, set[int]]:
    added_map: dict[str, set[int]] = {}
    current_file: str | None = None
    right_line = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            right_line = 0
            continue
        if raw_line.startswith("+++ /dev/null"):
            current_file = None
            continue
        if current_file is None:
            continue

        hunk_match = HUNK_HEADER_RE.match(raw_line)
        if hunk_match is not None:
            right_line = int(hunk_match.group(1))
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added_map.setdefault(current_file, set()).add(right_line)
            right_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        elif raw_line.startswith(" "):
            right_line += 1

    return added_map


def collect_target_lines(
    workdir: Path,
    repo_dir: Path,
    mode: str,
    merge_base_sha: str,
    head_sha: str,
    audit_paths: Sequence[str],
    preset_changed: Sequence[str] | None = None,
) -> tuple[dict[str, set[int] | None], list[dict[str, str]]]:
    """Return ({rel_file: set_of_added_line_numbers_or_None_for_all}, skipped_files)."""
    skipped: list[dict[str, str]] = []
    targets: dict[str, set[int] | None] = {}

    if mode in ("pr", "range") and merge_base_sha and head_sha:
        res = run_git(["git", "diff", "-U0", merge_base_sha, head_sha], cwd=repo_dir)
        diff_text = res.stdout if res and res.returncode == 0 else ""
        raw_map = parse_added_lines_by_file(diff_text)
        for rel, line_set in raw_map.items():
            reason = should_skip_file(rel)
            if reason is not None:
                skipped.append({"file": rel, "reason": reason})
            else:
                targets[rel] = line_set
        return targets, skipped

    if mode == "local":
        git_check = run_git(["git", "rev-parse", "--is-inside-work-tree"], cwd=workdir)
        if git_check is None or git_check.returncode != 0:
            fallback_files = list(preset_changed) if preset_changed is not None else []
            if not fallback_files:
                for root, dirs, files in os.walk(workdir, topdown=True, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (Path(root) / d).is_symlink())
                    rel_root = Path(root).relative_to(workdir)
                    for fname in sorted(files):
                        rel = (rel_root / fname).as_posix() if str(rel_root) != "." else fname
                        fallback_files.append(rel)
            for rel in fallback_files:
                reason = should_skip_file(rel)
                if reason is not None:
                    skipped.append({"file": rel, "reason": reason})
                else:
                    targets[rel] = None
            return targets, skipped

        head_chk = run_git(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=workdir)
        has_head = head_chk is not None and head_chk.returncode == 0
        diff_cmd = ["git", "diff", "-U0", "HEAD"] if has_head else ["git", "diff", "--cached", "-U0"]
        res = run_git(diff_cmd, cwd=workdir)
        raw_map = parse_added_lines_by_file(res.stdout if res else "")
        for rel, line_set in raw_map.items():
            reason = should_skip_file(rel)
            if reason is not None:
                skipped.append({"file": rel, "reason": reason})
            else:
                targets[rel] = line_set

        untracked_res = run_git(["git", "ls-files", "--others", "--exclude-standard"], cwd=workdir)
        if untracked_res and untracked_res.returncode == 0:
            for ln in untracked_res.stdout.splitlines():
                rel = ln.strip()
                if not rel:
                    continue
                reason = should_skip_file(rel)
                if reason is not None:
                    skipped.append({"file": rel, "reason": reason})
                else:
                    targets[rel] = None
        return targets, skipped

    # audit mode
    norm_prefixes = [p.strip("/").replace("\\", "/") for p in audit_paths if p.strip()]
    for root, dirs, files in os.walk(workdir, topdown=True, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (Path(root) / d).is_symlink())
        rel_root = Path(root).relative_to(workdir)
        for fname in sorted(files):
            rel = (rel_root / fname).as_posix() if str(rel_root) != "." else fname
            if norm_prefixes and not any(rel == pr or rel.startswith(pr + "/") for pr in norm_prefixes):
                continue
            reason = should_skip_file(rel)
            if reason is None:
                targets[rel] = None
    return targets, skipped


def indentation_depth(line: str) -> int:
    expanded = line.expandtabs(4)
    leading = len(expanded) - len(expanded.lstrip(" "))
    return leading // 4


def is_empty_catch_multiline(lines: Sequence[str], idx_zero: int, is_python: bool) -> bool:
    line = lines[idx_zero]
    if is_python:
        m = PY_BROAD_EXCEPT_RE.match(line)
        if m is None:
            return False
        if line.rstrip().endswith("pass") or line.rstrip().endswith("..."):
            return True
        for look in range(idx_zero + 1, min(idx_zero + 5, len(lines))):
            stripped = lines[look].strip()
            if not stripped or stripped.startswith("#"):
                continue
            return stripped in {"pass", "...", "return", "return None"}
        return False

    if CATCH_OPEN_RE.search(line) is None:
        return False
    for look in range(idx_zero + 1, min(idx_zero + 5, len(lines))):
        stripped = lines[look].strip()
        if not stripped or stripped.startswith("//") or (stripped.startswith("/*") and stripped.endswith("*/")):
            continue
        return stripped == "}"
    return False


def has_side_effect_in_window(lines: Sequence[str], idx_zero: int) -> bool:
    window = "\n".join(lines[idx_zero : min(idx_zero + 16, len(lines))])
    return SIDE_EFFECT_CALL_RE.search(window) is not None


def scan_single_file(
    rel_file: str,
    full_path: Path,
    allowed_lines: set[int] | None,
    max_remaining: int,
) -> tuple[list[dict[str, object]], str | None]:
    file_start = time.monotonic()
    try:
        raw_bytes = full_path.read_bytes()
    except OSError as exc:
        return [], f"read_error:{exc}"
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return [], "non_utf8_binary"

    lines = text.splitlines()
    suffix = full_path.suffix.lower()
    is_python = suffix == ".py"
    is_template_or_markup = suffix in {".astro", ".vue", ".svelte", ".html"}
    findings: list[dict[str, object]] = []

    def add_finding(line_no: int, rule_id: str, tags: list[str], raw_ln: str) -> None:
        findings.append(
            {
                "file": rel_file,
                "line": line_no,
                "rule_id": rule_id,
                "tags": tags,
                "excerpt": raw_ln.strip()[:200],
            }
        )

    for idx_zero, raw_line in enumerate(lines):
        if time.monotonic() - file_start > FILE_TIMEOUT_SECONDS:
            return findings, "file_timeout"
        if len(findings) >= max_remaining:
            break

        line_no = idx_zero + 1
        if allowed_lines is not None and line_no not in allowed_lines:
            continue

        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue

        # 1. STRINGIFIED_CODE_OR_EVAL
        matched_r1 = False
        for ts_match in TO_STRING_CALL_RE.finditer(raw_line):
            caller = ts_match.group(1)
            if caller not in BENIGN_TO_STRING_CALLERS or is_template_or_markup or "<script" in raw_line:
                add_finding(line_no, "STRINGIFIED_CODE_OR_EVAL", ["ai_slop", "stringified_code"], raw_line)
                matched_r1 = True
                break
        if not matched_r1:
            if (is_python and EVAL_PY_RE.search(raw_line)) or (not is_python and EVAL_JS_RE.search(raw_line)):
                add_finding(line_no, "STRINGIFIED_CODE_OR_EVAL", ["ai_slop", "dynamic_eval"], raw_line)

        if len(findings) >= max_remaining:
            break

        # 2. DUPLICATE_OR_OVERLAPPING_LISTENER
        if LISTENER_RE.search(raw_line):
            add_finding(
                line_no,
                "DUPLICATE_OR_OVERLAPPING_LISTENER",
                ["ai_slop", "listener_or_bus_overlap"],
                raw_line,
            )
            if len(findings) >= max_remaining:
                break

        # 3. MOCK_DEFENSIVE_GUARD
        if MOCK_GUARD_RE.search(raw_line) and not SSR_GLOBAL_GUARD_RE.search(raw_line):
            add_finding(
                line_no,
                "MOCK_DEFENSIVE_GUARD",
                ["ai_slop", "mock_driven_defensive_branch"],
                raw_line,
            )
            if len(findings) >= max_remaining:
                break

        # 4. TYPE_BYPASS_OR_SILENT_FALLBACK
        if TYPE_BYPASS_RE.search(raw_line):
            add_finding(
                line_no,
                "TYPE_BYPASS_OR_SILENT_FALLBACK",
                ["type_safety", "silent_bypass"],
                raw_line,
            )
            if len(findings) >= max_remaining:
                break

        # 5. EMPTY_OR_BROAD_CATCH
        if INLINE_EMPTY_CATCH_RE.search(raw_line) or is_empty_catch_multiline(lines, idx_zero, is_python):
            add_finding(
                line_no,
                "EMPTY_OR_BROAD_CATCH",
                ["error_handling", "swallowed_exception"],
                raw_line,
            )
            if len(findings) >= max_remaining:
                break

        # 6. DEEP_NESTING_OR_COMPLEX_FLOW
        is_deep = CONTROL_FLOW_KEYWORD_RE.match(raw_line) is not None and indentation_depth(raw_line) >= 5
        has_nested_ternary = (
            "extends " not in raw_line
            and NESTED_TERNARY_RE.search(raw_line) is not None
        )
        has_quad_reduce = QUADRATIC_REDUCE_RE.search(raw_line) is not None
        if is_deep or has_nested_ternary or has_quad_reduce:
            add_finding(
                line_no,
                "DEEP_NESTING_OR_COMPLEX_FLOW",
                ["maintainability", "complex_control_flow"],
                raw_line,
            )
            if len(findings) >= max_remaining:
                break

        # 7. MISLEADING_OR_PASS_THROUGH
        pure_match = PURE_NAMED_FUNC_RE.search(raw_line)
        if pure_match is not None and has_side_effect_in_window(lines, idx_zero):
            add_finding(
                line_no,
                "MISLEADING_OR_PASS_THROUGH",
                ["naming_contract", "hidden_side_effect"],
                raw_line,
            )
        elif PASS_THROUGH_FN_RE.match(raw_line) is not None:
            add_finding(
                line_no,
                "MISLEADING_OR_PASS_THROUGH",
                ["ai_slop", "redundant_wrapper"],
                raw_line,
            )

    return findings, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scan_diff_smells.py",
        description="Extract 7 canonical AI Slop and structural smell candidates from added/modified diff lines.",
    )
    parser.add_argument("--session", default=None, help="Session directory path.")
    parser.add_argument("--workdir", "--repo", dest="workdir", default=None, help="Standalone workdir path.")
    parser.add_argument("--base", default=None, help="Base commit SHA for standalone mode.")
    parser.add_argument("--head", default=None, help="Head commit SHA for standalone mode.")
    parser.add_argument("--range", default=None, help="Commit range <base>..<head> for standalone mode.")
    parser.add_argument("--local", action="store_true", help="Scan uncommitted local changes in standalone mode.")
    parser.add_argument("--audit", nargs="*", default=None, help="Audit mode paths in standalone mode.")
    parser.add_argument("--max-findings", type=int, default=50, help="Maximum findings to output (default: 50).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    max_findings = max(1, int(args.max_findings))

    session_dir: Path | None = None
    state: dict[str, object] = {}
    audit_paths: list[str] = []
    preset_changed: list[str] | None = None

    if args.session is not None:
        session_dir = Path(str(args.session)).expanduser().resolve()
        state = read_verified_gate_state(session_dir)

        stage2 = state.get("stage2")
        if not isinstance(stage2, dict) or stage2.get("executed") is not True:
            fail_with_code(
                "Stage 3 blocked: Stage 2 (run_checks.py --session) must be executed before scan_diff_smells.py.",
                2,
            )
        expected_log_sha = str(stage2.get("log_sha256") or "")
        ci_log = session_dir / "ci_checks.log"
        if not ci_log.is_file() or not expected_log_sha or sha256_file(ci_log) != expected_log_sha:
            fail_with_code("Stage 3 blocked: Stage 2 ci_checks.log missing or SHA-256 mismatch.", 2)

        workdir = Path(str(state.get("workdir") or "")).resolve()
        repo_dir = Path(str(state.get("repo_dir") or workdir)).resolve()
        mode = str(state.get("mode") or "local")
        merge_base_sha = str(state.get("merge_base_sha") or state.get("base_sha") or "")
        head_sha = str(state.get("head_sha") or "")
        ap_raw = state.get("audit_paths")
        if isinstance(ap_raw, list):
            audit_paths = [str(x) for x in ap_raw]
        ch_raw = state.get("changed_files")
        if isinstance(ch_raw, list):
            preset_changed = [str(x) for x in ch_raw]
    elif args.workdir is not None:
        workdir = Path(str(args.workdir)).expanduser().resolve()
        repo_dir = workdir
        if not workdir.is_dir():
            fail_with_code(f"--workdir does not exist: {workdir}", 2)
        if args.audit is not None:
            mode = "audit"
            merge_base_sha = ""
            head_sha = ""
            audit_paths = [str(x) for x in args.audit]
        elif args.range:
            mode = "range"
            spec = str(args.range)
            left, right = spec.split("...", 1) if "..." in spec else spec.split("..", 1) if ".." in spec else ("", "")
            if not left or not right:
                fail_with_code(f"Invalid --range: {spec}", 2)
            mb_res = run_git(["git", "merge-base", left.strip(), right.strip()], cwd=workdir)
            hd_res = run_git(["git", "rev-parse", right.strip()], cwd=workdir)
            merge_base_sha = mb_res.stdout.strip() if mb_res and mb_res.returncode == 0 else left.strip()
            head_sha = hd_res.stdout.strip() if hd_res and hd_res.returncode == 0 else right.strip()
        elif args.base and args.head:
            mode = "range"
            mb_res = run_git(["git", "merge-base", str(args.base), str(args.head)], cwd=workdir)
            merge_base_sha = mb_res.stdout.strip() if mb_res and mb_res.returncode == 0 else str(args.base)
            head_sha = str(args.head)
        else:
            mode = "local"
            merge_base_sha = ""
            head_sha = ""
    else:
        fail_with_code("Specify either --session <dir> or --workdir <dir>.", 2)

    targets, skipped_files = collect_target_lines(
        workdir=workdir,
        repo_dir=repo_dir,
        mode=mode,
        merge_base_sha=merge_base_sha,
        head_sha=head_sha,
        audit_paths=audit_paths,
        preset_changed=preset_changed,
    )

    started = time.monotonic()
    all_findings: list[dict[str, object]] = []

    for rel_file in sorted(targets):
        if time.monotonic() - started > TOTAL_TIMEOUT_SECONDS:
            skipped_files.append({"file": rel_file, "reason": "global_timeout"})
            continue
        remaining = max_findings - len(all_findings)
        if remaining <= 0:
            break
        fpath = workdir / rel_file
        if not fpath.is_file() or fpath.is_symlink():
            continue
        file_findings, skip_reason = scan_single_file(rel_file, fpath, targets[rel_file], remaining)
        all_findings.extend(file_findings)
        if skip_reason is not None:
            skipped_files.append({"file": rel_file, "reason": skip_reason})

    summary_by_rule: dict[str, int] = {rule_id: 0 for rule_id in ALL_RULE_IDS}
    for item in all_findings:
        rid = str(item.get("rule_id") or "")
        if rid in summary_by_rule:
            summary_by_rule[rid] += 1

    output: dict[str, object] = {
        "notice": (
            "Candidate signals only — inspect surrounding real code, existing shared modules, and "
            "references/judgment.md (4 dismissal patterns + 6-point invariants) before reporting."
        ),
        "summary_by_rule": summary_by_rule,
        "findings": all_findings,
        "skipped_files": skipped_files,
    }

    if session_dir is not None:
        smells_path = session_dir / "diff_smells.json"
        write_json_atomic(smells_path, output)
        flagged_files = sorted(
            {
                str(item["file"])
                for item in all_findings
                if isinstance(item, dict) and isinstance(item.get("file"), str) and item["file"]
            }
        )
        state["stage3"] = {
            "smell_scan_executed": True,
            "smell_sha256": sha256_file(smells_path),
            "smell_count": len(all_findings),
            "flagged_files": flagged_files,
            "updated_at": utc_now_iso(),
        }
        write_sealed_gate_state(session_dir, state)

    emit_json(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
