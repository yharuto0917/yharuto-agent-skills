#!/usr/bin/env python3
"""Stage 5 GitHub Pending Review publisher (HMAC lock, blob URL rewriting, 422-safe routing, no 'event' key)."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import (  # noqa: E402
    MARKDOWN_LINK_RE,
    build_git_diff_cmd,
    emit_json,
    evaluate_skip_test_stage2_status,
    fail_with_code,
    find_fenced_lines,
    get_state_version,
    has_stage2_test_failure,
    is_stage1_passed_or_skipped,
    normalize_rel_path,
    parse_diff_header_file_path,
    read_verified_gate_state,
    run_subproc,
    session_state_lock,
    sha256_bytes,
    sha256_file,
    split_markdown_table_row,
    write_json_atomic,
)

API_TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 20

REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ANCHOR_LINE_RE = re.compile(r"^L(\d+)(?:-L?(\d+))?$")
LABEL_LINE_SUFFIX_RE = re.compile(r":L?(\d+)(?:-L?(\d+))?$")
FINDING_H3_RE = re.compile(r"^### (🔴 Bug|🟡 Risk|🔵 Nit|❓ Question): ")


@dataclass(frozen=True)
class SectionRange:
    heading_line: int
    heading_text: str
    start_line: int
    end_line: int


def strip_dot_slash(raw_path: str) -> str:
    return normalize_rel_path(raw_path)


def convert_relative_links_to_blob(
    markdown_text: str,
    repo_slug: str,
    head_sha: str,
) -> str:
    lines = markdown_text.splitlines()
    fenced = find_fenced_lines(lines)
    converted_lines: list[str] = []

    def replace_match(match: re.Match[str]) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        if (
            target.startswith("http://")
            or target.startswith("https://")
            or target.startswith("mailto:")
            or target.startswith("#")
            or target.startswith("file:")
        ):
            return match.group(0)
        cleaned = strip_dot_slash(target)
        if "#" in cleaned:
            rel_path, anchor = cleaned.split("#", 1)
            rel_path = rel_path.lstrip("/")
            if not rel_path:
                return match.group(0)
            blob_url = f"https://github.com/{repo_slug}/blob/{head_sha}/{rel_path}#{anchor}"
        else:
            rel_path = cleaned.lstrip("/")
            if not rel_path:
                return match.group(0)
            blob_url = f"https://github.com/{repo_slug}/blob/{head_sha}/{rel_path}"
        return f"[{label}]({blob_url})"

    for idx_zero, raw_line in enumerate(lines):
        ln = idx_zero + 1
        if ln in fenced:
            converted_lines.append(raw_line)
        else:
            converted_lines.append(MARKDOWN_LINK_RE.sub(replace_match, raw_line))

    return "\n".join(converted_lines)


def _parse_location_target(label: str, raw_target: str) -> tuple[str, int | None, int | None] | None:
    target = raw_target.strip()
    if (
        not target
        or target.startswith("http://")
        or target.startswith("https://")
        or target.startswith("mailto:")
        or target.startswith("file:")
        or target.startswith("#")
        or target.startswith("/")
    ):
        return None

    cleaned = strip_dot_slash(target)
    if "#" in cleaned:
        rel_path, anchor = cleaned.split("#", 1)
        if not rel_path:
            return None
        am = ANCHOR_LINE_RE.match(anchor.strip())
        if am is not None:
            s_ln = int(am.group(1))
            e_ln = int(am.group(2)) if am.group(2) is not None else s_ln
            return rel_path, s_ln, e_ln
        return rel_path, None, None

    lm_target = LABEL_LINE_SUFFIX_RE.search(cleaned)
    if lm_target is not None:
        rel_path = cleaned[: lm_target.start()]
        if not rel_path:
            return None
        s_ln = int(lm_target.group(1))
        e_ln = int(lm_target.group(2)) if lm_target.group(2) is not None else s_ln
        return rel_path, s_ln, e_ln

    if label:
        lm_label = LABEL_LINE_SUFFIX_RE.search(label)
        if lm_label is not None:
            s_ln = int(lm_label.group(1))
            e_ln = int(lm_label.group(2)) if lm_label.group(2) is not None else s_ln
            return cleaned, s_ln, e_ln

    return cleaned, None, None


def extract_primary_location(subsection_lines: Sequence[str]) -> tuple[str, int | None, int | None] | None:
    """Extract (rel_path, start_line, end_line) from 4-col table row 1 OR concise first bullet."""
    fenced = find_fenced_lines(subsection_lines)

    first_body_idx: int | None = None
    for idx_zero in range(1, len(subsection_lines)):
        if (idx_zero + 1) in fenced:
            continue
        if subsection_lines[idx_zero].strip():
            first_body_idx = idx_zero
            break

    if first_body_idx is None:
        return None

    first_stripped = subsection_lines[first_body_idx].strip()

    # Form ②: Concise table-free bullet list (- ... or * ...)
    if first_stripped.startswith("- ") or first_stripped.startswith("* "):
        bullet_parts = [first_stripped]
        for next_idx in range(first_body_idx + 1, len(subsection_lines)):
            if (next_idx + 1) in fenced:
                break
            raw_next = subsection_lines[next_idx]
            st_next = raw_next.strip()
            if not st_next or st_next.startswith("- ") or st_next.startswith("* "):
                break
            if raw_next.startswith("  ") or raw_next.startswith("\t"):
                bullet_parts.append(st_next)
            else:
                break
        first_bullet_text = " ".join(bullet_parts)
        for m in MARKDOWN_LINK_RE.finditer(first_bullet_text):
            loc = _parse_location_target(m.group(1).strip(), m.group(2).strip())
            if loc is not None:
                return loc
        return None

    # Form ①: 4-column Markdown table
    table_header_seen = False
    separator_seen = False
    for idx_zero in range(first_body_idx, len(subsection_lines)):
        ln = idx_zero + 1
        if ln in fenced:
            continue
        stripped = subsection_lines[idx_zero].strip()
        if not stripped.startswith("|"):
            if separator_seen:
                break
            continue
        if not table_header_seen:
            table_header_seen = True
            continue
        if not separator_seen:
            separator_seen = True
            continue

        cells = split_markdown_table_row(stripped)
        if not cells:
            return None
        first_cell = cells[0]
        m = MARKDOWN_LINK_RE.search(first_cell)
        label = m.group(1).strip() if m is not None else ""
        target = m.group(2).strip() if m is not None else first_cell.strip("` ")
        return _parse_location_target(label, target)

    return None


def compute_diff_hunks(repo_dir: Path, merge_base_sha: str, head_sha: str) -> dict[str, list[set[int]]]:
    if not merge_base_sha or not head_sha:
        return {}
    proc = run_subproc(
        build_git_diff_cmd("-U3", merge_base_sha, head_sha),
        cwd=repo_dir,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc is None or proc.returncode != 0:
        return {}

    hunks_by_file: dict[str, list[set[int]]] = {}
    current_file: str | None = None
    current_hunk: set[int] | None = None
    in_header = False
    right_line = 0

    for raw in proc.stdout.splitlines():
        if raw.startswith("diff --git "):
            current_file = None
            current_hunk = None
            in_header = True
            continue
        if in_header:
            if raw.startswith("+++ "):
                parsed_plus = parse_diff_header_file_path(raw, "+++ ")
                if parsed_plus is None or parsed_plus == "/dev/null":
                    current_file = None
                else:
                    current_file = parsed_plus
                current_hunk = None
                continue
            hm = HUNK_HEADER_RE.match(raw)
            if hm is not None:
                in_header = False
                if current_file is None:
                    continue
                right_line = int(hm.group(1))
                current_hunk = set()
                hunks_by_file.setdefault(current_file, []).append(current_hunk)
            continue

        if current_file is None:
            continue

        hm = HUNK_HEADER_RE.match(raw)
        if hm is not None:
            right_line = int(hm.group(1))
            current_hunk = set()
            hunks_by_file.setdefault(current_file, []).append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if raw.startswith("+") and not raw.startswith("+++"):
            current_hunk.add(right_line)
            right_line += 1
        elif raw.startswith(" "):
            current_hunk.add(right_line)
            right_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue

    return hunks_by_file


def resolve_inline_placement(
    hunks_by_file: Mapping[str, Sequence[set[int]]],
    rel_path: str,
    start_line: int | None,
    end_line: int | None,
) -> tuple[int | None, int | None]:
    """Return (resolved_start_line_or_None, resolved_end_line_or_None). Second is None if out-of-diff."""
    file_hunks = hunks_by_file.get(rel_path)
    if not file_hunks or end_line is None:
        return None, None

    if start_line is not None and start_line < end_line:
        for hunk in file_hunks:
            if start_line in hunk and end_line in hunk:
                return start_line, end_line

    for hunk in file_hunks:
        if end_line in hunk:
            return None, end_line

    if start_line is not None:
        for hunk in file_hunks:
            if start_line in hunk:
                return None, start_line

    return None, None


def slice_by_heading(
    lines: Sequence[str],
    fenced: set[int],
    start_ln: int,
    end_ln: int,
    prefix: str,
) -> list[SectionRange]:
    pts: list[tuple[int, str]] = []
    for ln in range(start_ln, end_ln + 1):
        if ln in fenced:
            continue
        raw = lines[ln - 1]
        if raw.startswith(prefix) and not raw.startswith(prefix.rstrip() + "#"):
            pts.append((ln, raw))
    out: list[SectionRange] = []
    for idx, (h_ln, h_txt) in enumerate(pts):
        s_end = pts[idx + 1][0] - 1 if idx + 1 < len(pts) else end_ln
        out.append(SectionRange(heading_line=h_ln, heading_text=h_txt, start_line=h_ln, end_line=s_end))
    return out


def build_pending_review_payload(
    review_text: str,
    repo_slug: str,
    head_sha: str,
    review_mode: str,
    hunks_by_file: Mapping[str, Sequence[set[int]]],
) -> tuple[dict[str, object], int, int]:
    if review_mode == "stage1_ng":
        converted_full = convert_relative_links_to_blob(review_text, repo_slug, head_sha).strip()
        payload_ng: dict[str, object] = {
            "commit_id": head_sha,
            "body": converted_full,
            "comments": [],
        }
        return payload_ng, 0, 0

    lines = review_text.splitlines()
    fenced = find_fenced_lines(lines)
    h2s = slice_by_heading(lines, fenced, 1, len(lines), "## ")
    findings_h2 = next((s for s in h2s if s.heading_text.strip() == "## 指摘事項"), None)

    if findings_h2 is None:
        converted = convert_relative_links_to_blob(review_text, repo_slug, head_sha).strip()
        return {"commit_id": head_sha, "body": converted, "comments": []}, 0, 0

    h3s = slice_by_heading(
        lines,
        fenced,
        findings_h2.start_line + 1,
        findings_h2.end_line,
        "### ",
    )
    if not h3s:
        converted = convert_relative_links_to_blob(review_text, repo_slug, head_sha).strip()
        return {"commit_id": head_sha, "body": converted, "comments": []}, 0, 0

    inline_comments: list[dict[str, object]] = []
    fallback_blocks: list[str] = []

    for sub in h3s:
        sub_lines = lines[sub.start_line - 1 : sub.end_line]
        sub_md_raw = "\n".join(sub_lines).strip()
        sub_md_blob = convert_relative_links_to_blob(sub_md_raw, repo_slug, head_sha)

        if FINDING_H3_RE.match(sub.heading_text.strip()) is not None:
            loc = extract_primary_location(sub_lines)
            if loc is not None:
                rel_path, s_line, e_line = loc
                res_start, res_end = resolve_inline_placement(hunks_by_file, rel_path, s_line, e_line)
                if res_end is not None:
                    comment_obj: dict[str, object] = {
                        "path": rel_path,
                        "line": res_end,
                        "side": "RIGHT",
                        "body": sub_md_blob,
                    }
                    if res_start is not None and res_start < res_end:
                        comment_obj["start_line"] = res_start
                        comment_obj["start_side"] = "RIGHT"
                    inline_comments.append(comment_obj)
                    continue

        fallback_blocks.append(sub_md_blob)

    before_findings = "\n".join(lines[: findings_h2.start_line - 1]).strip()
    after_findings = "\n".join(lines[findings_h2.end_line :]).strip()

    if fallback_blocks and inline_comments:
        findings_body_section = (
            "## 指摘事項\n\n"
            f"差分行内の指摘（計{len(inline_comments)}件）は該当ファイルのインラインコメントに記載しています。\n\n"
            + "\n\n".join(fallback_blocks)
        )
    elif fallback_blocks:
        findings_body_section = "## 指摘事項\n\n" + "\n\n".join(fallback_blocks)
    else:
        findings_body_section = (
            "## 指摘事項\n\n"
            f"各指摘（計{len(inline_comments)}件）は該当ファイルのインラインコメントに記載しています。"
        )

    top_parts = [
        convert_relative_links_to_blob(before_findings, repo_slug, head_sha),
        findings_body_section,
        convert_relative_links_to_blob(after_findings, repo_slug, head_sha),
    ]
    top_body = "\n\n".join(part for part in top_parts if part)

    # IMPORTANT: Never include 'event' key so GitHub keeps the review in PENDING draft state.
    payload: dict[str, object] = {
        "commit_id": head_sha,
        "body": top_body,
        "comments": inline_comments,
    }
    assert "event" not in payload
    return payload, len(inline_comments), len(fallback_blocks)


def check_existing_pending_review(repo_slug: str, pr_number: int, cwd: Path) -> int | None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/pulls/{pr_number}/reviews"],
            cwd=str(cwd),
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=API_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail_with_code(f"Failed to query existing PR reviews via gh api: {exc}", 2)

    if proc.returncode != 0:
        fail_with_code(f"gh api GET reviews failed: {proc.stderr.strip()}", 2)

    try:
        parsed: object = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail_with_code(f"Invalid JSON from gh api GET reviews: {exc}", 2)

    if isinstance(parsed, list):
        for rev in parsed:
            if isinstance(rev, dict) and str(rev.get("state") or "").upper() == "PENDING":
                rid = rev.get("id")
                return rid if isinstance(rid, int) else -1
    return None


def post_pending_via_gh(
    repo_slug: str,
    pr_number: int,
    payload: Mapping[str, object],
    cwd: Path,
) -> dict[str, object]:
    if "event" in payload:
        fail_with_code("Security invariant violation: 'event' key must never be present in Pending Review payload.", 2)

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{repo_slug}/pulls/{pr_number}/reviews", "--input", "-"],
            input=json.dumps(payload, ensure_ascii=False),
            cwd=str(cwd),
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=API_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail_with_code(f"Failed to POST pending review via gh api: {exc}", 2)

    if proc.returncode != 0:
        fail_with_code(f"gh api POST pending review failed: {proc.stderr.strip()}", 2)

    try:
        resp: object = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail_with_code(f"Invalid JSON from gh api POST pending review: {exc}", 2)

    return {str(k): v for k, v in resp.items()} if isinstance(resp, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="post_pending_review.py",
        description="Publish validated review output as a GitHub PENDING pull request review.",
    )
    parser.add_argument("--session", required=True, help="Session directory path.")
    parser.add_argument("--repo", required=True, help="GitHub repository slug <owner/repo>.")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number.")
    parser.add_argument("--review-md", "--file", dest="review_md", required=True, help="Validated review.md path.")
    parser.add_argument("--dry-run", action="store_true", help="Build payload and rewrite URLs without calling GitHub API.")
    return parser


def _verify_post_gates_locked(
    session_dir: Path,
    review_path: Path,
    *,
    expected_version: int | None = None,
    expected_review_sha: str | None = None,
) -> tuple[dict[str, object], str, bytes, str, int]:
    """Verify all Stage 1-5 gates, ci_checks.log, and review.md digest atomically under session_state_lock."""
    with session_state_lock(session_dir, create_dir=False):
        raw_state = read_verified_gate_state(session_dir)
        current_ver = get_state_version(raw_state)
        if expected_version is not None and current_ver != expected_version:
            fail_with_code(
                f"Cannot post review: gate_state.json changed concurrently before posting "
                f"(expected state_version={expected_version}, current={current_ver}). Re-validate before posting.",
                2,
                extra={
                    "expected_state_version": expected_version,
                    "current_state_version": current_ver,
                },
            )

        if not review_path.is_file():
            fail_with_code(f"Review Markdown file not found: {review_path}", 2)
        try:
            review_bytes = review_path.read_bytes()
        except OSError as exc:
            fail_with_code(f"Cannot read review Markdown file ({review_path}): {exc}", 2)

        stage5 = raw_state.get("stage5")
        if not isinstance(stage5, dict) or stage5.get("validated") is not True:
            fail_with_code(
                "Cannot post review: stage5.validated is not true. Run validate_review_output.py --session first.",
                2,
            )

        expected_sha = str(stage5.get("review_sha256") or "")
        actual_sha = sha256_bytes(review_bytes)
        if not expected_sha or actual_sha != expected_sha:
            fail_with_code(
                "Cannot post review: review.md SHA-256 does not match stage5.review_sha256 (file modified after validation).",
                2,
            )
        if expected_review_sha is not None and actual_sha != expected_review_sha:
            fail_with_code(
                "Cannot post review: review.md changed concurrently before posting.",
                2,
            )

        s1_scan = raw_state.get("stage1_scan")
        if not isinstance(s1_scan, dict) or s1_scan.get("executed") is not True:
            fail_with_code("Cannot post review: impact_scan.py --session was not executed.", 2)

        stage1 = raw_state.get("stage1")
        s1_status = stage1.get("status") if isinstance(stage1, dict) else None
        bypass_status, bypass_reason = evaluate_skip_test_stage2_status(raw_state)
        if is_stage1_passed_or_skipped(raw_state, require_stage2_test_pass=True):
            stage2 = raw_state.get("stage2")
            stage3 = raw_state.get("stage3")
            stage4 = raw_state.get("stage4")
            if not (isinstance(stage2, dict) and stage2.get("executed") is True):
                fail_with_code("Cannot post review: Stage 2 was not executed.", 2)
            expected_log_sha = str(stage2.get("log_sha256") or "")
            ci_log_path = session_dir / "ci_checks.log"
            if not ci_log_path.is_file() or not expected_log_sha or sha256_file(ci_log_path) != expected_log_sha:
                fail_with_code("Cannot post review: Stage 2 ci_checks.log is missing or SHA-256 mismatch.", 2)
            if not (isinstance(stage3, dict) and stage3.get("smell_scan_executed") is True):
                fail_with_code("Cannot post review: Stage 3 (scan_diff_smells.py) was not executed.", 2)
            if not (
                isinstance(stage4, dict)
                and stage4.get("executed") is True
                and stage4.get("challenge_nonce_verified") is True
            ):
                fail_with_code("Cannot post review: Stage 4 adversarial verification was not completed.", 2)
            review_mode = "full"
        elif bypass_status in {"pending", "unverified"}:
            fail_with_code(
                f"Cannot post review: {bypass_reason}",
                2,
                extra={
                    "stage1_status": s1_status,
                    "skip_test_revoked": False,
                    "skip_test_on_hold": True,
                    "skip_test_hold_status": bypass_status,
                },
            )
        elif s1_status == "NG" or (
            s1_status == "SKIPPED_BY_FLAG"
            and (
                bool(raw_state.get("skip_test_revoked", False))
                or has_stage2_test_failure(raw_state)
                or bypass_status == "revoked"
            )
        ):
            review_mode = "stage1_ng"
        else:
            fail_with_code(f"Cannot post review: invalid Stage 1 status '{s1_status}'.", 2)

        stage5_mode = str(stage5.get("mode") or "").strip()
        if (stage5_mode == "stage1_ng") != (review_mode == "stage1_ng"):
            fail_with_code(
                f"Cannot post review: stage5 validated mode '{stage5_mode}' does not match current session review mode '{review_mode}'. "
                "Re-run validate_review_output.py --session.",
                2,
            )

        return raw_state, review_mode, review_bytes, actual_sha, current_ver


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_slug = str(args.repo).strip()
    if not REPO_SLUG_RE.match(repo_slug):
        fail_with_code(f"Invalid --repo '{repo_slug}': expected <owner/repo>.", 2)

    pr_number = int(args.pr)
    if pr_number < 1:
        fail_with_code(f"Invalid --pr '{pr_number}': must be >= 1.", 2)

    session_dir = Path(str(args.session)).expanduser().resolve()
    review_path = Path(str(args.review_md)).expanduser().resolve()
    raw_state, review_mode, review_bytes, actual_sha, initial_version = _verify_post_gates_locked(
        session_dir, review_path
    )

    repo_dir = Path(str(raw_state.get("repo_dir") or ".")).resolve()
    merge_base_sha = str(raw_state.get("merge_base_sha") or raw_state.get("base_sha") or "")
    head_sha = str(raw_state.get("head_sha") or "")
    if not head_sha:
        fail_with_code("Cannot post review: head_sha is empty in gate_state.json.", 2)

    try:
        review_text = review_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail_with_code(f"Cannot decode review Markdown file as UTF-8 ({review_path}): {exc}", 2)

    hunks_by_file = compute_diff_hunks(repo_dir, merge_base_sha, head_sha) if review_mode == "full" else {}
    payload, inline_count, body_fallback_count = build_pending_review_payload(
        review_text=review_text,
        repo_slug=repo_slug,
        head_sha=head_sha,
        review_mode=review_mode,
        hunks_by_file=hunks_by_file,
    )

    if "event" in payload:
        fail_with_code("Fatal invariant error: 'event' key found in payload.", 2)

    payload_out_path = session_dir / "pending_review_payload.json"
    write_json_atomic(payload_out_path, payload)

    if args.dry_run:
        emit_json(
            {
                "status": "dry_run_ok",
                "review_id": None,
                "inline_count": inline_count,
                "body_fallback_count": body_fallback_count,
                "commit_id": head_sha,
                "payload_path": str(payload_out_path),
                "payload": payload,
            }
        )
        return 0

    existing_id = check_existing_pending_review(repo_slug, pr_number, repo_dir)
    if existing_id is not None:
        emit_json(
            {
                "status": "aborted_existing_pending",
                "review_id": existing_id,
                "inline_count": 0,
                "body_fallback_count": 0,
            }
        )
        return 0

    # Re-verify session state, ci_checks.log, and review.md immediately before posting to GitHub.
    _verify_post_gates_locked(
        session_dir,
        review_path,
        expected_version=initial_version,
        expected_review_sha=actual_sha,
    )

    resp = post_pending_via_gh(repo_slug, pr_number, payload, repo_dir)
    emit_json(
        {
            "status": "created_pending",
            "review_id": resp.get("id"),
            "html_url": resp.get("html_url"),
            "inline_count": inline_count,
            "body_fallback_count": body_fallback_count,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
