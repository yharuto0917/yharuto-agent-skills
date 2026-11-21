#!/usr/bin/env python3
"""Unit tests for v3 §2: HMAC-SHA256 state sealing and Stage 1/3/4 substantive evidence locks."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import stat
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
from _lib import session_key_path  # noqa: E402


class TestHmacAndEvidenceLocks(unittest.TestCase):
    def _create_smelly_repo(self, repo: Path) -> None:
        init_git_repo(repo)
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "discountCalc.ts").write_text(
            "export function computeDiscount(amount: number): number {\n"
            "  return Math.max(0, Math.floor(amount * 0.1));\n"
            "}\n",
            encoding="utf-8",
        )
        (repo / "src" / "secondaryRule.ts").write_text(
            "export function clampRate(rate: number): number {\n"
            "  return Math.min(rate, 50);\n"
            "}\n",
            encoding="utf-8",
        )
        (repo / "tests" / "discount.test.mjs").write_text(
            "import test from 'node:test';\n"
            "import assert from 'node:assert/strict';\n"
            "test('ok', () => { assert.equal(1 + 1, 2); });\n",
            encoding="utf-8",
        )
        (repo / "package.json").write_text(
            json.dumps({"name": "lock-demo", "scripts": {"test": "node --test tests/discount.test.mjs"}}),
            encoding="utf-8",
        )
        git_commit_all(repo, "base")

        run_git(repo, ["checkout", "-b", "pr-branch"])
        # Introduce an explicit smell (as any + empty catch) on added lines
        (repo / "src" / "discountCalc.ts").write_text(
            "export function computeDiscount(amount: number): number {\n"
            "  try {\n"
            "    const raw = (amount as any) * 0.1;\n"
            "    return Math.max(0, Math.min(Math.floor(raw), 500));\n"
            "  } catch (e) {}\n"
            "  return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (repo / "src" / "secondaryRule.ts").write_text(
            "export function clampRate(rate: number): number {\n"
            "  return Math.max(0, Math.min(rate, 50));\n"
            "}\n",
            encoding="utf-8",
        )
        git_commit_all(repo, "pr changes")

    def test_hmac_key_permissions_and_tampering_rejected_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            self._create_smelly_repo(repo)

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, stdout_raw, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc, 0)
            sdir = Path(str(prep["session_dir"]))
            key_file = session_key_path(sdir)
            self.assertTrue(key_file.is_file())
            mode_bits = stat.S_IMODE(key_file.stat().st_mode)
            self.assertEqual(mode_bits, 0o600)
            # Secret key hex must NEVER be printed to stdout
            secret_hex = key_file.read_text(encoding="utf-8").strip()
            self.assertNotIn(secret_hex, stdout_raw)

            # Manually tamper gate_state.json to spoof passed stages
            state_path = sdir / "gate_state.json"
            tampered = json.loads(state_path.read_text(encoding="utf-8"))
            tampered["stage1_scan"] = {"executed": True, "sha256": "a" * 64}
            tampered["stage1"] = {"status": "OK", "verified_tests": []}
            tampered["stage2"] = {"executed": True, "overall_status": "passed", "checks": [], "log_sha256": "b" * 64}
            tampered["stage3"] = {"smell_scan_executed": True, "smell_sha256": "c" * 64, "smell_count": 0}
            tampered["stage4"] = {"prepared": True, "executed": True, "challenge_nonce_verified": True}
            state_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

            # Every downstream script must detect HMAC tampering
            rc_ci, out_ci, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_ci, 2)
            self.assertIn("HMAC", str(out_ci.get("error", "")))

            rc_sm, out_sm, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_sm, 2)
            self.assertIn("HMAC", str(out_sm.get("error", "")))

            review_file = Path(str(prep["review_output_path"]))
            review_file.write_text(
                build_clean_full_review(file_rel="src/discountCalc.ts", file_label="discountCalc.ts"),
                encoding="utf-8",
            )
            rc_val, out_val, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_file)],
                env=env,
            )
            self.assertEqual(rc_val, 1)
            self.assertFalse(out_val["passed"])
            gate_errors = [str(e) for e in out_val.get("gate_errors", [])] if isinstance(out_val.get("gate_errors"), list) else []
            self.assertTrue(any("HMAC" in e for e in gate_errors), f"Expected HMAC gate error, got {gate_errors}")

            # Audit log records hmac_verified=False when tampered before cleanup
            audit_file = root / "tampered_audit.json"
            env_audit = dict(env)
            env_audit["YHARUTO_REVIEW_AUDIT_LOG"] = str(audit_file)
            rc_cl, _, _, _ = run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env_audit)
            self.assertEqual(rc_cl, 0)
            audit_obj = json.loads(audit_file.read_text(encoding="utf-8"))
            self.assertIs(audit_obj["hmac_verified"], False)
            self.assertFalse(key_file.exists())

    def test_stage1_stage3_stage4_substantive_locks_and_cli_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            self._create_smelly_repo(repo)

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc, 0)
            sdir = Path(str(prep["session_dir"]))

            # 1. Skipping impact_scan.py before gate-stage1 -> rejected!
            rc_s1_skip, out_s1_skip, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/discount.test.mjs"],
                env=env,
            )
            self.assertEqual(rc_s1_skip, 2)
            self.assertIn("impact_scan.py", str(out_s1_skip.get("error", "")))

            # Execute impact_scan.py and verify extracted payload (body-only edits have 0 changed symbol headers)
            rc_imp, imp_out, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp, 0)
            self.assertEqual(imp_out.get("symbols"), [])
            self.assertEqual(imp_out.get("related_tests_and_mocks"), [])
            self.assertEqual(imp_out.get("comparative_files"), [])
            self.assertEqual(imp_out.get("build_hash_configs"), [])

            # 2. Passing production file instead of test file to --verified-tests -> rejected!
            rc_prod_test, out_prod_test, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "src/discountCalc.ts"],
                env=env,
            )
            self.assertEqual(rc_prod_test, 2)
            self.assertIn("TEST_PATH_RE", str(out_prod_test.get("error", "")))

            # 3. EXEMPT when testable_code_files is non-empty -> rejected!
            rc_ex, _, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "EXEMPT"],
                env=env,
            )
            self.assertEqual(rc_ex, 2)

            # Valid Stage 1 OK
            rc_s1_ok, _, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/discount.test.mjs"],
                env=env,
            )
            self.assertEqual(rc_s1_ok, 0)

            # 4. Running scan_diff_smells before Stage 2 -> rejected!
            rc_sm_early, _, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_sm_early, 2)

            # Run Stage 2
            rc_s2, _, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_s2, 0)

            # 5. Skipping scan_diff_smells before prepare-stage4 -> rejected!
            draft_path = Path(str(prep["draft_findings_path"]))
            draft_path.write_text("[]\n", encoding="utf-8")
            rc_p4_skip, out_p4_skip, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            self.assertEqual(rc_p4_skip, 2)
            self.assertIn("scan_diff_smells.py", str(out_p4_skip.get("error", "")))

            # Run Stage 3 scan_diff_smells -> finds exact rule_id and line numbers in src/discountCalc.ts
            rc_s3, smells_out, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_s3, 0)
            smell_items = smells_out.get("findings", [])
            self.assertIsInstance(smell_items, list)
            self.assertEqual(
                [
                    (str(item.get("file")), int(item.get("line", 0)), str(item.get("rule_id")))
                    for item in smell_items
                    if isinstance(item, dict)
                ],
                [
                    ("src/discountCalc.ts", 3, "TYPE_BYPASS_OR_SILENT_FALLBACK"),
                    ("src/discountCalc.ts", 5, "EMPTY_OR_BROAD_CATCH"),
                ],
            )

            # 6. Empty draft [] when smell_count > 0 -> rejected!
            rc_p4_empty, out_p4_empty, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            self.assertEqual(rc_p4_empty, 2)
            self.assertIn("unaccounted_smells", out_p4_empty)

            # 7. dismissed_smells with too-short reason (<10 chars) -> rejected!
            draft_path.write_text(
                json.dumps(
                    {
                        "findings": [],
                        "dismissed_smells": [
                            {"rule_id": "EMPTY_OR_BROAD_CATCH", "file": "src/discountCalc.ts", "reason": "short"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rc_p4_short, _, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            self.assertEqual(rc_p4_short, 2)

            # 8. Valid draft with 1 finding + concrete dismissed_smells (or finding covering file) -> succeeds!
            draft_path.write_text(
                json.dumps(
                    {
                        "findings": [
                            {
                                "id": "F1",
                                "file": "src/discountCalc.ts",
                                "line": "L5",
                                "severity": "Risk",
                                "claim": "例外捕捉ブロックが空のため入力型不正が0円割引として扱われる",
                            }
                        ],
                        "dismissed_smells": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_p4_ok, _, p4_stdout, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            self.assertEqual(rc_p4_ok, 0)

            adv_in = Path(str(prep["adversarial_input_path"]))
            nonce = extract_challenge_nonce(adv_in)
            # Nonce must NOT be leaked on prepare-stage4 stdout
            self.assertNotIn(nonce, p4_stdout)

            resp_path = Path(str(prep["adversarial_response_path"]))

            # 9. Wrong challenge_nonce -> rejected!
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": "0" * 32,
                        "challenged_findings": [{"id": "F1", "verdict": "keep", "reason": "実際のコードで空catchを確認しました。"}],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts", "src/secondaryRule.ts"],
                        "audit_notes": "全対象ファイルと境界条件・例外経路を検証しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r_bad_nonce, _, _, _ = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                env=env,
            )
            self.assertEqual(rc_r_bad_nonce, 2)

            # 10. Missing draft ID F1 in challenged_findings -> rejected!
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts", "src/secondaryRule.ts"],
                        "audit_notes": "全対象ファイルと境界条件・例外経路を検証しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r_no_id, _, _, _ = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                env=env,
            )
            self.assertEqual(rc_r_no_id, 2)

            # 11. Short audit_notes (<20 chars) -> rejected!
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [{"id": "F1", "verdict": "keep", "reason": "実際のコードで空catchを確認しました。"}],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts", "src/secondaryRule.ts"],
                        "audit_notes": "短いメモ",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r_short_notes, _, _, _ = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                env=env,
            )
            self.assertEqual(rc_r_short_notes, 2)

            # 12. Incomplete checked_files (missing src/secondaryRule.ts from testable_code_files) -> rejected!
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [{"id": "F1", "verdict": "keep", "reason": "実際のコードで空catchを確認しました。"}],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts"],
                        "audit_notes": "全対象ファイルと境界条件・例外経路を検証しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r_incomplete, out_inc, _, _ = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                env=env,
            )
            self.assertEqual(rc_r_incomplete, 2)
            self.assertIn("secondaryRule.ts", str(out_inc.get("error", "")))

            # 13. Valid run-stage4-cli helper reads {input_path}, echoes challenge_nonce, and covers all testable files!
            cli_helper = root / "mock_stage4_verifier.py"
            cli_helper.write_text(
                "import json, pathlib, re, sys\n"
                "sys.dont_write_bytecode = True\n"
                "txt = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
                "m = re.search(r'Challenge Nonce:\\s*`([0-9a-f]+)`', txt)\n"
                "print(json.dumps({\n"
                "    'challenge_nonce': m.group(1),\n"
                "    'challenged_findings': [{'id': 'F1', 'verdict': 'dismiss', 'reason': '呼び出し元で数値型が事前検証済みのため誤検知です。'}],\n"
                "    'missed_findings': [],\n"
                "    'checked_files': ['src/discountCalc.ts', 'src/secondaryRule.ts', 'tests/discount.test.mjs'],\n"
                "    'audit_notes': '両方の対象モジュールと単体テストの境界条件を照合し問題ないことを確認しました。'\n"
                "}, ensure_ascii=False))\n",
                encoding="utf-8",
            )
            rc_cli, cli_out, _, stderr_cli = run_script(
                "prepare_worktree.py",
                [
                    "run-stage4-cli",
                    "--session",
                    str(sdir),
                    "--cmd",
                    f"{sys.executable} -B {cli_helper} {{input_path}}",
                ],
                env=env,
            )
            self.assertEqual(rc_cli, 0, stderr_cli)
            self.assertIs(cli_out["challenge_nonce_verified"], True)
            self.assertEqual(cli_out["verifier_mode"], "cli_agent")

            # Stage 5 validate + cleanup with audit log
            review_file = Path(str(prep["review_output_path"]))
            review_file.write_text(
                build_clean_full_review(file_rel="src/discountCalc.ts", file_label="discountCalc.ts"),
                encoding="utf-8",
            )
            rc_v, val_out, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_file)],
                env=env,
            )
            self.assertEqual(rc_v, 0, json.dumps(val_out, ensure_ascii=False))
            self.assertTrue(val_out["passed"])

            audit_dest = root / "clean_audit.json"
            env_clean = dict(env)
            env_clean["YHARUTO_REVIEW_AUDIT_LOG"] = str(audit_dest)
            rc_c, _, _, _ = run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env_clean)
            self.assertEqual(rc_c, 0)
            audit_json = json.loads(audit_dest.read_text(encoding="utf-8"))
            self.assertIs(audit_json["hmac_verified"], True)
            self.assertTrue(audit_json["stage1_scan"]["executed"])
            self.assertTrue(audit_json["stage3"]["smell_scan_executed"])
            self.assertTrue(audit_json["stage4"]["challenge_nonce_verified"])
            self.assertTrue(audit_json["stage5"]["validated"])

    def test_skip_test_on_prepare_continues_past_stage1_ng_through_stage5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            self._create_smelly_repo(repo)

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, stderr = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc, 0, stderr)
            self.assertIs(prep.get("skip_test"), True)
            sdir = Path(str(prep["session_dir"]))

            rc_imp, _, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp, 0)

            rc_s1, out_s1, _, stderr_s1 = run_script(
                "prepare_worktree.py",
                [
                    "gate-stage1",
                    "--session",
                    str(sdir),
                    "--verdict",
                    "NG",
                    "--gaps-count",
                    "2",
                    "--summary",
                    "異常系・境界値テストが不足しています",
                ],
                env=env,
            )
            self.assertEqual(rc_s1, 0, stderr_s1)
            self.assertIs(out_s1.get("skip_test"), True)
            self.assertEqual(out_s1["stage1"]["status"], "NG")
            self.assertIs(out_s1["stage1"]["bypassed_by_skip_test"], True)

            # stage1_ng early-return output must be rejected when --skip-test is active
            ng_review = sdir / "early_ng.md"
            ng_review.write_text(
                build_clean_stage1_ng_review(
                    code_rel="src/discountCalc.ts",
                    code_label="discountCalc.ts",
                    test_rel="tests/discount.test.mjs",
                    test_label="discount.test.mjs",
                ),
                encoding="utf-8",
            )
            rc_ng_early, out_ng_early, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(ng_review)],
                env=env,
            )
            self.assertEqual(rc_ng_early, 1)
            self.assertFalse(out_ng_early["passed"])

            # Stage 2, Stage 3, Stage 4 must all unlock and succeed
            rc_s2, _, _, stderr_s2 = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_s2, 0, stderr_s2)

            rc_s3, _, _, stderr_s3 = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_s3, 0, stderr_s3)

            draft_path = Path(str(prep["draft_findings_path"]))
            draft_path.write_text(
                json.dumps(
                    {
                        "findings": [
                            {
                                "id": "F1",
                                "file": "src/discountCalc.ts",
                                "line": "L3-L4",
                                "severity": "Nit",
                                "claim": "割引率の上限値をモジュール定数へ切り出すと意図が追いやすくなります",
                            }
                        ],
                        "dismissed_smells": [
                            {
                                "rule_id": "TYPE_BYPASS_OR_SILENT_FALLBACK",
                                "file": "src/discountCalc.ts",
                                "reason": "検証用の最小再現変更であり実仕様の本件確認対象から除外します。",
                            },
                            {
                                "rule_id": "EMPTY_OR_BROAD_CATCH",
                                "file": "src/discountCalc.ts",
                                "reason": "検証用の最小再現変更であり本件テストでは軽微指摘のみ扱います。",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_p4, _, _, stderr_p4 = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            self.assertEqual(rc_p4, 0, stderr_p4)

            adv_in = Path(str(prep["adversarial_input_path"]))
            self.assertIn("Stage 1 Status: `NG` (skip_test=`True`)", adv_in.read_text(encoding="utf-8"))
            nonce = extract_challenge_nonce(adv_in)

            resp_path = Path(str(prep["adversarial_response_path"]))
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [
                            {
                                "id": "F1",
                                "verdict": "keep",
                                "reason": "変更行の上限リテラル定数化提案は妥当です。",
                            }
                        ],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts", "src/secondaryRule.ts"],
                        "audit_notes": "変更対象コードと関連テストを確認し継続レビューを完了しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r4, _, _, stderr_r4 = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                env=env,
            )
            self.assertEqual(rc_r4, 0, stderr_r4)

            # Still reject stage1_ng output after Stage 2-4 completed
            rc_ng_after, out_ng_after, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(ng_review)],
                env=env,
            )
            self.assertEqual(rc_ng_after, 1)
            self.assertFalse(out_ng_after["passed"])

            nit_findings = "\n".join(
                [
                    "### 🔵 Nit: 割引上限リテラルの定数化",
                    "",
                    "- [discountCalc.ts:L3-L4](src/discountCalc.ts#L3-L4) の上限値リテラルを名前付き定数へ切り出すと意図が追いやすくなります。",
                ]
            )
            review_file = Path(str(prep["review_output_path"]))

            # Both 🟡 Comment and 🟢 Approve pass validation when Bug=0/Risk=0 under --skip-test
            review_file.write_text(
                build_clean_full_review(
                    verdict="🟡 Comment",
                    alert_tag="[!WARNING]",
                    alert_headline="**テスト不足（Stage 1 NG）は --skip-test により継続し、構造上の軽微改善があります**",
                    alert_reason="Stage 1 のテスト不足を除外した範囲で確定不具合は確認されませんでした。",
                    file_rel="src/discountCalc.ts",
                    file_label="discountCalc.ts",
                    findings_body=nit_findings,
                ),
                encoding="utf-8",
            )
            rc_val_y, out_val_y, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_file)],
                env=env,
            )
            self.assertEqual(rc_val_y, 0, json.dumps(out_val_y, ensure_ascii=False))
            self.assertTrue(out_val_y["passed"])

            review_file.write_text(
                build_clean_full_review(
                    verdict="🟢 Approve",
                    alert_tag="[!TIP]",
                    file_rel="src/discountCalc.ts",
                    file_label="discountCalc.ts",
                    findings_body=nit_findings,
                ),
                encoding="utf-8",
            )
            rc_val_g, out_val_g, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_file)],
                env=env,
            )
            self.assertEqual(rc_val_g, 0, json.dumps(out_val_g, ensure_ascii=False))
            self.assertTrue(out_val_g["passed"])

            # post_pending_review.py --dry-run must execute in full mode with inline diff comment
            rc_post, post_out, _, stderr_post = run_script(
                "post_pending_review.py",
                [
                    "--session",
                    str(sdir),
                    "--repo",
                    "example/shop",
                    "--pr",
                    "42",
                    "--review-md",
                    str(review_file),
                    "--dry-run",
                ],
                env=env,
            )
            self.assertEqual(rc_post, 0, stderr_post)
            self.assertEqual(post_out["status"], "dry_run_ok")
            self.assertEqual(post_out["inline_count"], 1)

            audit_dest = root / "skip_test_audit.json"
            env_audit = dict(env)
            env_audit["YHARUTO_REVIEW_AUDIT_LOG"] = str(audit_dest)
            rc_clean, _, _, _ = run_script(
                "prepare_worktree.py",
                ["cleanup", "--session", str(sdir)],
                env=env_audit,
            )
            self.assertEqual(rc_clean, 0)
            audit_json = json.loads(audit_dest.read_text(encoding="utf-8"))
            self.assertIs(audit_json["hmac_verified"], True)
            self.assertIs(audit_json["skip_test"], True)
            self.assertEqual(audit_json["stage1"]["status"], "NG")
            self.assertIs(audit_json["stage1"]["bypassed_by_skip_test"], True)
            self.assertIs(audit_json["stage2"]["executed"], True)
            self.assertIs(audit_json["stage4"]["challenge_nonce_verified"], True)
            self.assertIs(audit_json["stage5"]["validated"], True)

    def test_skip_test_flag_on_gate_stage1_and_non_skip_blocking_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            self._create_smelly_repo(repo)
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # 1. Without --skip-test: SKIP rejected, NG blocks Stage 2 & 4, then --skip-test on gate-stage1 NG unlocks
            rc_a, prep_a, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc_a, 0)
            self.assertIs(prep_a.get("skip_test"), False)
            sdir_a = Path(str(prep_a["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_a)], env=env)[0], 0)

            rc_skip_no_flag, out_skip_no_flag, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_a), "--verdict", "SKIP"],
                env=env,
            )
            self.assertEqual(rc_skip_no_flag, 2)
            self.assertIn("--skip-test", str(out_skip_no_flag.get("error", "")))

            rc_ng_no_flag, out_ng_no_flag, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_a), "--verdict", "NG", "--gaps-count", "1", "--summary", "不足あり"],
                env=env,
            )
            self.assertEqual(rc_ng_no_flag, 0)
            self.assertIs(out_ng_no_flag["stage1"]["bypassed_by_skip_test"], False)
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_a)], env=env)[0], 2)

            draft_a = Path(str(prep_a["draft_findings_path"]))
            draft_a.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["prepare-stage4", "--session", str(sdir_a), "--draft", str(draft_a)],
                    env=env,
                )[0],
                2,
            )

            # Invalid --gaps-count 0 even with --skip-test must still reject
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir_a), "--verdict", "NG", "--gaps-count", "0", "--skip-test"],
                    env=env,
                )[0],
                2,
            )

            # Passing --skip-test directly on gate-stage1 (verdict NG without explicit gaps/summary) unlocks Stage 2
            rc_ng_skip, out_ng_skip, _, stderr_ng_skip = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_a), "--verdict", "NG", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_ng_skip, 0, stderr_ng_skip)
            self.assertIs(out_ng_skip.get("skip_test"), True)
            self.assertEqual(out_ng_skip["stage1"]["status"], "NG")
            self.assertEqual(out_ng_skip["stage1"]["gaps_count"], 1)
            self.assertIs(out_ng_skip["stage1"]["bypassed_by_skip_test"], True)
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_a)], env=env)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_a)], env=env)[0], 0)

            # 2. gate-stage1 --verdict SKIP --skip-test runs full Stage 2-5 pipeline
            rc_b, prep_b, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc_b, 0)
            sdir_b = Path(str(prep_b["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_b)], env=env)[0], 0)
            rc_s1_skip, out_s1_skip, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_b), "--verdict", "SKIP", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_s1_skip, 0)
            self.assertEqual(out_s1_skip["stage1"]["status"], "SKIPPED_BY_FLAG")
            self.assertIs(out_s1_skip["stage1"]["bypassed_by_skip_test"], True)
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_b)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir_b)], env=env)[0], 0)
            draft_b = Path(str(prep_b["draft_findings_path"]))
            draft_b.write_text(
                json.dumps(
                    {
                        "findings": [],
                        "dismissed_smells": [
                            {
                                "rule_id": "TYPE_BYPASS_OR_SILENT_FALLBACK",
                                "file": "src/discountCalc.ts",
                                "reason": "検証用フィクスチャの型回避行であり本テスト確認対象外です。",
                            },
                            {
                                "rule_id": "EMPTY_OR_BROAD_CATCH",
                                "file": "src/discountCalc.ts",
                                "reason": "検証用フィクスチャの空捕捉行であり本テスト確認対象外です。",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["prepare-stage4", "--session", str(sdir_b), "--draft", str(draft_b)],
                    env=env,
                )[0],
                0,
            )
            nonce_b = extract_challenge_nonce(Path(str(prep_b["adversarial_input_path"])))
            resp_b = Path(str(prep_b["adversarial_response_path"]))
            resp_b.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce_b,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.ts", "src/secondaryRule.ts"],
                        "audit_notes": "SKIP指定時の全ステージ完走および対象コード確認を実施しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["record-stage4", "--session", str(sdir_b), "--verifier-mode", "subagent", "--response-file", str(resp_b)],
                    env=env,
                )[0],
                0,
            )
            review_b = Path(str(prep_b["review_output_path"]))
            review_b.write_text(
                build_clean_full_review(file_rel="src/discountCalc.ts", file_label="discountCalc.ts"),
                encoding="utf-8",
            )
            rc_vb, out_vb, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir_b), "--file", str(review_b)],
                env=env,
            )
            self.assertEqual(rc_vb, 0, json.dumps(out_vb, ensure_ascii=False))
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_b)], env=env)[0], 0)

            # 3. gate-stage1 --verdict EXEMPT --skip-test and --verdict OK --skip-test both unlock Stage 2
            rc_c, prep_c, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc_c, 0)
            sdir_c = Path(str(prep_c["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_c)], env=env)[0], 0)

            rc_ex_skip, out_ex_skip, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_c), "--verdict", "EXEMPT", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_ex_skip, 0)
            self.assertEqual(out_ex_skip["stage1"]["status"], "SKIPPED_BY_FLAG")
            self.assertIs(out_ex_skip["stage1"]["bypassed_by_skip_test"], True)
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_c)], env=env)[0], 0)

            rc_ok_skip, out_ok_skip, _, _ = run_script(
                "prepare_worktree.py",
                [
                    "gate-stage1",
                    "--session",
                    str(sdir_c),
                    "--verdict",
                    "OK",
                    "--verified-tests",
                    "tests/discount.test.mjs",
                    "--skip-test",
                ],
                env=env,
            )
            self.assertEqual(rc_ok_skip, 0)
            self.assertEqual(out_ok_skip["stage1"]["status"], "OK")
            self.assertIs(out_ok_skip["stage1"]["bypassed_by_skip_test"], False)
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_c)], env=env)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_c)], env=env)[0], 0)

    def test_skip_test_revoked_when_tests_fail_but_allowed_when_only_lint_fails(self) -> None:
        from _lib import find_stage2_test_failures

        # Unit check: URL / description / passing test names containing 'error'/'fail'/'stale' must NOT false-positive
        self.assertEqual(
            find_stage2_test_failures(
                [
                    {
                        "tool": "gh_pr_checks",
                        "status": "failed",
                        "summary_lines": [
                            "lint\tfail\t2s\thttps://github.com/org/my-test-repo/actions/runs/1\t1 error in tests/foo.test.ts",
                            "unit-tests\tpass\t5s\thttps://github.com/org/my-test-repo/actions/runs/2\t0 failed, 42 passed",
                            "test-error-handling: SUCCESS",
                            "unit-test (fail-fast): SUCCESS",
                            "test-stale-cache: SUCCESS",
                            "ci/lint: FAILURE",
                        ],
                    }
                ]
            ),
            [],
        )
        # Positive checks: cancel status, CamelCase unitTest, e2e, nextest, and structured failed_checks
        for sample_check in (
            {
                "tool": "gh_pr_checks",
                "status": "failed",
                "summary_lines": [
                    "unit-tests\tfail\t5s\thttps://github.com/org/repo/actions/runs/3",
                ],
            },
            {
                "tool": "gh_pr_checks",
                "status": "failed",
                "summary_lines": [
                    "ci/e2e\tcancel\t3s\thttps://github.com/org/repo/actions/runs/4",
                ],
            },
            {
                "tool": "gh_pr_checks",
                "status": "failed",
                "failed_checks": ["unitTest"],
                "summary_lines": ["lint: SUCCESS"],
            },
            {
                "tool": "gh_pr_checks",
                "status": "failed",
                "failed_checks": ["cargo nextest"],
                "summary_lines": [],
            },
        ):
            self.assertEqual(find_stage2_test_failures([sample_check]), ["gh_pr_checks"])
        # Non-test words containing 'test' substring (latest / attestation / contest) must NOT match
        self.assertEqual(
            find_stage2_test_failures(
                [
                    {
                        "tool": "gh_pr_checks",
                        "status": "failed",
                        "failed_checks": ["check-latest", "build-attestation", "contest-lint"],
                    }
                ]
            ),
            [],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # 1. Failing test regression repo: --skip-test must be revoked at Stage 2 and enforce stage1_ng
            fail_repo = root / "fail_repo"
            init_git_repo(fail_repo)
            (fail_repo / "src").mkdir()
            (fail_repo / "tests").mkdir()
            (fail_repo / "src" / "discountCalc.mjs").write_text(
                "export function computeDiscount(amount) {\n  return Math.floor(amount * 0.1);\n}\n",
                encoding="utf-8",
            )
            (fail_repo / "tests" / "discount.test.mjs").write_text(
                "import test from 'node:test';\n"
                "import assert from 'node:assert/strict';\n"
                "import { computeDiscount } from '../src/discountCalc.mjs';\n"
                "test('basic discount', () => { assert.equal(computeDiscount(100), 10); });\n",
                encoding="utf-8",
            )
            (fail_repo / "package.json").write_text(
                json.dumps({"name": "fail-demo", "type": "module", "scripts": {"test": "node --test tests/discount.test.mjs"}}),
                encoding="utf-8",
            )
            git_commit_all(fail_repo, "base passing test")

            run_git(fail_repo, ["checkout", "-b", "pr-branch"])
            (fail_repo / "src" / "discountCalc.mjs").write_text(
                "export function computeDiscount(amount) {\n  return Math.floor(amount * 0.2);\n}\n",
                encoding="utf-8",
            )
            git_commit_all(fail_repo, "break discount calculation")

            # 1a. --verdict NG with --skip-test -> Stage 2 fails test -> revoked -> stage1_ng enforced
            rc1, prep1, _, stderr1 = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--range", "main..pr-branch", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc1, 0, stderr1)
            sdir1 = Path(str(prep1["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir1)], env=env)[0], 0)
            rc_g1, out_g1, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir1), "--verdict", "NG", "--gaps-count", "1", "--summary", "異常系テスト不足"],
                env=env,
            )
            self.assertEqual(rc_g1, 0)
            self.assertIs(out_g1["stage1"]["bypassed_by_skip_test"], True)

            rc_ci1, out_ci1, _, stderr_ci1 = run_script("run_checks.py", ["--session", str(sdir1)], env=env)
            self.assertEqual(rc_ci1, 0, stderr_ci1)
            self.assertIs(out_ci1.get("skip_test_revoked"), True)
            self.assertEqual(out_ci1.get("required_output_mode"), "stage1_ng")

            # Stage 3, Stage 4, gate-stage1 re-override, and Stage 2 re-run must all be blocked
            rc_sm1, out_sm1, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir1)], env=env)
            self.assertEqual(rc_sm1, 2)
            self.assertIs(out_sm1.get("skip_test_revoked"), True)

            draft1 = Path(str(prep1["draft_findings_path"]))
            draft1.write_text("[]\n", encoding="utf-8")
            rc_p4_1, out_p4_1, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir1), "--draft", str(draft1)],
                env=env,
            )
            self.assertEqual(rc_p4_1, 2)
            self.assertIs(out_p4_1.get("skip_test_revoked"), True)

            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir1), "--verdict", "SKIP", "--skip-test"],
                    env=env,
                )[0],
                2,
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir1), "--verdict", "OK", "--verified-tests", "tests/discount.test.mjs"],
                    env=env,
                )[0],
                2,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir1)], env=env)[0], 2)

            # Full review output must be rejected while stage1_ng output passes
            full_md1 = sdir1 / "wrong_full.md"
            full_md1.write_text(
                build_clean_full_review(
                    verdict="🔴 Request changes",
                    alert_tag="[!CAUTION]",
                    file_rel="src/discountCalc.mjs",
                    file_label="discountCalc.mjs",
                ),
                encoding="utf-8",
            )
            rc_vf1, out_vf1, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir1), "--file", str(full_md1)],
                env=env,
            )
            self.assertEqual(rc_vf1, 1)
            self.assertFalse(out_vf1["passed"])
            self.assertEqual(out_vf1["mode"], "stage1_ng")

            ng_md1 = Path(str(prep1["review_output_path"]))
            ng_md1.write_text(
                build_clean_stage1_ng_review(
                    code_rel="src/discountCalc.mjs",
                    code_label="discountCalc.mjs",
                    test_rel="tests/discount.test.mjs",
                    test_label="discount.test.mjs",
                ),
                encoding="utf-8",
            )
            rc_vn1, out_vn1, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir1), "--file", str(ng_md1)],
                env=env,
            )
            self.assertEqual(rc_vn1, 0, json.dumps(out_vn1, ensure_ascii=False))
            self.assertEqual(out_vn1["mode"], "stage1_ng")

            rc_post1, out_post1, _, stderr_post1 = run_script(
                "post_pending_review.py",
                ["--session", str(sdir1), "--repo", "example/shop", "--pr", "10", "--review-md", str(ng_md1), "--dry-run"],
                env=env,
            )
            self.assertEqual(rc_post1, 0, stderr_post1)
            self.assertEqual(out_post1["inline_count"], 0)

            audit1 = root / "revoked_audit.json"
            env_audit1 = dict(env)
            env_audit1["YHARUTO_REVIEW_AUDIT_LOG"] = str(audit1)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir1)], env=env_audit1)[0], 0)
            audit1_obj = json.loads(audit1.read_text(encoding="utf-8"))
            self.assertIs(audit1_obj["hmac_verified"], True)
            self.assertIs(audit1_obj["skip_test"], True)
            self.assertIs(audit1_obj["skip_test_revoked"], True)
            self.assertEqual(audit1_obj["stage1"]["status"], "NG")
            self.assertIs(audit1_obj["stage1"]["bypassed_by_skip_test"], False)

            # 1b. --verdict SKIP with --skip-test -> Stage 2 fails test -> downgraded to NG and stage1_ng passes
            rc1b, prep1b, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--range", "main..pr-branch", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc1b, 0)
            sdir1b = Path(str(prep1b["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir1b)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir1b), "--verdict", "SKIP"], env=env)[0],
                0,
            )
            rc_ci1b, out_ci1b, _, _ = run_script("run_checks.py", ["--session", str(sdir1b)], env=env)
            self.assertEqual(rc_ci1b, 0)
            self.assertIs(out_ci1b.get("skip_test_revoked"), True)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir1b)], env=env)[0], 2)
            ng_md1b = Path(str(prep1b["review_output_path"]))
            ng_md1b.write_text(
                build_clean_stage1_ng_review(
                    code_rel="src/discountCalc.mjs",
                    code_label="discountCalc.mjs",
                    test_rel="tests/discount.test.mjs",
                    test_label="discount.test.mjs",
                ),
                encoding="utf-8",
            )
            rc_vn1b, out_vn1b, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir1b), "--file", str(ng_md1b)],
                env=env,
            )
            self.assertEqual(rc_vn1b, 0, json.dumps(out_vn1b, ensure_ascii=False))
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir1b)], env=env)[0], 0)

            # 2. Passing tests + failing lint: --skip-test stays active because tests themselves passed
            lint_fail_repo = root / "lint_fail_repo"
            init_git_repo(lint_fail_repo)
            (lint_fail_repo / "src").mkdir()
            (lint_fail_repo / "tests").mkdir()
            (lint_fail_repo / "src" / "discountCalc.mjs").write_text(
                "export function computeDiscount(amount) {\n  return Math.floor(amount * 0.1);\n}\n",
                encoding="utf-8",
            )
            (lint_fail_repo / "tests" / "discount.test.mjs").write_text(
                "import test from 'node:test';\n"
                "import assert from 'node:assert/strict';\n"
                "import { computeDiscount } from '../src/discountCalc.mjs';\n"
                "test('basic discount', () => { assert.equal(computeDiscount(100), 10); });\n",
                encoding="utf-8",
            )
            (lint_fail_repo / "lint_runner.mjs").write_text("process.exit(0);\n", encoding="utf-8")
            (lint_fail_repo / "package.json").write_text(
                json.dumps(
                    {
                        "name": "lint-fail-demo",
                        "type": "module",
                        "scripts": {
                            "test": "node --test tests/discount.test.mjs",
                            "lint": "node lint_runner.mjs",
                        },
                    }
                ),
                encoding="utf-8",
            )
            git_commit_all(lint_fail_repo, "base ok")

            run_git(lint_fail_repo, ["checkout", "-b", "pr-branch"])
            (lint_fail_repo / "src" / "discountCalc.mjs").write_text(
                "export function computeDiscount(amount) {\n  return Math.max(0, Math.floor(amount * 0.1));\n}\n",
                encoding="utf-8",
            )
            (lint_fail_repo / "lint_runner.mjs").write_text(
                "console.error('src/discountCalc.mjs:2:1: error lint rule');\nprocess.exit(1);\n",
                encoding="utf-8",
            )
            git_commit_all(lint_fail_repo, "pass test but fail lint")

            rc2, prep2, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(lint_fail_repo), "--range", "main..pr-branch", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc2, 0)
            sdir2 = Path(str(prep2["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir2)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir2), "--verdict", "NG", "--gaps-count", "1", "--summary", "境界値テスト不足"],
                    env=env,
                )[0],
                0,
            )
            rc_ci2, out_ci2, _, _ = run_script("run_checks.py", ["--session", str(sdir2)], env=env)
            self.assertEqual(rc_ci2, 0)
            self.assertNotIn("skip_test_revoked", out_ci2)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir2)], env=env)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir2)], env=env)[0], 0)

            # 3. Fork PR (--untrusted + --pr-meta): gh_pr_checks test failure revokes --skip-test; lint-only failure does not
            base_sha = run_git(fail_repo, ["rev-parse", "main"]).stdout.strip()
            head_sha = run_git(fail_repo, ["rev-parse", "pr-branch"]).stdout.strip()

            meta_test_fail = root / "meta_test_fail.json"
            meta_test_fail.write_text(
                json.dumps(
                    {
                        "number": 77,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [
                            {"name": "unit-tests", "conclusion": "FAILURE"},
                            {"name": "lint", "conclusion": "SUCCESS"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rc3a, prep3a, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--pr", "77", "--pr-meta", str(meta_test_fail), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc3a, 0)
            sdir3a = Path(str(prep3a["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir3a)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir3a), "--verdict", "NG", "--gaps-count", "1", "--summary", "不足"],
                    env=env,
                )[0],
                0,
            )
            rc_ci3a, out_ci3a, _, _ = run_script("run_checks.py", ["--session", str(sdir3a)], env=env)
            self.assertEqual(rc_ci3a, 0)
            self.assertIs(out_ci3a.get("skip_test_revoked"), True)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir3a)], env=env)[0], 2)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir3a)], env=env)[0], 0)

            meta_lint_only = root / "meta_lint_only.json"
            meta_lint_only.write_text(
                json.dumps(
                    {
                        "number": 78,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [
                            {"name": "unit-tests", "conclusion": "SUCCESS"},
                            {"name": "eslint", "conclusion": "FAILURE"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rc3b, prep3b, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--pr", "78", "--pr-meta", str(meta_lint_only), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc3b, 0)
            sdir3b = Path(str(prep3b["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir3b)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir3b), "--verdict", "NG", "--gaps-count", "1", "--summary", "不足"],
                    env=env,
                )[0],
                0,
            )
            rc_ci3b, out_ci3b, _, _ = run_script("run_checks.py", ["--session", str(sdir3b)], env=env)
            self.assertEqual(rc_ci3b, 0)
            self.assertNotIn("skip_test_revoked", out_ci3b)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir3b)], env=env)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir3b)], env=env)[0], 0)

            # 4. >40 pr_meta checks with failing test at #45 (beyond SUMMARY_TAIL_LINES=40) must still revoke --skip-test
            many_checks: list[dict[str, str]] = [
                {"name": f"build-matrix-{idx}", "conclusion": "SUCCESS"} for idx in range(1, 45)
            ]
            many_checks.append({"name": "unitTest", "conclusion": "FAILURE"})
            meta_many = root / "meta_many.json"
            meta_many.write_text(
                json.dumps(
                    {
                        "number": 79,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": many_checks,
                    }
                ),
                encoding="utf-8",
            )
            rc4, prep4, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--pr", "79", "--pr-meta", str(meta_many), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc4, 0)
            sdir4 = Path(str(prep4["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir4)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir4), "--verdict", "NG", "--gaps-count", "1", "--summary", "不足"],
                    env=env,
                )[0],
                0,
            )
            rc_ci4, out_ci4, _, _ = run_script("run_checks.py", ["--session", str(sdir4)], env=env)
            self.assertEqual(rc_ci4, 0)
            self.assertIs(out_ci4.get("skip_test_revoked"), True)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir4)], env=env)[0], 0)

            # 5. Re-running Stage 2 into test failure after Stage 4/5 resets stage5 and blocks record-stage4, run-stage4-cli, and post_pending_review
            rc5, prep5, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(fail_repo), "--pr", "80", "--pr-meta", str(meta_lint_only), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc5, 0)
            sdir5 = Path(str(prep5["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir5)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir5), "--verdict", "NG", "--gaps-count", "1", "--summary", "不足"],
                    env=env,
                )[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir5)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir5)], env=env)[0], 0)
            draft5 = Path(str(prep5["draft_findings_path"]))
            draft5.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["prepare-stage4", "--session", str(sdir5), "--draft", str(draft5)],
                    env=env,
                )[0],
                0,
            )
            nonce5 = extract_challenge_nonce(Path(str(prep5["adversarial_input_path"])))
            resp5 = Path(str(prep5["adversarial_response_path"]))
            resp5.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce5,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.mjs"],
                        "audit_notes": "初回レビュー対象ファイルを確認し記録しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["record-stage4", "--session", str(sdir5), "--verifier-mode", "subagent", "--response-file", str(resp5)],
                    env=env,
                )[0],
                0,
            )
            review5 = Path(str(prep5["review_output_path"]))
            review5.write_text(
                build_clean_full_review(
                    verdict="🔴 Request changes",
                    alert_tag="[!CAUTION]",
                    file_rel="src/discountCalc.mjs",
                    file_label="discountCalc.mjs",
                ),
                encoding="utf-8",
            )
            rc_v5, out_v5, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir5), "--file", str(review5)],
                env=env,
            )
            self.assertEqual(rc_v5, 0, json.dumps(out_v5, ensure_ascii=False))

            # Now re-run Stage 2 with --allow-exec on fail_repo -> local test fails -> revokes --skip-test & resets stage5
            rc_ci5_rerun, out_ci5_rerun, _, _ = run_script(
                "run_checks.py",
                ["--session", str(sdir5), "--allow-exec"],
                env=env,
            )
            self.assertEqual(rc_ci5_rerun, 0)
            self.assertIs(out_ci5_rerun.get("skip_test_revoked"), True)

            # Stale full review MUST be rejected by post_pending_review, and Stage 4 record/CLI MUST be blocked
            rc_post5, out_post5, _, _ = run_script(
                "post_pending_review.py",
                ["--session", str(sdir5), "--repo", "example/shop", "--pr", "80", "--review-md", str(review5), "--dry-run"],
                env=env,
            )
            self.assertEqual(rc_post5, 2)
            self.assertIn("stage5.validated", str(out_post5.get("error", "")))

            rc_r4_5, out_r4_5, _, _ = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir5), "--verifier-mode", "subagent", "--response-file", str(resp5)],
                env=env,
            )
            self.assertEqual(rc_r4_5, 2)
            self.assertIs(out_r4_5.get("skip_test_revoked"), True)

            rc_c4_5, out_c4_5, _, _ = run_script(
                "prepare_worktree.py",
                ["run-stage4-cli", "--session", str(sdir5), "--cmd", f"{sys.executable} -B -c pass"],
                env=env,
            )
            self.assertEqual(rc_c4_5, 2)
            self.assertIs(out_c4_5.get("skip_test_revoked"), True)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir5)], env=env)[0], 0)


if __name__ == "__main__":
    unittest.main()
