#!/usr/bin/env python3
"""Shared test helpers for the yharuto-code-review v3 unit test suite."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence

TESTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TESTS_DIR.parent
REPO_ROOT = SKILL_ROOT.parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
EVALS_DIR = SKILL_ROOT / "evals"
FIXTURES_DIR = EVALS_DIR / "fixtures"

CHALLENGE_NONCE_RE = re.compile(r"Challenge Nonce:\s*`([0-9a-f]+)`")


def run_script(
    script_name: str,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, object], str, str]:
    script_path = SCRIPTS_DIR / script_name
    merged_env = dict(os.environ)
    merged_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env is not None:
        merged_env.update(env)

    proc = subprocess.run(
        [sys.executable, "-B", str(script_path), *args],
        cwd=str(cwd or SKILL_ROOT),
        env=merged_env,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    parsed: dict[str, object] = {}
    if proc.stdout.strip():
        try:
            raw: object = json.loads(proc.stdout)
            if isinstance(raw, dict):
                parsed = {str(k): v for k, v in raw.items()}
        except json.JSONDecodeError:
            parsed = {}
    return proc.returncode, parsed, proc.stdout, proc.stderr


def run_git(repo_dir: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        env=env,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_git(repo_dir, ["init", "-b", "main"])
    run_git(repo_dir, ["config", "user.name", "Test Runner"])
    run_git(repo_dir, ["config", "user.email", "test@example.com"])
    run_git(repo_dir, ["config", "commit.gpgsign", "false"])


def git_commit_all(repo_dir: Path, message: str) -> str:
    run_git(repo_dir, ["add", "-A"])
    run_git(repo_dir, ["commit", "-m", message])
    return run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()


def extract_challenge_nonce(adversarial_input_path: Path) -> str:
    text = adversarial_input_path.read_text(encoding="utf-8")
    m = CHALLENGE_NONCE_RE.search(text)
    if m is None:
        raise AssertionError(f"Challenge Nonce not found in {adversarial_input_path}")
    return m.group(1)


def build_clean_full_review(
    *,
    verdict: str = "🟢 Approve",
    alert_tag: str = "[!TIP]",
    alert_headline: str = "**不具合および変更起因の回帰失敗は確認されませんでした（Bug 0件 / Risk 0件）**",
    alert_reason: str = "注文金額の割引上限計算と境界値テストが追加されており、既存の丸め規則とも一貫しています。",
    file_rel: str = "src/discountCalc.py",
    file_label: str = "discountCalc.py",
    findings_body: str | None = None,
    overview_heading: str = "## 変更概要",
    overview_header_row: str = "| ファイル | 変更種別 | 役割と主な変更内容 |",
    scope_applied_prefix: str = "- 反映される範囲:",
    scope_unaffected_prefix: str = "- 影響しない範囲:",
) -> str:
    effective_findings = (
        findings_body
        if findings_body is not None
        else "対応が必要な不具合（Bug）・懸念（Risk）および変更コードへの軽微な提案（Nit）・確認質問（Question）は検出されませんでした。"
    )
    return "\n".join(
        [
            overview_heading,
            "",
            "注文金額に対する割引額の上限判定を追加し、対応する単体テストを更新する変更です。",
            "",
            overview_header_row,
            "|---|---|---|",
            f"| [{file_label}]({file_rel}) | 変更 | 注文金額に対する割引上限の計算処理を追加 |",
            "",
            f"{scope_applied_prefix} 注文確定時の割引金額算出処理",
            f"{scope_unaffected_prefix} 配送手数料の計算処理、請求書PDF出力処理",
            "",
            "```mermaid",
            "flowchart LR",
            '  A["注文金額を受領"] --> B["割引上限と照合"] --> C["確定割引額を返却"]',
            "```",
            "",
            f"## レビュー判定: {verdict}",
            "",
            f"> {alert_tag}",
            f"> {alert_headline}",
            f"> - 理由: {alert_reason}",
            "> - CI・検証結果: lint pass / typecheck pass / test pass / build pass（敵対的検証: subagent完了）",
            "",
            "## 指摘事項",
            "",
            effective_findings,
            "",
            "## CI・敵対的検証サマリー",
            "",
            "- Stage 1（テスト網羅性確認）: OK（対象テストで正常系・異常系・境界値を確認）",
            "- Stage 2（CI自動実行）: test pass / build not_configured",
            "- Stage 4（敵対的検証）: subagent完了（見落とし0件）",
            "- 検証範囲: 変更コードと対応単体テストを確認（未検証範囲なし）",
            "",
        ]
    )


def build_clean_stage1_ng_review(
    *,
    code_rel: str = "src/discountCalc.py",
    code_label: str = "discountCalc.py",
    test_rel: str = "tests/test_discount_calc.py",
    test_label: str = "test_discount_calc.py",
    third_col_header: str = "型・契約",
) -> str:
    return "\n".join(
        [
            "## 変更概要",
            "",
            "注文金額に対する割引額の上限判定を追加し、単体テスト1件を追加する変更です。",
            "",
            "| ファイル | 変更種別 | 役割と主な変更内容 |",
            "|---|---|---|",
            f"| [{code_label}]({code_rel}) | 変更 | 割引上限を超える要求を制限する分岐を追加 |",
            f"| [{test_label}]({test_rel}) | 変更 | 正常系の単体テスト1件を追加 |",
            "",
            "- 反映される範囲: 注文確定時の割引金額算出処理",
            "- 影響しない範囲: 配送手数料の計算処理",
            "",
            "```mermaid",
            "flowchart LR",
            '  A["注文金額を受領"] --> B["割引上限を判定"] --> C["割引額を返却"]',
            "```",
            "",
            "## レビュー判定: 🛑 Test Gate NG（テスト修正差し戻し）",
            "",
            "> [!CAUTION]",
            "> **テストの網羅性が不足しているため本レビュー前に差し戻します（不足 1件）**",
            "> - 理由: 今回追加された上限超過および負数の境界ケースを検証するテストが存在しません。",
            "> - 後続ステージ: Stage 2（CI自動実行）以降は未実施（テスト修正後に再実行してください）",
            "",
            "## テスト不備事項",
            "",
            "### 🛑 Test Issue: 追加された上限超過・負数入力の境界ケースが検証されていない",
            "",
            f"| 箇所 | 対象 | {third_col_header} | 処理内容と目的 |",
            "|---|---|---|---|",
            f"| [{code_label}:L1-L8]({code_rel}#L1-L8) | `compute_discount` | `(amount: int) -> int` | 割引上限と負数入力を制限する変更対象処理 |",
            f"| [{test_label}:L1-L8]({test_rel}#L1-L8) | `{test_label}` | `() -> None` | 正常系1件のみを確認する単体テスト |",
            "",
            "- 現在の単体テストは上限未満の正常系1件のみを確認しており、上限超過や負数入力の分岐が誤っていても通過します。",
            f"- [{test_label}:L1-L8]({test_rel}#L1-L8) に上限と同額・上限超過・負数入力の境界ケースを追加してください。",
            "",
        ]
    )
