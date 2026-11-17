#!/usr/bin/env python3
"""Unit tests for v3 §0: .gemini session roots, isolated worktrees across all 4 modes, and zero git pollution."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import tempfile
import time
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


class TestSessionAndIsolation(unittest.TestCase):
    def test_global_scope_and_canonical_paths_and_zero_git_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_global = root / "global_gemini_tmp"
            repo = root / "repo"
            init_git_repo(repo)

            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return max(0, min(amount // 10, 500))\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_discount_calc.py").write_text(
                "import unittest\nfrom src.discountCalc import compute_discount\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_cap(self) -> None:\n"
                "        self.assertEqual(compute_discount(10000), 500)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "initial")
            run_git(repo, ["checkout", "-b", "feat/cap"])
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return max(0, min(amount // 10, 800))\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_discount_calc.py").write_text(
                "import unittest\nfrom src.discountCalc import compute_discount\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_cap(self) -> None:\n"
                "        self.assertEqual(compute_discount(10000), 800)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "raise cap")

            env = {"YHARUTO_SESSION_ROOT": str(fake_global), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, stderr = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..feat/cap"],
                env=env,
            )
            self.assertEqual(rc, 0, stderr)
            session_dir = Path(str(prep["session_dir"]))
            workdir = Path(str(prep["workdir"]))

            self.assertEqual(prep["session_scope"], "global")
            self.assertEqual(session_dir.parent.resolve(), fake_global.resolve())
            self.assertEqual(workdir.resolve(), (session_dir / "worktree").resolve())
            self.assertEqual(prep["draft_findings_path"], str(session_dir / "draft_findings.json"))
            self.assertEqual(prep["adversarial_input_path"], str(session_dir / "adversarial_input.md"))
            self.assertEqual(prep["adversarial_response_path"], str(session_dir / "adversarial_response.json"))
            self.assertEqual(prep["review_output_path"], str(session_dir / "review.md"))

            # Target repo git status must remain 100% clean while session is active
            porcelain_mid = run_git(repo, ["status", "--porcelain=v1", "-uall"]).stdout.strip()
            self.assertEqual(porcelain_mid, "")

            rc_clean, clean_out, _, _ = run_script(
                "prepare_worktree.py",
                ["cleanup", "--session", str(session_dir)],
                env=env,
            )
            self.assertEqual(rc_clean, 0)
            self.assertEqual(clean_out["status"], "cleaned")
            self.assertFalse(session_dir.exists())
            self.assertFalse(session_key_path(session_dir).exists())

    def test_project_scope_cleanup_prunes_empty_gemini_and_preserves_preexisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Case A: repo without pre-existing .gemini -> cleanup removes .gemini completely
            repo_a = root / "repo_a"
            init_git_repo(repo_a)
            (repo_a / "README.md").write_text("# Demo\n", encoding="utf-8")
            git_commit_all(repo_a, "init")

            rc, prep_a, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_a), "--audit", "--session-scope", "project"],
            )
            self.assertEqual(rc, 0)
            sdir_a = Path(str(prep_a["session_dir"]))
            self.assertTrue((repo_a / ".gemini" / "tmp" / "yharuto-code-review").is_dir())

            rc_c, _, _, _ = run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_a)])
            self.assertEqual(rc_c, 0)
            self.assertFalse((repo_a / ".gemini").exists())
            self.assertEqual(run_git(repo_a, ["status", "--porcelain=v1", "-uall"]).stdout.strip(), "")

            # Case B: repo WITH pre-existing .gemini/settings.json -> .gemini/settings.json preserved
            repo_b = root / "repo_b"
            init_git_repo(repo_b)
            (repo_b / ".gemini").mkdir()
            (repo_b / ".gemini" / "settings.json").write_text("{}\n", encoding="utf-8")
            git_commit_all(repo_b, "init with gemini config")

            rc2, prep_b, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_b), "--audit", "--session-scope", "project"],
            )
            self.assertEqual(rc2, 0)
            sdir_b = Path(str(prep_b["session_dir"]))

            rc_c2, _, _, _ = run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_b)])
            self.assertEqual(rc_c2, 0)
            self.assertTrue((repo_b / ".gemini" / "settings.json").is_file())
            self.assertFalse((repo_b / ".gemini" / "tmp").exists())

    def test_unwritable_global_root_falls_back_to_project_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked_file = root / "not_a_directory"
            blocked_file.write_text("blocker", encoding="utf-8")

            repo = root / "repo"
            init_git_repo(repo)
            (repo / "app.py").write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")
            git_commit_all(repo, "init")

            env = {"YHARUTO_SESSION_ROOT": str(blocked_file)}
            rc, prep, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--audit"],
                env=env,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(prep["session_scope"], "project")
            sdir = Path(str(prep["session_dir"]))
            self.assertTrue(str(sdir).startswith(str((repo / ".gemini" / "tmp" / "yharuto-code-review").resolve())))

            rc_c, _, _, _ = run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)
            self.assertEqual(rc_c, 0)
            self.assertFalse((repo / ".gemini").exists())

    def test_local_and_audit_modes_isolate_worktree_and_build_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)

            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "index.mjs").write_text("export const v = 1;\n", encoding="utf-8")
            (repo / "build.mjs").write_text(
                "import fs from 'node:fs';\n"
                "fs.mkdirSync('dist', { recursive: true });\n"
                "fs.writeFileSync('dist/bundle.js', 'built');\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps(
                    {
                        "name": "iso-demo",
                        "type": "module",
                        "scripts": {
                            "test": "node --test tests/basic.test.mjs",
                            "build": "node build.mjs",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (repo / "tests" / "basic.test.mjs").write_text(
                "import test from 'node:test';\n"
                "import assert from 'node:assert/strict';\n"
                "import { v } from '../src/index.mjs';\n"
                "test('v', () => { assert.equal(v, 2); });\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "base commit")

            # Uncommitted tracked edit + untracked file in repo
            (repo / "src" / "index.mjs").write_text("export const v = 2;\n", encoding="utf-8")
            (repo / "src" / "untracked_helper.mjs").write_text("export const h = true;\n", encoding="utf-8")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            rc, prep, _, stderr = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--local"],
                env=env,
            )
            self.assertEqual(rc, 0, stderr)
            sdir = Path(str(prep["session_dir"]))
            workdir = Path(str(prep["workdir"]))

            self.assertNotEqual(workdir.resolve(), repo.resolve())
            self.assertEqual(workdir.resolve(), (sdir / "worktree").resolve())
            self.assertIn("v = 2", (workdir / "src" / "index.mjs").read_text(encoding="utf-8"))
            self.assertTrue((workdir / "src" / "untracked_helper.mjs").is_file())

            rc_imp_local, imp_local, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp_local, 0)
            self.assertEqual(imp_local.get("related_tests_and_mocks"), [])
            self.assertEqual(
                imp_local.get("build_hash_configs"),
                [{"file": "build.mjs", "line": 1, "reason": "build_or_bundler_config"}],
            )
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/basic.test.mjs"],
                env=env,
            )
            rc_ci, ci_out, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_ci, 0)
            self.assertEqual(ci_out["overall_status"], "passed")
            # Build artifact created ONLY inside isolated workdir, NEVER in repo!
            self.assertTrue((workdir / "dist" / "bundle.js").is_file())
            self.assertFalse((repo / "dist").exists())

            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)

            # Non-git directory in --audit mode + standalone run_checks --workdir
            plain_dir = root / "plain_audit_dir"
            plain_dir.mkdir()
            (plain_dir / "mod.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
            rc_aud, prep_aud, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(plain_dir), "--audit"],
                env=env,
            )
            self.assertEqual(rc_aud, 0)
            aud_sdir = Path(str(prep_aud["session_dir"]))
            aud_wdir = Path(str(prep_aud["workdir"]))
            self.assertNotEqual(aud_wdir.resolve(), plain_dir.resolve())
            self.assertTrue((aud_wdir / "mod.py").is_file())
            run_script("prepare_worktree.py", ["cleanup", "--session", str(aud_sdir)], env=env)

            # Standalone run_checks.py --workdir must NOT leave any log file in target dir
            before_entries = sorted(p.name for p in plain_dir.iterdir())
            rc_sw, _, _, _ = run_script("run_checks.py", ["--workdir", str(plain_dir)], env=env)
            self.assertEqual(rc_sw, 0)
            after_entries = sorted(p.name for p in plain_dir.iterdir())
            self.assertEqual(before_entries, after_entries)

    def test_validate_rejects_git_working_tree_pollution_and_cleanup_removes_stale_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            sess_root.mkdir()

            # Create a stale >12h session remnant and orphan key
            stale_dir = sess_root / "yharuto-review-oldrepo-deadbeef1234"
            stale_dir.mkdir()
            (stale_dir / "marker.txt").write_text("old", encoding="utf-8")
            keys_dir = sess_root / ".keys"
            keys_dir.mkdir()
            orphan_key = keys_dir / "deadbeef1234.key"
            orphan_key.write_text("00" * 32, encoding="utf-8")
            old_ts = time.time() - (13 * 3600)
            os.utime(stale_dir, (old_ts, old_ts))
            os.utime(orphan_key, (old_ts, old_ts))

            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return amount // 10\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_discount_calc.py").write_text(
                "import unittest\nfrom src.discountCalc import compute_discount\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_basic(self) -> None:\n"
                "        self.assertEqual(compute_discount(100), 10)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return min(amount // 10, 500)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "head")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc, 0)
            sdir = Path(str(prep["session_dir"]))

            rc_imp_range, imp_range, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp_range, 0)
            self.assertEqual(
                imp_range.get("related_tests_and_mocks"),
                [{"file": "tests/test_discount_calc.py", "kind": "test", "reason": "matches_module_stem:discountcalc"}],
            )
            self.assertEqual(imp_range.get("build_hash_configs"), [])
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/test_discount_calc.py"],
                env=env,
            )
            run_script("run_checks.py", ["--session", str(sdir)], env=env)
            run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)

            draft_path = Path(str(prep["draft_findings_path"]))
            draft_path.write_text("[]\n", encoding="utf-8")
            run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
                env=env,
            )
            nonce = extract_challenge_nonce(Path(str(prep["adversarial_input_path"])))
            resp_path = Path(str(prep["adversarial_response_path"]))
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.py", "tests/test_discount_calc.py"],
                        "audit_notes": "割引上限境界と既存テストの整合を確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            run_script(
                "prepare_worktree.py",
                [
                    "record-stage4",
                    "--session",
                    str(sdir),
                    "--verifier-mode",
                    "subagent",
                    "--response-file",
                    str(resp_path),
                ],
                env=env,
            )

            review_md = Path(str(prep["review_output_path"]))
            review_md.write_text(build_clean_full_review(), encoding="utf-8")

            # Pollute repo_dir with an untracked file -> validate MUST reject via gate_errors
            polluted_file = repo / "stray_review_draft.md"
            polluted_file.write_text("oops", encoding="utf-8")

            rc_v1, val_bad, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_md)],
                env=env,
            )
            self.assertEqual(rc_v1, 1)
            self.assertFalse(val_bad["passed"])
            gate_errs = [str(x) for x in val_bad.get("gate_errors", [])] if isinstance(val_bad.get("gate_errors"), list) else []
            self.assertTrue(any("pollution" in e.lower() for e in gate_errs), f"Expected pollution error, got {gate_errs}")

            # Remove stray file -> validate succeeds!
            polluted_file.unlink()
            rc_v2, val_ok, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(review_md)],
                env=env,
            )
            self.assertEqual(rc_v2, 0, json.dumps(val_ok, ensure_ascii=False))
            self.assertTrue(val_ok["passed"])

            # Cleanup must also remove the >12h stale session and orphan key
            rc_c, clean_res, _, _ = run_script(
                "prepare_worktree.py",
                ["cleanup", "--session", str(sdir)],
                env=env,
            )
            self.assertEqual(rc_c, 0)
            self.assertFalse(stale_dir.exists())
            self.assertFalse(orphan_key.exists())
            stale_cleaned = clean_res.get("stale_sessions_cleaned", [])
            self.assertTrue(isinstance(stale_cleaned, list) and len(stale_cleaned) >= 1)


if __name__ == "__main__":
    unittest.main()
