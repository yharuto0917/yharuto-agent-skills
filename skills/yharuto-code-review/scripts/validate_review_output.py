#!/usr/bin/env python3
"""Stage 1 NG, Full & Audit mechanical gate, HMAC verifier, Markdown structure, and prose validator."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hmac
import json
from pathlib import Path
import re
from typing import Mapping, NoReturn, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import (  # noqa: E402
    ALLOWED_STAGE1_SKIP_STATUSES,
    FencedBlock,
    MARKDOWN_LINK_RE,
    SectionSlice,
    TEST_PATH_RE,
    collect_repo_porcelain,
    emit_json,
    evaluate_skip_test_stage2_status,
    get_state_version,
    has_stage2_test_failure,
    has_surviving_changed_files,
    is_deleted_changed_file_in_base,
    is_git_metadata_rel_path,
    is_separator_row,
    load_session_hmac_key,
    parse_fenced_blocks,
    save_review_history,
    session_state_lock,
    sha256_bytes,
    sha256_file,
    should_advance_review_history,
    slice_sections_by_prefix,
    split_markdown_table_row,
    utc_now_iso,
    verify_state_hmac,
    write_sealed_gate_state,
)
from _prose_lint import (  # noqa: E402
    EMOJI_RE,
    EXEMPT_FINDING_RE,
    EXEMPT_VERDICT_RE,
    run_prose_lint,
)

FINDING_CATEGORY_SPECS: tuple[tuple[str, int, str], ...] = (
    ("### 🔴 Bug: ", 0, "🔴 Bug"),
    ("### 🟡 Risk: ", 1, "🟡 Risk"),
    ("### 🔵 Nit: ", 2, "🔵 Nit"),
    ("### ❓ Question: ", 3, "❓ Question"),
)

VERDICT_TO_ALERT: dict[str, str] = {
    "🛑 Test Gate NG（テスト修正差し戻し）": "[!CAUTION]",
    "🔴 Request changes": "[!CAUTION]",
    "🟡 Comment": "[!WARNING]",
    "🟢 Approve": "[!TIP]",
    "🔴 要修正（重大な懸念あり）": "[!CAUTION]",
    "🟡 要確認（構造・保守上の懸念あり）": "[!WARNING]",
    "🟢 健全（重大な懸念なし）": "[!TIP]",
}

PR_MODE_VERDICTS: frozenset[str] = frozenset(
    {
        "🔴 Request changes",
        "🟡 Comment",
        "🟢 Approve",
    }
)
AUDIT_MODE_VERDICTS: frozenset[str] = frozenset(
    {
        "🔴 Request changes",
        "🟡 Comment",
        "🟢 Approve",
        "🔴 要修正（重大な懸念あり）",
        "🟡 要確認（構造・保守上の懸念あり）",
        "🟢 健全（重大な懸念なし）",
    }
)
RED_VERDICTS: frozenset[str] = frozenset(
    {
        "🔴 Request changes",
        "🔴 要修正（重大な懸念あり）",
    }
)
GREEN_VERDICTS: frozenset[str] = frozenset(
    {
        "🟢 Approve",
        "🟢 健全（重大な懸念なし）",
    }
)
YELLOW_VERDICTS: frozenset[str] = frozenset(
    {
        "🟡 Comment",
        "🟡 要確認（構造・保守上の懸念あり）",
    }
)

PR_OVERVIEW_HEADER_TUPLES: tuple[tuple[str, str, str], ...] = (
    ("ファイル", "変更種別", "役割と主な変更内容"),
)
AUDIT_OVERVIEW_HEADER_TUPLES: tuple[tuple[str, str, str], ...] = (
    ("ファイル", "種別", "役割と主な内容"),
    ("ファイル", "変更種別", "役割と主な変更内容"),
)
ALLOWED_FINDING_HEADER_TUPLES: tuple[tuple[str, str, str, str], ...] = (
    ("箇所", "対象", "型・契約", "処理内容と目的"),
    ("箇所", "対象", "引数と返り値", "処理内容と目的"),
)

ZERO_FINDINGS_SENTENCE = (
    "対応が必要な不具合（Bug）・懸念（Risk）および変更コードへの軽微な提案（Nit）・確認質問（Question）は検出されませんでした。"
)
ZERO_FINDINGS_REQUIRED_SUBSTR = "検出されませんでした。"

TEMP_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("session_temp_dir", re.compile(r"yharuto-(?:review|eval)-[A-Za-z0-9_-]+")),
    ("gemini_tmp_dir", re.compile(r"(?:~|/)?\.gemini/tmp/")),
    ("macos_var_folders", re.compile(r"(?:/private)?/var/folders/")),
    ("tmp_path", re.compile(r"(?:/private)?/tmp/")),
    ("file_uri_scheme", re.compile(r"file:///")),
)

OVERVIEW_JUDGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:バグ|不具合|脆弱性|欠陥)が(?:あり|ある|存在|見つかり|残っ)"),
    re.compile(r"テストが(?:不足|不十分|欠落|書かれておらず|存在せず)"),
    re.compile(r"(?:問題があり|危険な実装|修正が必要|差し戻します|改善が必要)"),
    re.compile(r"(?:AI\s*Slop|二重実装になって|握りつぶして)", re.IGNORECASE),
    re.compile(r"[🛑🔴🟡🟢🔵❓]"),
)

PR_META_SUBJECT_RE = re.compile(
    r"(?:PR|Pull\s*Request|プルリク(?:エスト)?)\s*(?:の)?\s*"
    r"(?:概要|本文|説明文?|タイトル|テンプレート|ラベル|チェックリスト)",
    re.IGNORECASE,
)
PR_META_ACTION_RE = re.compile(
    r"(?:更新|記載|記入|追記|漏れ|不足|修正|乖離|未記入|未記載|未更新|空欄|TODO|書かれ)",
    re.IGNORECASE,
)
NIT_QUESTION_META_LINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pr_metadata_remark",
        re.compile(
            r"(?:PR|Pull\s*Request|プルリク(?:エスト)?)\s*(?:の)?\s*"
            r"(?:概要|本文|説明文?|タイトル|テンプレート|ラベル|チェックリスト)"
            r".*(?:更新|記載|記入|追記|漏れ|不足|修正|乖離|未記入|未記載|未更新|空欄|TODO|書かれ)",
            re.IGNORECASE,
        ),
    ),
    (
        "pr_metadata_remark_reversed",
        re.compile(
            r"(?:更新漏れ|記載漏れ|記入漏れ|追記漏れ|記載不足|未記入|未記載|未更新|空欄|TODO)"
            r".*(?:PR|Pull\s*Request|プルリク(?:エスト)?)\s*(?:の)?\s*"
            r"(?:概要|本文|説明文?|タイトル|テンプレート|ラベル|チェックリスト)",
            re.IGNORECASE,
        ),
    ),
    (
        "commit_message_remark",
        re.compile(r"(?:コミット\s*メッセージ|commit\s+messages?)", re.IGNORECASE),
    ),
    (
        "issue_link_metadata_remark",
        re.compile(
            r"(?:Issue|イシュー)\s*(?:リンク|番号)\s*(?:の)?\s*"
            r"(?:記載|記入|追記|漏れ|不足|未記載|未記入)",
            re.IGNORECASE,
        ),
    ),
)


def fail_cli_usage(message: str, code: int = 2) -> NoReturn:
    emit_json({"passed": False, "error": message, "exit_code": code})
    sys.exit(code)


def clean_header_cell(cell: str) -> str:
    return cell.replace("*", "").replace("`", "").strip()


def _normalize_rel_path(raw_str: str) -> str:
    cleaned = raw_str.strip().strip("`")
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = re.sub(r":L?\d+(?:-L?\d+)?$", "", cleaned)
    cleaned = cleaned.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip()


def validate_cell_links(
    cell: str,
    line_no: int,
    structural_errors: list[str],
    require_repo_relative: bool = True,
) -> None:
    links = MARKDOWN_LINK_RE.findall(cell)
    if len(links) > 1:
        structural_errors.append(
            f"L{line_no}: Table cell contains {len(links)} links (maximum 1 link per cell allowed): {cell[:80]}"
        )
    for _, target in links:
        t = target.strip()
        if require_repo_relative and (t.startswith("/") or t.startswith("file:")):
            structural_errors.append(
                f"L{line_no}: Link target '{t}' must be repository-relative (absolute/file:// paths are prohibited)."
            )


def find_table_in_range(
    lines: Sequence[str],
    fenced_lines: set[int],
    start_line: int,
    end_line: int,
    allowed_header_tuples: Sequence[Sequence[str]],
) -> tuple[int, int, list[tuple[int, list[str]]]] | None:
    normalized_allowed = [tuple(h) for h in allowed_header_tuples]
    line_no = start_line
    while line_no <= end_line:
        if line_no in fenced_lines or line_no + 1 > end_line or (line_no + 1) in fenced_lines:
            line_no += 1
            continue
        raw = lines[line_no - 1].strip()
        next_raw = lines[line_no].strip()
        if raw.startswith("|") and next_raw.startswith("|"):
            hdr_cells = tuple(clean_header_cell(c) for c in split_markdown_table_row(raw))
            sep_cells = split_markdown_table_row(next_raw)
            if hdr_cells in normalized_allowed and is_separator_row(sep_cells, len(hdr_cells)):
                rows: list[tuple[int, list[str]]] = []
                row_ln = line_no + 2
                while row_ln <= end_line and row_ln not in fenced_lines:
                    r_str = lines[row_ln - 1].strip()
                    if not r_str.startswith("|"):
                        break
                    rows.append((row_ln, split_markdown_table_row(r_str)))
                    row_ln += 1
                return line_no, row_ln - 1, rows
        line_no += 1
    return None


def _verify_smell_accountability_soft(
    session_dir: Path,
    gate_errors: list[str],
) -> None:
    smells_path = session_dir / "diff_smells.json"
    draft_path = session_dir / "draft_findings.json"
    if not smells_path.is_file():
        gate_errors.append("Stage 3 diff_smells.json is missing.")
        return
    if not draft_path.is_file():
        gate_errors.append("Stage 4 draft_findings.json is missing.")
        return

    try:
        smells_obj: object = json.loads(smells_path.read_text(encoding="utf-8"))
        draft_obj: object = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        gate_errors.append(f"Failed to parse diff_smells.json or draft_findings.json: {exc}")
        return

    if not isinstance(smells_obj, dict):
        gate_errors.append("diff_smells.json is not a JSON object.")
        return
    raw_smells = smells_obj.get("findings")
    if not isinstance(raw_smells, list) or len(raw_smells) == 0:
        return

    findings_list: list[object] = []
    dismissed_list: list[object] = []
    if isinstance(draft_obj, list):
        findings_list = draft_obj
    elif isinstance(draft_obj, dict):
        f_cand = draft_obj.get("findings", draft_obj.get("draft_findings"))
        if isinstance(f_cand, list):
            findings_list = f_cand
        d_cand = draft_obj.get("dismissed_smells")
        if isinstance(d_cand, list):
            dismissed_list = d_cand

    finding_files: set[str] = set()
    for item in findings_list:
        if isinstance(item, dict):
            f_norm = _normalize_rel_path(str(item.get("file") or item.get("path") or item.get("location") or ""))
            if f_norm:
                finding_files.add(f_norm)

    dismissed_pairs: set[tuple[str, str]] = set()
    for d_item in dismissed_list:
        if isinstance(d_item, dict):
            r_id = str(d_item.get("rule_id") or "").strip()
            d_file = _normalize_rel_path(str(d_item.get("file") or d_item.get("path") or ""))
            d_reason = str(d_item.get("reason") or "").strip()
            if r_id and d_file and len(d_reason) >= 10:
                dismissed_pairs.add((r_id, d_file))

    for s_entry in raw_smells:
        if not isinstance(s_entry, dict):
            continue
        s_file = _normalize_rel_path(str(s_entry.get("file") or ""))
        s_rule = str(s_entry.get("rule_id") or "").strip()
        if not s_file or not s_rule:
            continue
        covered_by_finding = any(
            ff == s_file or s_file.endswith("/" + ff) or ff.endswith("/" + s_file)
            for ff in finding_files
        )
        covered_by_dismissal = (s_rule, s_file) in dismissed_pairs
        if not covered_by_finding and not covered_by_dismissal:
            gate_errors.append(
                f"Stage 3 smell '{s_rule}' on '{s_file}' is neither included in draft_findings nor explained in dismissed_smells (reason >= 10 chars)."
            )
            break


def _verify_stage4_response_soft(
    session_dir: Path,
    state: Mapping[str, object],
    stage4: Mapping[str, object],
    workdir: Path,
    gate_errors: list[str],
) -> None:
    in_path = Path(str(stage4.get("input_path") or (session_dir / "adversarial_input.md")))
    in_sha = str(stage4.get("input_sha256") or "")
    resp_path = Path(str(stage4.get("response_path") or (session_dir / "adversarial_response.json")))
    resp_sha = str(stage4.get("response_sha256") or "")

    if not in_path.is_file() or not in_sha or sha256_file(in_path) != in_sha:
        gate_errors.append("Stage 4 adversarial_input.md is missing or SHA-256 mismatch.")
        return
    if not resp_path.is_file() or not resp_sha or sha256_file(resp_path) != resp_sha:
        gate_errors.append("Stage 4 adversarial_response.json is missing or SHA-256 mismatch.")
        return

    if stage4.get("challenge_nonce_verified") is not True:
        gate_errors.append("Stage 4 challenge_nonce_verified is not true in gate_state.json.")

    try:
        input_text = in_path.read_text(encoding="utf-8")
        raw_resp: object = json.loads(resp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        gate_errors.append(f"Failed to read Stage 4 evidence files: {exc}")
        return

    if not isinstance(raw_resp, dict):
        gate_errors.append("Stage 4 adversarial_response.json must be a JSON object.")
        return

    expected_nonce_sha = str(stage4.get("challenge_nonce_sha256") or "")
    provided_nonce = raw_resp.get("challenge_nonce")
    if not isinstance(provided_nonce, str) or not provided_nonce.strip():
        gate_errors.append("Stage 4 adversarial_response.json is missing 'challenge_nonce'.")
    else:
        clean_nonce = provided_nonce.strip()
        actual_nonce_sha = sha256_bytes(clean_nonce.encode("utf-8"))
        if not expected_nonce_sha or not hmac.compare_digest(actual_nonce_sha, expected_nonce_sha):
            gate_errors.append("Stage 4 challenge_nonce SHA-256 mismatch.")
        elif clean_nonce not in input_text:
            gate_errors.append("Stage 4 challenge_nonce does not match adversarial_input.md.")

    audit_notes = raw_resp.get("audit_notes")
    if not isinstance(audit_notes, str) or len(audit_notes.strip()) < 20:
        gate_errors.append("Stage 4 adversarial_response.json 'audit_notes' must be >= 20 characters.")

    challenged = raw_resp.get("challenged_findings")
    raw_draft_ids = stage4.get("draft_ids", [])
    expected_draft_ids = [str(x) for x in raw_draft_ids] if isinstance(raw_draft_ids, list) else []
    if not isinstance(challenged, list):
        gate_errors.append("Stage 4 adversarial_response.json 'challenged_findings' must be a list.")
    else:
        seen_cids: set[str] = set()
        for idx, c_item in enumerate(challenged):
            if not isinstance(c_item, dict):
                gate_errors.append(f"Stage 4 challenged_findings[{idx}] is not an object.")
                continue
            cid = str(c_item.get("id") or "").strip()
            c_verdict = str(c_item.get("verdict") or "").strip().lower()
            c_reason = c_item.get("reason")
            if cid:
                seen_cids.add(cid)
            if c_verdict not in {"keep", "dismiss", "revise"}:
                gate_errors.append(f"Stage 4 challenged_findings[{idx}] has invalid verdict '{c_item.get('verdict')}'.")
            if not isinstance(c_reason, str) or len(c_reason.strip()) < 10:
                gate_errors.append(f"Stage 4 challenged_findings[{idx}] ('{cid}') reason must be >= 10 characters.")
        missing_ids = [did for did in expected_draft_ids if did not in seen_cids]
        if missing_ids:
            gate_errors.append(f"Stage 4 challenged_findings did not cover all draft finding IDs: {missing_ids}.")

    checked_raw = raw_resp.get("checked_files")
    if not isinstance(checked_raw, list) or len(checked_raw) < 1:
        gate_errors.append("Stage 4 checked_files evidence is empty.")
        return

    normalized_checked: set[str] = set()
    live_checked_count = 0
    workdir_resolved = workdir.resolve()
    for item in checked_raw:
        if not isinstance(item, str) or not item.strip():
            continue
        cleaned_p = _normalize_rel_path(item)
        cand = Path(cleaned_p)
        full = cand.resolve() if cand.is_absolute() else (workdir_resolved / cand).resolve()
        try:
            rel = full.relative_to(workdir_resolved).as_posix()
        except ValueError:
            gate_errors.append(f"Stage 4 checked_files entry escapes workdir: {item}")
            continue
        if is_git_metadata_rel_path(rel):
            gate_errors.append(f"Stage 4 checked_files entry cannot reference git metadata: {rel}")
            continue
        if full.is_file():
            live_checked_count += 1
            normalized_checked.add(rel)
            continue
        if is_deleted_changed_file_in_base(state, workdir_resolved, rel):
            normalized_checked.add(rel)
            continue
        gate_errors.append(f"Stage 4 checked_files entry does not exist in workdir: {rel}")

    if not normalized_checked:
        gate_errors.append("Stage 4 checked_files did not resolve to any valid inspected file.")

    if has_surviving_changed_files(state, workdir_resolved) and live_checked_count < 1:
        gate_errors.append(
            "Stage 4 checked_files must include at least 1 inspected live file in workdir when selected files survive."
        )

    mode_val = str(state.get("mode") or "")
    testable_raw = state.get("testable_code_files")
    testable_files = [str(x) for x in testable_raw] if isinstance(testable_raw, list) else []
    if mode_val in {"pr", "range", "local"} and len(testable_files) > 0:
        covered = [tf for tf in testable_files if tf in normalized_checked]
        required_cov = len(testable_files) if len(testable_files) <= 10 else 10
        if len(covered) < required_cov:
            missing_tf = [tf for tf in testable_files if tf not in normalized_checked]
            gate_errors.append(
                f"Stage 4 checked_files must cover testable_code_files ({len(covered)}/{required_cov} covered; missing: {missing_tf[:10]})."
            )


def validate_gate_state(
    session_dir: Path,
) -> tuple[str, dict[str, object], list[str], bool, bool]:
    """Return (expected_mode, state_dict, gate_errors, has_stage2_failure, hmac_ok)."""
    gate_errors: list[str] = []
    state_path = session_dir / "gate_state.json"
    if not state_path.is_file():
        return "full", {}, [f"gate_state.json does not exist in session_dir: {session_dir}"], False, False

    with session_state_lock(session_dir, create_dir=False):
        try:
            raw_state: object = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return "full", {}, [f"Failed to parse gate_state.json: {exc}"], False, False

        if not isinstance(raw_state, dict):
            return "full", {}, ["gate_state.json is not a JSON object."], False, False

        state = {str(k): v for k, v in raw_state.items()}
        mode_val = str(state.get("mode") or "")
        default_ok_mode = "audit" if mode_val == "audit" else "full"

        # 1. Verify HMAC-SHA256 signature
        hmac_ok, hmac_err = verify_state_hmac(session_dir, state)
        if not hmac_ok:
            gate_errors.append(hmac_err)

        workdir = Path(str(state.get("workdir") or "")).resolve()
        repo_dir_str = str(state.get("repo_dir") or "")

        # 2. Verify target git working tree cleanliness (zero pollution in repo_dir)
        init_porcelain = state.get("initial_repo_porcelain")
        if isinstance(init_porcelain, list) and repo_dir_str:
            repo_dir = Path(repo_dir_str).resolve()
            if repo_dir.is_dir():
                ignore_proj = str(state.get("session_scope") or "") == "project"
                cur_porcelain = collect_repo_porcelain(repo_dir, ignore_project_session=ignore_proj)
                if cur_porcelain is not None:
                    initial_set = {str(x) for x in init_porcelain}
                    newly_dirty = sorted(set(cur_porcelain) - initial_set)
                    if newly_dirty:
                        gate_errors.append(
                            f"Git working tree pollution detected in target repo_dir ({len(newly_dirty)} new dirty/untracked file(s)): {newly_dirty[:10]}"
                        )

        # 3. Verify Stage 1 impact_scan.py execution
        s1_scan = state.get("stage1_scan")
        impact_path = session_dir / "impact_scan.json"
        if not isinstance(s1_scan, dict) or s1_scan.get("executed") is not True:
            gate_errors.append("Stage 1 impact scan (impact_scan.py --session) was not executed.")
        elif not impact_path.is_file() or sha256_file(impact_path) != str(s1_scan.get("sha256") or ""):
            gate_errors.append("Stage 1 impact_scan.json is missing or SHA-256 mismatch.")

        # 4. Verify Stage 1 Test Sufficiency Gate
        stage1 = state.get("stage1")
        s1_status = stage1.get("status") if isinstance(stage1, dict) else None
        bypass_status, bypass_reason = evaluate_skip_test_stage2_status(state)
        skip_test_revoked = (
            bool(state.get("skip_test_revoked", False))
            or has_stage2_test_failure(state)
            or bypass_status == "revoked"
        )
        effective_skip_test = bool(state.get("skip_test", False)) and not skip_test_revoked

        if s1_status == "NG":
            gaps = stage1.get("gaps_count", 0) if isinstance(stage1, dict) else 0
            if not isinstance(gaps, int) or gaps < 1:
                gate_errors.append("Stage 1 status is NG, but gaps_count >= 1 was not recorded via gate-stage1.")
            if not effective_skip_test:
                return "stage1_ng", state, gate_errors, False, hmac_ok
        elif s1_status == "SKIPPED_BY_FLAG":
            if not effective_skip_test:
                if bool(state.get("skip_test", False)) and skip_test_revoked:
                    return "stage1_ng", state, gate_errors, False, hmac_ok
                gate_errors.append(
                    "Stage 1 status is SKIPPED_BY_FLAG, but --skip-test is not active on this session."
                )
                return default_ok_mode, state, gate_errors, False, hmac_ok
        elif s1_status not in {"OK", "EXEMPT_NO_TESTABLE_CODE"}:
            gate_errors.append(
                f"Stage 1 Test Sufficiency Gate was not passed (current status: '{s1_status}')."
            )
            return default_ok_mode, state, gate_errors, False, hmac_ok

        if effective_skip_test and s1_status in ALLOWED_STAGE1_SKIP_STATUSES and bypass_status in {"pending", "unverified"}:
            gate_errors.append(bypass_reason)

        if s1_status == "EXEMPT_NO_TESTABLE_CODE" and mode_val in {"pr", "range", "local"}:
            t_raw = state.get("testable_code_files")
            if isinstance(t_raw, list) and len(t_raw) >= 1:
                gate_errors.append(
                    "Stage 1 EXEMPT_NO_TESTABLE_CODE is invalid when testable_code_files is non-empty in pr/range/local mode."
                )

        if s1_status == "OK" and isinstance(stage1, dict):
            vtests = stage1.get("verified_tests")
            if not isinstance(vtests, list) or len(vtests) == 0:
                gate_errors.append("Stage 1 status is OK, but verified_tests list is empty.")
            else:
                for vt in vtests:
                    if isinstance(vt, dict):
                        vt_rel = str(vt.get("path") or "")
                        vpath = workdir / vt_rel
                        vsha = str(vt.get("sha256") or "")
                        if TEST_PATH_RE.search(vt_rel) is None:
                            gate_errors.append(
                                f"Stage 1 verified test '{vt_rel}' does not match TEST_PATH_RE naming conventions."
                            )
                        if not vpath.is_file():
                            gate_errors.append(f"Stage 1 verified test missing in workdir: {vt_rel}")
                        elif sha256_file(vpath) != vsha:
                            gate_errors.append(f"Stage 1 verified test SHA-256 mismatch: {vt_rel}")

        # 5. Verify Stage 2 execution & log SHA-256; has_stage2_failure ONLY on regression 'failed'
        stage2 = state.get("stage2")
        has_stage2_failure = False
        if not isinstance(stage2, dict) or stage2.get("executed") is not True:
            gate_errors.append("Stage 2 (run_checks.py) has not been executed (stage2.executed != true).")
        else:
            log_sha = str(stage2.get("log_sha256") or "")
            ci_log = session_dir / "ci_checks.log"
            if not ci_log.is_file():
                gate_errors.append("Stage 2 log file ci_checks.log is missing.")
            elif not log_sha or sha256_file(ci_log) != log_sha:
                gate_errors.append("Stage 2 log file ci_checks.log SHA-256 does not match gate_state.json.")

            checks_list = stage2.get("checks")
            if isinstance(checks_list, list):
                for chk in checks_list:
                    if isinstance(chk, dict) and chk.get("status") == "failed":
                        has_stage2_failure = True

        # 6. Verify Stage 3 scan_diff_smells.py execution & smell accountability
        stage3 = state.get("stage3")
        smells_path = session_dir / "diff_smells.json"
        if not isinstance(stage3, dict) or stage3.get("smell_scan_executed") is not True:
            gate_errors.append("Stage 3 (scan_diff_smells.py --session) has not been executed.")
        else:
            smell_sha = str(stage3.get("smell_sha256") or "")
            if not smells_path.is_file() or not smell_sha or sha256_file(smells_path) != smell_sha:
                gate_errors.append("Stage 3 diff_smells.json is missing or SHA-256 mismatch.")
            else:
                smell_count = stage3.get("smell_count", 0)
                if isinstance(smell_count, int) and smell_count > 0:
                    _verify_smell_accountability_soft(session_dir, gate_errors)

        # 7. Verify Stage 4 preparation, execution, challenge_nonce, IDs, audit_notes, and checked_files
        stage4 = state.get("stage4")
        if not isinstance(stage4, dict) or stage4.get("executed") is not True:
            gate_errors.append(
                "Stage 4 adversarial verification has not been completed (prepare-stage4 + record-stage4 required)."
            )
        else:
            _verify_stage4_response_soft(session_dir, state, stage4, workdir, gate_errors)

    return default_ok_mode, state, gate_errors, has_stage2_failure, hmac_ok


def validate_overview_section(
    sec: SectionSlice,
    lines: Sequence[str],
    blocks: Sequence[FencedBlock],
    fenced_lines: set[int],
    expected_mode: str,
    structural_errors: list[str],
) -> None:
    is_audit = expected_mode == "audit"
    sec_label = sec.heading_text
    allowed_headers = AUDIT_OVERVIEW_HEADER_TUPLES if is_audit else PR_OVERVIEW_HEADER_TUPLES

    tbl = find_table_in_range(
        lines=lines,
        fenced_lines=fenced_lines,
        start_line=sec.body_start_line,
        end_line=sec.body_end_line,
        allowed_header_tuples=allowed_headers,
    )
    if tbl is None:
        expected_desc = (
            "'| ファイル | 種別 | 役割と主な内容 |' (or '| ファイル | 変更種別 | 役割と主な変更内容 |')"
            if is_audit
            else "'| ファイル | 変更種別 | 役割と主な変更内容 |'"
        )
        structural_errors.append(
            f"Section '{sec_label}' is missing the required 3-column table {expected_desc}."
        )
        tbl_lines: set[int] = set()
    else:
        t_start, t_end, t_rows = tbl
        tbl_lines = set(range(t_start, t_end + 1))
        if len(t_rows) < 1:
            structural_errors.append(f"Section '{sec_label}' 3-column table must contain at least 1 file row.")
        for r_ln, cells in t_rows:
            if len(cells) != 3:
                structural_errors.append(f"L{r_ln}: Overview table row has {len(cells)} columns (expected 3).")
            for cell in cells:
                validate_cell_links(cell, r_ln, structural_errors)

    has_applied_scope = False
    has_unaffected_scope = False
    prose_lines: list[str] = []

    applied_re = (
        re.compile(r"^-\s*(?:\*\*)?(?:監査対象範囲|反映される範囲)(?:\*\*)?\s*[:：]")
        if is_audit
        else re.compile(r"^-\s*(?:\*\*)?反映される範囲(?:\*\*)?\s*[:：]")
    )
    unaffected_re = (
        re.compile(r"^-\s*(?:\*\*)?(?:監査対象外範囲|影響しない範囲)(?:\*\*)?\s*[:：]")
        if is_audit
        else re.compile(r"^-\s*(?:\*\*)?影響しない範囲(?:\*\*)?\s*[:：]")
    )

    for ln in range(sec.body_start_line, sec.body_end_line + 1):
        if ln in fenced_lines or ln in tbl_lines:
            continue
        raw = lines[ln - 1].strip()
        if not raw:
            continue
        if applied_re.match(raw):
            has_applied_scope = True
        elif unaffected_re.match(raw):
            has_unaffected_scope = True
        elif not raw.startswith("-") and not raw.startswith(">") and not raw.startswith("|"):
            prose_lines.append(raw)

        for pat in OVERVIEW_JUDGMENT_PATTERNS:
            if pat.search(raw):
                structural_errors.append(
                    f"L{ln}: '{sec_label}' must be strictly neutral (evaluative/judgment expression detected: '{raw[:60]}')."
                )
                break

    if not prose_lines:
        structural_errors.append(f"Section '{sec_label}' must include neutral introductory prose.")
    if not has_applied_scope:
        req_b1 = "'- 監査対象範囲: ...' (or '- 反映される範囲: ...')" if is_audit else "'- 反映される範囲: ...'"
        structural_errors.append(f"Section '{sec_label}' is missing {req_b1}.")
    if not has_unaffected_scope:
        req_b2 = "'- 監査対象外範囲: ...' (or '- 影響しない範囲: ...')" if is_audit else "'- 影響しない範囲: ...'"
        structural_errors.append(f"Section '{sec_label}' is missing {req_b2}.")

    mermaid_in_overview = [
        b
        for b in blocks
        if b.lang == "mermaid"
        and sec.body_start_line <= b.start_line <= sec.body_end_line
        and any(bl.strip() for bl in b.body_lines)
    ]
    if not mermaid_in_overview:
        structural_errors.append(f"Section '{sec_label}' must include a ```mermaid flow diagram.")


def validate_verdict_section(
    sec: SectionSlice,
    lines: Sequence[str],
    fenced_lines: set[int],
    expected_mode: str,
    structural_errors: list[str],
) -> str | None:
    m = EXEMPT_VERDICT_RE.fullmatch(sec.heading_text)
    if m is None:
        structural_errors.append(
            f"L{sec.heading_line}: Invalid verdict heading '{sec.heading_text}'."
        )
        return None

    verdict = m.group(1)
    if expected_mode == "stage1_ng" and verdict != "🛑 Test Gate NG（テスト修正差し戻し）":
        structural_errors.append(
            f"L{sec.heading_line}: Stage 1 NG session requires verdict '🛑 Test Gate NG（テスト修正差し戻し）', got '{verdict}'."
        )
    elif expected_mode == "full" and verdict not in PR_MODE_VERDICTS:
        structural_errors.append(
            f"L{sec.heading_line}: Full PR review mode requires one of {sorted(PR_MODE_VERDICTS)}, got '{verdict}'."
        )
    elif expected_mode == "audit" and verdict not in AUDIT_MODE_VERDICTS:
        structural_errors.append(
            f"L{sec.heading_line}: Audit mode cannot use '{verdict}'."
        )

    expected_alert = VERDICT_TO_ALERT[verdict]
    alert_header_line: int | None = None
    alert_body_count = 0
    for ln in range(sec.body_start_line, sec.body_end_line + 1):
        if ln in fenced_lines:
            continue
        stripped = lines[ln - 1].strip()
        if not stripped:
            continue
        if alert_header_line is None:
            if stripped == f"> {expected_alert}":
                alert_header_line = ln
            else:
                structural_errors.append(
                    f"L{ln}: Verdict '{verdict}' must be immediately followed by GitHub Alert '> {expected_alert}' (got '{stripped[:50]}')."
                )
                break
        elif stripped.startswith(">"):
            content_after_gt = stripped[1:].strip()
            if content_after_gt:
                alert_body_count += 1
            if EMOJI_RE.search(stripped):
                structural_errors.append(
                    f"L{ln}: Emojis are prohibited inside GitHub Alert body lines (keep emoji only in the heading)."
                )

    if alert_header_line is None:
        structural_errors.append(
            f"Section '{sec.heading_text}' is missing required GitHub Alert '> {expected_alert}'."
        )
    elif alert_body_count < 1:
        structural_errors.append(
            f"L{alert_header_line}: GitHub Alert '> {expected_alert}' must include bold summary and reason lines."
        )

    return verdict


def extract_repo_rel_path_from_target(raw_target: str) -> str | None:
    target = raw_target.strip().strip("`").strip()
    if not target:
        return None
    if target.startswith(("http://", "https://", "mailto:", "file:", "#")):
        return target
    if "#" in target:
        target = target.split("#", 1)[0]
    else:
        target = re.sub(r":L?\d+(?:-L?\d+)?$", "", target)
    norm = target.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm or None


def extract_repo_rel_path_from_location_cell(cell: str) -> str | None:
    m = MARKDOWN_LINK_RE.search(cell)
    target = m.group(2).strip() if m is not None else cell
    return extract_repo_rel_path_from_target(target)


def validate_nit_question_non_code_meta(
    sub: SectionSlice,
    lines: Sequence[str],
    fenced_lines: set[int],
    structural_errors: list[str],
) -> None:
    flagged_subsection = False
    if PR_META_SUBJECT_RE.search(sub.heading_text):
        structural_errors.append(
            f"L{sub.heading_line}: Non-code PR metadata remark (pr_metadata_heading) is prohibited in "
            f"'{sub.heading_text}' (🔵 Nit / ❓ Question must target changed code only)."
        )
        flagged_subsection = True

    check_lines: list[tuple[int, str]] = [(sub.heading_line, sub.heading_text)]
    for ln in range(sub.body_start_line, sub.body_end_line + 1):
        if ln in fenced_lines:
            continue
        raw = lines[ln - 1].strip()
        if raw:
            check_lines.append((ln, raw))

    for ln, text_line in check_lines:
        for rule_name, rule_re in NIT_QUESTION_META_LINE_PATTERNS:
            if rule_re.search(text_line):
                if ln == sub.heading_line and flagged_subsection:
                    break
                structural_errors.append(
                    f"L{ln}: Non-code metadata remark ({rule_name}) is prohibited under "
                    f"'{sub.heading_text}': '{text_line[:80]}'"
                )
                flagged_subsection = True
                break

    if not flagged_subsection:
        combined_non_fenced = "\n".join(txt for _, txt in check_lines)
        if PR_META_SUBJECT_RE.search(combined_non_fenced) and PR_META_ACTION_RE.search(combined_non_fenced):
            structural_errors.append(
                f"L{sub.heading_line}: Non-code PR metadata remark is prohibited under '{sub.heading_text}' "
                "(🔵 Nit / ❓ Question must target changed code only, never PR description/title/template)."
            )


def _validate_concise_nit_question_bullets(
    sub: SectionSlice,
    first_non_blank: int,
    lines: Sequence[str],
    fenced_lines: set[int],
    structural_errors: list[str],
    enforce_changed_files: set[str] | None,
) -> None:
    for ln in range(first_non_blank, sub.body_end_line + 1):
        if ln in fenced_lines:
            continue
        if lines[ln - 1].strip().startswith("|"):
            structural_errors.append(
                f"L{ln}: '{sub.heading_text}' mixes non-table lines before a Markdown table; "
                "use either an immediate 4-column table or table-free 1-3 bullets."
            )
            return

    bullets: list[tuple[int, str]] = []
    for ln in range(first_non_blank, sub.body_end_line + 1):
        if ln in fenced_lines:
            continue
        raw = lines[ln - 1]
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullets.append((ln, stripped))
        elif bullets and (raw.startswith("  ") or raw.startswith("\t")):
            prev_ln, prev_txt = bullets[-1]
            bullets[-1] = (prev_ln, prev_txt + " " + stripped)
        else:
            structural_errors.append(
                f"L{ln}: Concise table-free '{sub.heading_text}' must consist solely of 1 to 3 bullet items ('- ...')."
            )
            return

    if len(bullets) < 1 or len(bullets) > 3:
        structural_errors.append(
            f"L{sub.heading_line}: Concise table-free '{sub.heading_text}' must contain 1 to 3 bullet items (got {len(bullets)})."
        )
        if not bullets:
            return

    for b_ln, b_txt in bullets:
        for _, link_target in MARKDOWN_LINK_RE.findall(b_txt):
            t_clean = link_target.strip()
            if t_clean.startswith("/") or t_clean.startswith("file:"):
                structural_errors.append(
                    f"L{b_ln}: Link target '{t_clean}' must be repository-relative (absolute/file:// paths are prohibited)."
                )

    first_b_ln, first_b_txt = bullets[0]
    first_links = MARKDOWN_LINK_RE.findall(first_b_txt)
    repo_rel_targets: list[str] = []
    for _, raw_target in first_links:
        t = raw_target.strip()
        if not t or t.startswith(("http://", "https://", "mailto:", "#", "file:", "/")):
            continue
        repo_rel_targets.append(t)

    if not repo_rel_targets:
        structural_errors.append(
            f"L{first_b_ln}: First bullet of concise '{sub.heading_text}' must include at least 1 repository-relative Markdown link (e.g. '[file.ts:L10-L12](src/file.ts#L10-L12)')."
        )
        return

    primary_rel_path = extract_repo_rel_path_from_target(repo_rel_targets[0])
    if enforce_changed_files is not None:
        if primary_rel_path is None or primary_rel_path not in enforce_changed_files:
            structural_errors.append(
                f"L{first_b_ln}: Primary target '{primary_rel_path or '(missing)'}' in first bullet under "
                f"'{sub.heading_text}' is not in session changed_files "
                "(🔵 Nit / ❓ Question must target files modified in the diff)."
            )


def validate_finding_subsection(
    sub: SectionSlice,
    lines: Sequence[str],
    fenced_lines: set[int],
    structural_errors: list[str],
    *,
    allow_concise_bullets: bool = False,
    check_non_code_meta: bool = False,
    enforce_changed_files: set[str] | None = None,
) -> None:
    if check_non_code_meta:
        validate_nit_question_non_code_meta(sub, lines, fenced_lines, structural_errors)

    first_non_blank: int | None = None
    for ln in range(sub.body_start_line, sub.body_end_line + 1):
        if lines[ln - 1].strip():
            first_non_blank = ln
            break

    if first_non_blank is None:
        structural_errors.append(f"L{sub.heading_line}: Subsection '{sub.heading_text}' is empty.")
        return

    first_stripped = lines[first_non_blank - 1].strip()
    if allow_concise_bullets and not first_stripped.startswith("|"):
        _validate_concise_nit_question_bullets(
            sub=sub,
            first_non_blank=first_non_blank,
            lines=lines,
            fenced_lines=fenced_lines,
            structural_errors=structural_errors,
            enforce_changed_files=enforce_changed_files,
        )
        return

    tbl = find_table_in_range(
        lines=lines,
        fenced_lines=fenced_lines,
        start_line=first_non_blank,
        end_line=sub.body_end_line,
        allowed_header_tuples=ALLOWED_FINDING_HEADER_TUPLES,
    )
    if tbl is None or tbl[0] != first_non_blank:
        structural_errors.append(
            f"L{sub.heading_line}: '{sub.heading_text}' must be immediately followed by the 4-column table "
            "'| 箇所 | 対象 | 型・契約 | 処理内容と目的 |' (or '| 箇所 | 対象 | 引数と返り値 | 処理内容と目的 |')."
        )
        return

    t_start, t_end, t_rows = tbl
    if len(t_rows) < 1:
        structural_errors.append(
            f"L{t_start}: 4-column table under '{sub.heading_text}' must have at least 1 data row."
        )

    for r_ln, cells in t_rows:
        if len(cells) != 4:
            structural_errors.append(
                f"L{r_ln}: Table row under '{sub.heading_text}' has {len(cells)} columns (expected 4)."
            )
        for cell in cells:
            validate_cell_links(cell, r_ln, structural_errors)

    if enforce_changed_files is not None and len(t_rows) >= 1:
        first_row_ln, first_row_cells = t_rows[0]
        first_loc_cell = first_row_cells[0] if first_row_cells else ""
        primary_rel_path = extract_repo_rel_path_from_location_cell(first_loc_cell)
        if primary_rel_path is None or primary_rel_path not in enforce_changed_files:
            structural_errors.append(
                f"L{first_row_ln}: Primary target '{primary_rel_path or '(missing)'}' in row 1 under "
                f"'{sub.heading_text}' is not in session changed_files "
                "(🔵 Nit / ❓ Question must target files modified in the diff)."
            )

    bullet_count = 0
    for ln in range(t_end + 1, sub.body_end_line + 1):
        if ln in fenced_lines:
            continue
        stripped = lines[ln - 1].strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet_count += 1

    min_bullets = 1 if allow_concise_bullets else 2
    if bullet_count < min_bullets:
        structural_errors.append(
            f"L{sub.heading_line}: '{sub.heading_text}' must include short bullet points explaining impact and remediation (>= {min_bullets} bullet(s))."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_review_output.py",
        description="Validate HMAC-sealed gate_state.json evidence, review Markdown structure, and Japanese prose quality.",
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--session", default=None, help="Session directory path (enforces gate_state.json locks).")
    mode_group.add_argument(
        "--standalone-mode",
        choices=["stage1_ng", "full", "audit"],
        default=None,
        help="Validate structure and prose without a session directory.",
    )
    parser.add_argument("--file", "--review-md", dest="file", required=True, help="Path to generated review Markdown file.")
    parser.add_argument("--yomiyasu-lint", default=None, help="Optional explicit path to yomiyasu_lint.py.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    review_path = Path(str(args.file)).expanduser().resolve()
    if not review_path.is_file():
        fail_cli_usage(f"Review file does not exist: {review_path}", 2)
    try:
        review_bytes = review_path.read_bytes()
        review_text = review_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        fail_cli_usage(f"Cannot read review file as UTF-8 ({review_path}): {exc}", 2)
    initial_review_sha256 = sha256_bytes(review_bytes)

    session_dir: Path | None = None
    state: dict[str, object] = {}
    gate_errors: list[str] = []
    has_stage2_failure = False
    hmac_ok = False
    initial_state_version = 0

    if args.session is not None:
        session_dir = Path(str(args.session)).expanduser().resolve()
        expected_mode, state, gate_errors, has_stage2_failure, hmac_ok = validate_gate_state(session_dir)
        initial_state_version = get_state_version(state)
    else:
        expected_mode = str(args.standalone_mode)

    lines = review_text.splitlines()
    blocks, fenced_lines = parse_fenced_blocks(lines)
    structural_errors: list[str] = []

    # 1. Check non-mermaid fenced code blocks >= 4 lines (mermaid is explicitly allowed)
    for blk in blocks:
        if blk.lang != "mermaid" and len(blk.body_lines) >= 4:
            structural_errors.append(
                f"L{blk.start_line}-L{blk.end_line}: Non-mermaid code block ({blk.lang or 'plain'}) has "
                f"{len(blk.body_lines)} lines (>= 4 lines prohibited; describe remediation in 2-3 bullet points)."
            )

    # 2. Check temp path / file:// / .gemini/tmp leaks anywhere in document
    for idx_zero, raw_line in enumerate(lines):
        ln = idx_zero + 1
        for leak_name, leak_re in TEMP_LEAK_PATTERNS:
            if leak_re.search(raw_line):
                structural_errors.append(
                    f"L{ln}: Prohibited temporary/local path leak ({leak_name}) in review output: '{raw_line.strip()[:80]}'"
                )

    # 3. Validate top-level ## sections
    h2_sections = slice_sections_by_prefix(lines, fenced_lines, 1, len(lines), "## ")
    parsed_verdict: str | None = None
    bug_count = 0
    risk_count = 0
    nit_count = 0
    question_count = 0
    test_issue_count = 0

    enforced_changed_files: set[str] | None = None
    if session_dir is not None and state.get("mode") in {"pr", "range", "local"}:
        raw_changed = state.get("changed_files")
        if isinstance(raw_changed, list) and len(raw_changed) > 0:
            normalized_set: set[str] = set()
            for item in raw_changed:
                if isinstance(item, str) and item.strip():
                    norm_item = _normalize_rel_path(item)
                    if norm_item:
                        normalized_set.add(norm_item)
            if normalized_set:
                enforced_changed_files = normalized_set

    allowed_first_h2 = {"## 監査概要", "## 変更概要"} if expected_mode == "audit" else {"## 変更概要"}

    if len(h2_sections) < 2:
        structural_errors.append(
            "Review output must start with '## 変更概要' (or '## 監査概要' in audit mode) followed immediately by '## レビュー判定: ...'."
        )
    else:
        if h2_sections[0].heading_text not in allowed_first_h2:
            expected_h2_desc = "'## 監査概要' or '## 変更概要'" if expected_mode == "audit" else "'## 変更概要'"
            structural_errors.append(
                f"L{h2_sections[0].heading_line}: First H2 section must be {expected_h2_desc} (got '{h2_sections[0].heading_text}')."
            )
        else:
            validate_overview_section(
                h2_sections[0], lines, blocks, fenced_lines, expected_mode, structural_errors
            )

        parsed_verdict = validate_verdict_section(
            h2_sections[1], lines, fenced_lines, expected_mode, structural_errors
        )

    if expected_mode == "stage1_ng":
        if len(h2_sections) != 3 or h2_sections[2].heading_text != "## テスト不備事項":
            got_names = [s.heading_text for s in h2_sections]
            structural_errors.append(
                f"stage1_ng mode requires exact H2 sequence ['## 変更概要', '## レビュー判定: 🛑 Test Gate NG（テスト修正差し戻し）', '## テスト不備事項'] (got {got_names})."
            )
        if len(h2_sections) >= 3 and h2_sections[2].heading_text == "## テスト不備事項":
            test_sec = h2_sections[2]
            h3_list = slice_sections_by_prefix(
                lines, fenced_lines, test_sec.body_start_line, test_sec.body_end_line, "### "
            )
            if len(h3_list) < 1:
                structural_errors.append(
                    "Section '## テスト不備事項' must contain at least 1 '### 🛑 Test Issue: <summary>' subsection."
                )
            for sub in h3_list:
                if not sub.heading_text.startswith("### 🛑 Test Issue: ") or not EXEMPT_FINDING_RE.fullmatch(sub.heading_text):
                    structural_errors.append(
                        f"L{sub.heading_line}: Invalid Test Issue heading '{sub.heading_text}' (expected '### 🛑 Test Issue: <summary without extra emoji>')."
                    )
                else:
                    test_issue_count += 1
                validate_finding_subsection(sub, lines, fenced_lines, structural_errors)
    else:
        expected_tail = ["## 指摘事項", "## CI・敵対的検証サマリー"]
        actual_tail = [s.heading_text for s in h2_sections[2:]]
        if actual_tail != expected_tail:
            first_label = "## 監査概要" if expected_mode == "audit" else "## 変更概要"
            structural_errors.append(
                f"{expected_mode} mode requires H2 sections {[first_label, '## レビュー判定: ...'] + expected_tail} (got {[s.heading_text for s in h2_sections]})."
            )

        if len(h2_sections) >= 3 and h2_sections[2].heading_text == "## 指摘事項":
            findings_sec = h2_sections[2]
            h3_list = slice_sections_by_prefix(
                lines, fenced_lines, findings_sec.body_start_line, findings_sec.body_end_line, "### "
            )
            if len(h3_list) == 0:
                sec_text = "\n".join(
                    lines[ln - 1]
                    for ln in range(findings_sec.body_start_line, findings_sec.body_end_line + 1)
                    if ln not in fenced_lines
                )
                if ZERO_FINDINGS_REQUIRED_SUBSTR not in sec_text:
                    structural_errors.append(
                        f"When there are 0 findings across Bug/Risk/Nit/Question, '## 指摘事項' must include a "
                        f"zero-findings statement containing '{ZERO_FINDINGS_REQUIRED_SUBSTR}' "
                        f"(e.g., '{ZERO_FINDINGS_SENTENCE}')."
                    )
            else:
                max_rank_seen = -1
                max_label_seen = ""
                for sub in h3_list:
                    matched_spec: tuple[str, int, str] | None = None
                    if EXEMPT_FINDING_RE.fullmatch(sub.heading_text):
                        for prefix, rank, label in FINDING_CATEGORY_SPECS:
                            if sub.heading_text.startswith(prefix):
                                matched_spec = (prefix, rank, label)
                                break

                    if matched_spec is None:
                        structural_errors.append(
                            f"L{sub.heading_line}: Invalid finding heading '{sub.heading_text}' "
                            "(expected '### <🔴 Bug|🟡 Risk|🔵 Nit|❓ Question>: <summary without extra emoji>')."
                        )
                        validate_finding_subsection(sub, lines, fenced_lines, structural_errors)
                        continue

                    _, rank, label = matched_spec
                    if rank == 0:
                        bug_count += 1
                    elif rank == 1:
                        risk_count += 1
                    elif rank == 2:
                        nit_count += 1
                    elif rank == 3:
                        question_count += 1

                    if rank < max_rank_seen:
                        structural_errors.append(
                            f"L{sub.heading_line}: Finding category order violation: '### {label}:' cannot appear "
                            f"after '### {max_label_seen}:' (required priority order: 🔴 Bug -> 🟡 Risk -> 🔵 Nit -> ❓ Question)."
                        )
                    else:
                        max_rank_seen = rank
                        max_label_seen = label

                    is_nit_or_question = rank in (2, 3)
                    validate_finding_subsection(
                        sub,
                        lines,
                        fenced_lines,
                        structural_errors,
                        allow_concise_bullets=is_nit_or_question,
                        check_non_code_meta=is_nit_or_question,
                        enforce_changed_files=enforced_changed_files if is_nit_or_question else None,
                    )

        if len(h2_sections) >= 4 and h2_sections[3].heading_text == "## CI・敵対的検証サマリー":
            sum_sec = h2_sections[3]
            sum_text = "\n".join(
                lines[ln - 1]
                for ln in range(sum_sec.body_start_line, sum_sec.body_end_line + 1)
                if ln not in fenced_lines
            )
            for required_token in ("Stage 1", "Stage 2", "Stage 4", "検証範囲"):
                if required_token not in sum_text:
                    structural_errors.append(
                        f"Section '## CI・敵対的検証サマリー' is missing required summary item '{required_token}'."
                    )

        # Cross-check verdict consistency with Code Health & regression-only Stage 2 failure
        if parsed_verdict is not None:
            if bug_count >= 1 and parsed_verdict not in RED_VERDICTS:
                expected_red = (
                    "'🔴 要修正（重大な懸念あり）' or '🔴 Request changes'"
                    if expected_mode == "audit"
                    else "'🔴 Request changes'"
                )
                structural_errors.append(
                    f"Verdict cannot be '{parsed_verdict}' when {bug_count} '🔴 Bug' finding(s) remain "
                    f"(must be {expected_red})."
                )
            if has_stage2_failure and parsed_verdict in GREEN_VERDICTS:
                gate_errors.append(
                    f"Verdict cannot be '{parsed_verdict}' when Stage 2 CI has regression checks with status == 'failed'."
                )
            bypass_status_final, _ = evaluate_skip_test_stage2_status(state)
            skip_test_bypassed = bypass_status_final == "confirmed"
            if bug_count == 0 and risk_count == 0 and not has_stage2_failure:
                if skip_test_bypassed:
                    if parsed_verdict not in (GREEN_VERDICTS | YELLOW_VERDICTS):
                        expected_allowed = (
                            "'🟢 健全（重大な懸念なし）', '🟢 Approve', '🟡 要確認（構造・保守上の懸念あり）', or '🟡 Comment'"
                            if expected_mode == "audit"
                            else "'🟢 Approve' or '🟡 Comment'"
                        )
                        structural_errors.append(
                            f"Verdict must be {expected_allowed} when Bug=0, Risk=0, and Stage 2 has no failed checks "
                            f"with --skip-test (got '{parsed_verdict}')."
                        )
                elif parsed_verdict not in GREEN_VERDICTS:
                    expected_green = (
                        "'🟢 健全（重大な懸念なし）' or '🟢 Approve'"
                        if expected_mode == "audit"
                        else "'🟢 Approve'"
                    )
                    structural_errors.append(
                        f"Verdict must be {expected_green} when Bug=0, Risk=0, and Stage 2 has no failed checks "
                        f"(got '{parsed_verdict}'; 🔵 Nit={nit_count} and ❓ Question={question_count} do not affect verdict)."
                    )

    # 4. Run portable built-in + optional yomiyasu Japanese prose & emoji lint
    try:
        prose_warnings, lint_backend = run_prose_lint(
            review_text,
            explicit_yomiyasu_path=args.yomiyasu_lint,
            lines=lines,
            fenced_lines=fenced_lines,
            doc_type="pr",
            reader="expert",
            exempt_review_headings=True,
            strict_explicit=True,
        )
    except FileNotFoundError as exc:
        fail_cli_usage(str(exc), 2)

    passed = len(gate_errors) == 0 and len(structural_errors) == 0 and len(prose_warnings) == 0

    if session_dir is not None and state and hmac_ok:
        state_path = session_dir / "gate_state.json"
        if not session_dir.is_dir() or not state_path.is_file() or load_session_hmac_key(session_dir) is None:
            gate_errors.append(
                "Stage 5 validation aborted: session gate_state.json or HMAC key is missing before commit "
                "(session may have been cleaned up concurrently); cannot record Stage 5 validation."
            )
            passed = False
        else:
            with session_state_lock(session_dir, create_dir=False):
                state_changed_concurrently = False
                concurrent_err = ""
                if not state_path.is_file() or load_session_hmac_key(session_dir) is None:
                    state_changed_concurrently = True
                    concurrent_err = (
                        "Stage 5 validation aborted: session gate_state.json or HMAC key disappeared before commit."
                    )
                    latest_raw: object = None
                else:
                    try:
                        latest_raw = json.loads(state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        state_changed_concurrently = True
                        concurrent_err = f"Failed to re-read gate_state.json before Stage 5 commit: {exc}"
                        latest_raw = None

                if not state_changed_concurrently:
                    if not isinstance(latest_raw, dict):
                        state_changed_concurrently = True
                        concurrent_err = "gate_state.json is no longer a valid JSON object before Stage 5 commit."
                    else:
                        latest_state = {str(k): v for k, v in latest_raw.items()}
                        latest_hmac_ok, latest_hmac_err = verify_state_hmac(session_dir, latest_state)
                        latest_ver = get_state_version(latest_state)
                        if not latest_hmac_ok:
                            state_changed_concurrently = True
                            concurrent_err = f"gate_state.json HMAC invalid before Stage 5 commit: {latest_hmac_err}"
                        elif latest_ver != initial_state_version:
                            state_changed_concurrently = True
                            concurrent_err = (
                                f"Stage 5 validation aborted: gate_state.json changed concurrently "
                                f"(expected state_version={initial_state_version}, current={latest_ver}); "
                                "re-run validate_review_output.py against the latest session state."
                            )
                        else:
                            for guarded_key in ("stage1", "stage2", "skip_test_revoked", "skip_test_revoked_reason"):
                                if latest_state.get(guarded_key) != state.get(guarded_key):
                                    state_changed_concurrently = True
                                    concurrent_err = (
                                        f"Stage 5 validation aborted: '{guarded_key}' changed while validation was running; "
                                        "re-run validate_review_output.py against the latest session state."
                                    )
                                    break
                            if not state_changed_concurrently:
                                current_review_sha = sha256_file(review_path) if review_path.is_file() else ""
                                if current_review_sha != initial_review_sha256:
                                    state_changed_concurrently = True
                                    concurrent_err = (
                                        "Stage 5 validation aborted: review file was modified while validation was running; "
                                        "re-run validate_review_output.py against the latest review file."
                                    )

                if state_changed_concurrently:
                    gate_errors.append(concurrent_err)
                    passed = False
                else:
                    state["stage5"] = {
                        "validated": passed,
                        "review_file": str(review_path),
                        "review_sha256": initial_review_sha256 if passed else None,
                        "mode": expected_mode,
                        "verdict": parsed_verdict,
                        "validated_at": utc_now_iso(),
                    }
                    write_sealed_gate_state(session_dir, state, expected_version=initial_state_version)
                    if passed and expected_mode == "full" and should_advance_review_history(session_dir, state):
                        save_review_history(
                            session_dir=session_dir,
                            state=state,
                            expected_mode=expected_mode,
                            verdict=parsed_verdict,
                        )

    result: dict[str, object] = {
        "passed": passed,
        "mode": expected_mode,
        "verdict": parsed_verdict,
        "gate_errors": gate_errors,
        "structural_errors": structural_errors,
        "prose_warnings": prose_warnings,
        "stats": {
            "line_count": len(lines),
            "bug_count": bug_count,
            "risk_count": risk_count,
            "nit_count": nit_count,
            "question_count": question_count,
            "test_issue_count": test_issue_count,
            "mermaid_blocks": sum(1 for b in blocks if b.lang == "mermaid"),
            "code_blocks": sum(1 for b in blocks if b.lang != "mermaid"),
            "prose_linter": lint_backend,
        },
    }
    emit_json(result)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
