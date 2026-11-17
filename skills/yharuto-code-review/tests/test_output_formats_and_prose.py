#!/usr/bin/env python3
"""Unit tests for v3 §6 & §7.2: audit mode, 4-col headers, concise Nit/Question, Pending Review placement, and built-in prose linter."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
from pathlib import Path
import tempfile
import unittest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_TESTS_DIR.parent / "scripts") not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR.parent / "scripts"))

from _helpers import (  # noqa: E402
    build_clean_full_review,
    build_clean_stage1_ng_review,
    extract_challenge_nonce,
    git_commit_all,
    init_git_repo,
    run_git,
    run_script,
)
from _prose_lint import lint_prose_builtin, run_prose_lint  # noqa: E402


class TestOutputFormatsAndProse(unittest.TestCase):
    def test_audit_mode_headings_tables_and_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"YOMIYASU_LINT_DISABLE": "1"}

            # 1. Audit mode with '🟡 要確認（構造・保守上の懸念あり）' + Risk finding
            risk_findings = "\n".join(
                [
                    "### 🟡 Risk: 復号失敗時に空の例外捕捉で未認証状態と同じ返り値を返している",
                    "",
                    "| 箇所 | 対象 | 型・契約 | 処理内容と目的 |",
                    "|---|---|---|---|",
                    "| [sessionVault.ts:L18-L29](src/vault/sessionVault.ts#L18-L29) | `readSessionKey` | `(raw: string) => SessionKey \\| null` | 暗号化済みトークンを復号して有効期限を判定する処理 |",
                    "",
                    "- 復号例外を空の例外ブロックで無視しているため、鍵破損と未ログイン状態が呼び出し側で区別できません。",
                    "- [sessionVault.ts:L18-L29](src/vault/sessionVault.ts#L18-L29) で復号失敗時は構造化ログを記録したうえで専用エラー型を返してください。",
                ]
            )
            audit_md = build_clean_full_review(
                verdict="🟡 要確認（構造・保守上の懸念あり）",
                alert_tag="[!WARNING]",
                alert_headline="**直ちに悪用可能な欠陥はありませんが例外処理境界に整理すべき懸念があります（Bug 0件 / Risk 1件）**",
                alert_reason="トークン復号失敗と未ログイン状態が同一の返り値に統合されている箇所があります。",
                file_rel="src/vault/sessionVault.ts",
                file_label="sessionVault.ts",
                findings_body=risk_findings,
                overview_heading="## 監査概要",
                overview_header_row="| ファイル | 種別 | 役割と主な内容 |",
                scope_applied_prefix="- 監査対象範囲:",
                scope_unaffected_prefix="- 監査対象外範囲:",
            )
            audit_file = root / "audit_ok.md"
            audit_file.write_text(audit_md, encoding="utf-8")
            rc1, out1, _, _ = run_script(
                "validate_review_output.py",
                ["--standalone-mode", "audit", "--file", str(audit_file)],
                env=env,
            )
            self.assertEqual(rc1, 0, json.dumps(out1, ensure_ascii=False))
            self.assertTrue(out1["passed"])

            # 2. Audit mode with 0 findings + '🟢 健全（重大な懸念なし）'
            audit_green = build_clean_full_review(
                verdict="🟢 健全（重大な懸念なし）",
                alert_tag="[!TIP]",
                file_rel="src/vault/sessionVault.ts",
                file_label="sessionVault.ts",
                overview_heading="## 監査概要",
                overview_header_row="| ファイル | 種別 | 役割と主な内容 |",
                scope_applied_prefix="- 監査対象範囲:",
                scope_unaffected_prefix="- 監査対象外範囲:",
            )
            green_file = root / "audit_green.md"
            green_file.write_text(audit_green, encoding="utf-8")
            rc2, out2, _, _ = run_script(
                "validate_review_output.py",
                ["--standalone-mode", "audit", "--file", str(green_file)],
                env=env,
            )
            self.assertEqual(rc2, 0, json.dumps(out2, ensure_ascii=False))

            # 3. Audit verdict used in PR 'full' mode must be rejected
            rc3, out3, _, _ = run_script(
                "validate_review_output.py",
                ["--standalone-mode", "full", "--file", str(green_file)],
                env=env,
            )
            self.assertEqual(rc3, 1)
            self.assertFalse(out3["passed"])

    def test_both_4col_headers_and_concise_nit_question_and_pending_review_inline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # Verify both '型・契約' and legacy '引数と返り値' pass in stage1_ng
            for col3 in ("型・契約", "引数と返り値"):
                f_ng = root / f"ng_{col3}.md"
                f_ng.write_text(build_clean_stage1_ng_review(third_col_header=col3), encoding="utf-8")
                rc_ng, out_ng, _, _ = run_script(
                    "validate_review_output.py",
                    ["--standalone-mode", "stage1_ng", "--file", str(f_ng)],
                    env=env,
                )
                self.assertEqual(rc_ng, 0, json.dumps(out_ng, ensure_ascii=False))

            # Full session with concise table-free Nit & Question -> validate + post_pending_review --dry-run
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return amount // 10\n",
                encoding="utf-8",
            )
            (repo / "src" / "unchangedHelper.py").write_text(
                "def noop() -> int:\n    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_discount_calc.py").write_text(
                "import unittest\nfrom src.discountCalc import compute_discount\n"
                "class T(unittest.TestCase):\n"
                "    def test_calc(self) -> None:\n"
                "        self.assertEqual(compute_discount(200), 20)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "base")

            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n"
                "    cap = 500\n"
                "    return max(0, min(amount // 10, cap))\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "add discount cap")

            _, prep, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            sdir = Path(str(prep["session_dir"]))
            head_sha = str(prep["head_sha"])

            rc_imp, imp_out, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp, 0)
            self.assertEqual(
                imp_out.get("related_tests_and_mocks"),
                [{"file": "tests/test_discount_calc.py", "kind": "test", "reason": "matches_module_stem:discountcalc"}],
            )
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/test_discount_calc.py"],
                env=env,
            )
            run_script("run_checks.py", ["--session", str(sdir)], env=env)
            run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            draft_p = Path(str(prep["draft_findings_path"]))
            draft_p.write_text("[]\n", encoding="utf-8")
            run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_p)], env=env)
            nonce = extract_challenge_nonce(Path(str(prep["adversarial_input_path"])))
            resp_p = Path(str(prep["adversarial_response_path"]))
            resp_p.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.py", "tests/test_discount_calc.py"],
                        "audit_notes": "変更された上限計算と単体テストの整合を確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_p)],
                env=env,
            )

            # Valid concise table-free Nit + Question targeting changed file src/discountCalc.py
            concise_findings = "\n".join(
                [
                    "### 🔵 Nit: 割引上限額のリテラルをモジュール先頭の定数へ切り出すと意図が明確になる",
                    "",
                    "- [discountCalc.py:L2-L3](src/discountCalc.py#L2-L3) の上限値を関数内リテラルからモジュール定数へ移動すると単体テストからも同じ上限値を参照できます。",
                    "",
                    "### ❓ Question: 端数切り捨ての順序を上限比較の前に置いている仕様の確認",
                    "",
                    "- [discountCalc.py:L2-L3](src/discountCalc.py#L2-L3) で1割算の切り捨て後に上限と比較していますが、会計仕様通りか念のため確認させてください。",
                ]
            )
            review_ok = Path(str(prep["review_output_path"]))
            review_ok.write_text(build_clean_full_review(findings_body=concise_findings), encoding="utf-8")
            rc_v, out_v, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_ok)],
                env=env,
            )
            self.assertEqual(rc_v, 0, json.dumps(out_v, ensure_ascii=False))
            self.assertTrue(out_v["passed"])

            # Verify post_pending_review.py --dry-run converts concise Nit & Question into inline diff comments!
            rc_post, post_out, _, stderr_post = run_script(
                "post_pending_review.py",
                [
                    "--session",
                    str(sdir),
                    "--repo",
                    "example/shop",
                    "--pr",
                    "7",
                    "--review-md",
                    str(review_ok),
                    "--dry-run",
                ],
                env=env,
            )
            self.assertEqual(rc_post, 0, stderr_post)
            self.assertEqual(post_out["inline_count"], 2)
            payload = post_out["payload"]
            self.assertIsInstance(payload, dict)
            if isinstance(payload, dict):
                self.assertNotIn("event", payload)
                comments = payload.get("comments", [])
                self.assertEqual(len(comments), 2)
                first_comment = comments[0]
                self.assertEqual(first_comment["path"], "src/discountCalc.py")
                self.assertEqual(first_comment["start_line"], 2)
                self.assertEqual(first_comment["line"], 3)
                self.assertIn(f"https://github.com/example/shop/blob/{head_sha}/src/discountCalc.py#L2-L3", first_comment["body"])

            # Reject concise Nit targeting UNCHANGED file src/unchangedHelper.py
            unchanged_nit = "\n".join(
                [
                    "### 🔵 Nit: 未変更ファイルの定数整理",
                    "",
                    "- [unchangedHelper.py:L1-L2](src/unchangedHelper.py#L1-L2) の返り値を定数化すると読みやすくなります。",
                ]
            )
            bad_unchanged = sdir / "bad_unchanged.md"
            bad_unchanged.write_text(build_clean_full_review(findings_body=unchanged_nit), encoding="utf-8")
            rc_bu, out_bu, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(bad_unchanged)],
                env=env,
            )
            self.assertEqual(rc_bu, 1)
            self.assertFalse(out_bu["passed"])

            # Reject concise Nit complaining about PR description metadata
            pr_meta_nit = "\n".join(
                [
                    "### 🔵 Nit: 変更内容の記載整理",
                    "",
                    "- [discountCalc.py:L2-L3](src/discountCalc.py#L2-L3) の上限変更についてPRの概要にも追記してください。",
                ]
            )
            bad_meta = sdir / "bad_meta.md"
            bad_meta.write_text(build_clean_full_review(findings_body=pr_meta_nit), encoding="utf-8")
            rc_bm, out_bm, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(bad_meta)],
                env=env,
            )
            self.assertEqual(rc_bm, 1)
            self.assertFalse(out_bm["passed"])

            # Reject table-free 🔴 Bug (Bug/Risk still require 4-col table!)
            tableless_bug = "\n".join(
                [
                    "### 🔴 Bug: 負数時に割引計算が誤る",
                    "",
                    "- [discountCalc.py:L2-L3](src/discountCalc.py#L2-L3) で負数がそのまま返ります。",
                    "- 0以上へ制限してください。",
                ]
            )
            bad_bug = sdir / "bad_bug.md"
            bad_bug.write_text(
                build_clean_full_review(
                    verdict="🔴 Request changes",
                    alert_tag="[!CAUTION]",
                    findings_body=tableless_bug,
                ),
                encoding="utf-8",
            )
            rc_bb, out_bb, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(bad_bug)],
                env=env,
            )
            self.assertEqual(rc_bb, 1)
            self.assertFalse(out_bb["passed"])

            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)

    def test_temp_leak_patterns_and_standalone_builtin_prose_linter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"YOMIYASU_LINT_DISABLE": "1"}

            # 1. Temp path leak detection including /.gemini/tmp/
            leaked_md = build_clean_full_review().replace(
                "src/discountCalc.py",
                ".gemini/tmp/yharuto-code-review/src/discountCalc.py",
            )
            leak_file = root / "leak.md"
            leak_file.write_text(leaked_md, encoding="utf-8")
            rc_lk, out_lk, _, _ = run_script(
                "validate_review_output.py",
                ["--standalone-mode", "full", "--file", str(leak_file)],
                env=env,
            )
            self.assertEqual(rc_lk, 1)
            struct_errs = [str(e) for e in out_lk.get("structural_errors", [])] if isinstance(out_lk.get("structural_errors"), list) else []
            self.assertTrue(any("gemini_tmp_dir" in e for e in struct_errs), f"Got: {struct_errs}")

            # 2. Built-in prose linter standalone mode (YOMIYASU_LINT_DISABLE=1)
            old_disable = os.environ.get("YOMIYASU_LINT_DISABLE")
            os.environ["YOMIYASU_LINT_DISABLE"] = "1"
            try:
                clean_findings, backend = run_prose_lint(build_clean_full_review())
                self.assertEqual(backend, "builtin")
                self.assertEqual(clean_findings, [])

                # Backticked `Test Gate` must NOT trigger bare_katakana_gate
                with_backtick_gate = "- `Test Gate` の確認結果は正常です。"
                self.assertEqual(lint_prose_builtin(with_backtick_gate), [])
            finally:
                if old_disable is None:
                    os.environ.pop("YOMIYASU_LINT_DISABLE", None)
                else:
                    os.environ["YOMIYASU_LINT_DISABLE"] = old_disable

            bad_samples: list[tuple[str, str]] = [
                ("- 入力が欠損すると静かに壊れる経路があります。", "metaphor_verb"),
                ("- エラー時に黙って失敗するため原因を追えません。", "metaphor_verb"),
                ("- 例外を握りつぶす実装になっています。", "metaphor_verb"),
                ("- 下位モジュールが上位の事情を知りすぎています。", "personification_or_calque"),
                ("- モックテストによる偽緑が発生しています。", "personification_or_calque"),
                ("- 障害の根因は初期化順序です。", "personification_or_calque"),
                ("- 設定を切り替えることが可能です。", "ai_verbose_expression"),
                ("- 多角的な観点から確認しました。", "ai_verbose_expression"),
                ("- データの整合性を担保します。", "ai_verbose_expression"),
                ("- 境界値の検証を実施しました。", "ai_verbose_expression"),
                ("- このファンクションは数値を返します。", "ai_verbose_expression"),
                ("- レビューゲートを通過しました。", "bare_katakana_gate"),
                ("- 重要なのは境界値の扱いです。", "preamble_filler"),
                ("- 結論から言うと修正が必要です。", "preamble_filler"),
                ("- 特筆すべきは例外経路です。", "preamble_filler"),
                ("- 入力値 — 特に負数 — の確認が必要です。", "decorative_dash"),
                ("- 確認事項は次のとおりです:", "trailing_colon"),
                ("- 上限を超えた場合に例外を送出する必要がある。", "style_mix_plain_form"),
                ("- 確認しました 🎉。", "emoji_prohibited"),
            ]
            for line_text, expected_rule in bad_samples:
                hits = lint_prose_builtin(line_text)
                hit_rules = {str(h["rule"]) for h in hits}
                self.assertIn(
                    expected_rule,
                    hit_rules,
                    f"Expected rule '{expected_rule}' on {line_text!r}, got {hits}",
                )


import os  # noqa: E402

if __name__ == "__main__":
    unittest.main()
