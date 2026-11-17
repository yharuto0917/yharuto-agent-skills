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


if __name__ == "__main__":
    unittest.main()
