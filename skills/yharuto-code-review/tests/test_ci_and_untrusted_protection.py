#!/usr/bin/env python3
"""Unit tests for v3 §4 & §5: Stage 2 CI strictness (build, timeout, base_worktree, pending) & 3-layer untrusted code protection."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _helpers import (  # noqa: E402
    FIXTURES_DIR,
    build_clean_full_review,
    extract_challenge_nonce,
    git_commit_all,
    init_git_repo,
    run_git,
    run_script,
)


class TestCiAndUntrustedProtection(unittest.TestCase):
    def test_fixture4_plain_range_blocks_ci_script_hazard_without_pr_meta_or_untrusted_flag(self) -> None:
        """Attack test (#110 §5.3): plain --range main..pr-branch on Fixture 4 must NEVER create PWNED."""
        fixture4 = FIXTURES_DIR / "boundary-and-injection-trap"
        self.assertTrue(fixture4.is_dir(), f"Missing fixture: {fixture4}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "f4_repo"
            init_git_repo(repo)

            # Copy base/ -> commit on main
            for item in (fixture4 / "base").iterdir():
                dst = repo / item.name
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            git_commit_all(repo, "base")

            # Checkout pr-branch, replace with head/ -> commit
            run_git(repo, ["checkout", "-b", "pr-branch"])
            for existing in list(repo.iterdir()):
                if existing.name == ".git":
                    continue
                if existing.is_dir():
                    shutil.rmtree(existing)
                else:
                    existing.unlink()
            for item in (fixture4 / "head").iterdir():
                dst = repo / item.name
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            git_commit_all(repo, "head with malicious package.json test script")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            # Deliberately NO --pr-meta and NO --untrusted!
            rc, prep, _, stderr = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-branch"],
                env=env,
            )
            self.assertEqual(rc, 0, stderr)
            self.assertIs(prep["ci_script_hazard"], True)
            sdir = Path(str(prep["session_dir"]))
            workdir = Path(str(prep["workdir"]))

            # Find a test file in workdir to pass Stage 1
            test_files = sorted(p.relative_to(workdir).as_posix() for p in (workdir / "tests").rglob("*") if p.is_file())
            self.assertTrue(len(test_files) >= 1)

            run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", test_files[0]],
                env=env,
            )
            rc_ci, ci_out, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_ci, 0)
            self.assertEqual(ci_out["execution_policy"], "blocked_untrusted_script")
            self.assertEqual(ci_out["overall_status"], "blocked_untrusted_script")

            checks_by_tool = {
                str(c["tool"]): str(c["status"])
                for c in ci_out.get("checks", [])
                if isinstance(c, dict)
            }
            self.assertEqual(checks_by_tool.get("test"), "blocked_untrusted_script")
            self.assertIn("static_syntax_check", checks_by_tool)

            # Verify PWNED was NEVER created anywhere!
            self.assertFalse((workdir / "PWNED").exists())
            self.assertFalse((repo / "PWNED").exists())

            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)

    def test_untrusted_flag_and_cross_remote_ref_auto_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
            git_commit_all(repo, "main")

            run_git(repo, ["checkout", "-b", "topic"])
            (repo / "src.py").write_text("x = 2\n", encoding="utf-8")
            topic_sha = git_commit_all(repo, "topic")

            # Layer 1: explicit --untrusted flag
            env = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            rc1, prep1, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..topic", "--untrusted"],
                env=env,
            )
            self.assertEqual(rc1, 0)
            self.assertIs(prep1["is_fork_pr"], True)
            self.assertEqual(prep1["untrusted_reason"], "cli_untrusted_flag")
            run_script("prepare_worktree.py", ["cleanup", "--session", str(prep1["session_dir"])], env=env)

            # Layer 2: non-origin remote ref (contributor/topic) -> auto is_fork_pr=True
            run_git(repo, ["remote", "add", "origin", "https://example.com/official/repo.git"])
            run_git(repo, ["remote", "add", "contributor", "https://example.com/forkuser/repo.git"])
            run_git(repo, ["update-ref", "refs/remotes/contributor/topic", topic_sha])

            rc2, prep2, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..contributor/topic"],
                env=env,
            )
            self.assertEqual(rc2, 0)
            self.assertIs(prep2["is_fork_pr"], True)
            self.assertEqual(prep2["untrusted_reason"], "cross_remote_ref")
            run_script("prepare_worktree.py", ["cleanup", "--session", str(prep2["session_dir"])], env=env)

    def test_failed_preexisting_vs_regression_and_approve_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # --- Case A: Test already fails on main (merge_base_sha) -> failed_preexisting -> Approve ALLOWED ---
            repo_pre = root / "repo_pre"
            init_git_repo(repo_pre)
            (repo_pre / "src").mkdir()
            (repo_pre / "tests").mkdir()
            (repo_pre / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return amount // 10\n",
                encoding="utf-8",
            )
            (repo_pre / "tests" / "test_discount_calc.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_preexisting_broken(self) -> None:\n"
                "        self.assertEqual(1, 2)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_pre, "base with preexisting broken test")

            run_git(repo_pre, ["checkout", "-b", "pr-branch"])
            (repo_pre / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return max(0, amount // 10)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_pre, "clean improvement on broken base")

            _, prep_pre, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_pre), "--range", "main..pr-branch"],
                env=env,
            )
            sdir_pre = Path(str(prep_pre["session_dir"]))
            run_script("impact_scan.py", ["--session", str(sdir_pre)], env=env)
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_pre), "--verdict", "OK", "--verified-tests", "tests/test_discount_calc.py"],
                env=env,
            )
            rc_ci_pre, ci_pre, _, _ = run_script("run_checks.py", ["--session", str(sdir_pre)], env=env)
            self.assertEqual(rc_ci_pre, 0)
            self.assertEqual(ci_pre["overall_status"], "failed_preexisting")
            self.assertFalse((sdir_pre / "base_worktree").exists())
            test_chk_pre = next(c for c in ci_pre["checks"] if isinstance(c, dict) and c.get("tool") == "test")
            self.assertEqual(test_chk_pre["status"], "failed_preexisting")
            self.assertEqual(test_chk_pre["failure_origin"], "preexisting")

            # Complete Stage 3/4 and validate Approve -> MUST PASS!
            run_script("scan_diff_smells.py", ["--session", str(sdir_pre)], env=env)
            draft_pre = Path(str(prep_pre["draft_findings_path"]))
            draft_pre.write_text("[]\n", encoding="utf-8")
            run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir_pre), "--draft", str(draft_pre)], env=env)
            nonce_pre = extract_challenge_nonce(Path(str(prep_pre["adversarial_input_path"])))
            resp_pre = Path(str(prep_pre["adversarial_response_path"]))
            resp_pre.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce_pre,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.py", "tests/test_discount_calc.py"],
                        "audit_notes": "既存テスト失敗がmerge_base時点から存在することを確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir_pre), "--verifier-mode", "subagent", "--response-file", str(resp_pre)],
                env=env,
            )
            rev_pre = Path(str(prep_pre["review_output_path"]))
            rev_pre.write_text(build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]"), encoding="utf-8")
            rc_vp, val_pre, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir_pre), "--file", str(rev_pre)],
                env=env,
            )
            self.assertEqual(rc_vp, 0, json.dumps(val_pre, ensure_ascii=False))
            self.assertTrue(val_pre["passed"])
            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_pre)], env=env)

            # --- Case B: Test passes on main and fails on pr-branch -> regression 'failed' -> Approve REJECTED ---
            repo_reg = root / "repo_reg"
            init_git_repo(repo_reg)
            (repo_reg / "src").mkdir()
            (repo_reg / "tests").mkdir()
            (repo_reg / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return 10\n",
                encoding="utf-8",
            )
            (repo_reg / "tests" / "test_discount_calc.py").write_text(
                "import unittest\nfrom src.discountCalc import compute_discount\n"
                "class T(unittest.TestCase):\n"
                "    def test_val(self) -> None:\n"
                "        self.assertEqual(compute_discount(100), 10)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_reg, "green base")

            run_git(repo_reg, ["checkout", "-b", "pr-branch"])
            (repo_reg / "src" / "discountCalc.py").write_text(
                "def compute_discount(amount: int) -> int:\n    return 999\n",
                encoding="utf-8",
            )
            git_commit_all(repo_reg, "regression commit")

            _, prep_reg, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_reg), "--range", "main..pr-branch"],
                env=env,
            )
            sdir_reg = Path(str(prep_reg["session_dir"]))
            run_script("impact_scan.py", ["--session", str(sdir_reg)], env=env)
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir_reg), "--verdict", "OK", "--verified-tests", "tests/test_discount_calc.py"],
                env=env,
            )
            rc_ci_reg, ci_reg, _, _ = run_script("run_checks.py", ["--session", str(sdir_reg)], env=env)
            self.assertEqual(rc_ci_reg, 0)
            self.assertEqual(ci_reg["overall_status"], "failed")
            test_chk_reg = next(c for c in ci_reg["checks"] if isinstance(c, dict) and c.get("tool") == "test")
            self.assertEqual(test_chk_reg["status"], "failed")
            self.assertEqual(test_chk_reg["failure_origin"], "regression")

            run_script("scan_diff_smells.py", ["--session", str(sdir_reg)], env=env)
            draft_reg = Path(str(prep_reg["draft_findings_path"]))
            draft_reg.write_text("[]\n", encoding="utf-8")
            run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir_reg), "--draft", str(draft_reg)], env=env)
            nonce_reg = extract_challenge_nonce(Path(str(prep_reg["adversarial_input_path"])))
            resp_reg = Path(str(prep_reg["adversarial_response_path"]))
            resp_reg.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce_reg,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/discountCalc.py", "tests/test_discount_calc.py"],
                        "audit_notes": "変更により単体テストが回帰失敗していることを確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir_reg), "--verifier-mode", "subagent", "--response-file", str(resp_reg)],
                env=env,
            )
            rev_reg = Path(str(prep_reg["review_output_path"]))
            rev_reg.write_text(build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]"), encoding="utf-8")
            rc_vr, val_reg, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir_reg), "--file", str(rev_reg)],
                env=env,
            )
            self.assertEqual(rc_vr, 1)
            self.assertFalse(val_reg["passed"])
            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_reg)], env=env)

    def test_timeout_status_and_gh_pr_checks_pending_and_live_fallthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "slowMod.py").write_text("def val() -> int:\n    return 1\n", encoding="utf-8")
            (repo / "tests" / "test_slow.py").write_text(
                "import time, unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_hang(self) -> None:\n"
                "        time.sleep(3)\n",
                encoding="utf-8",
            )
            base_sha = git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "src" / "slowMod.py").write_text("def val() -> int:\n    return 2\n", encoding="utf-8")
            head_sha = git_commit_all(repo, "head")

            # 1. Offline pr_meta with IN_PROGRESS check + 1s timeout on local test
            pr_meta_pending = root / "meta_pending.json"
            pr_meta_pending.write_text(
                json.dumps(
                    {
                        "number": 11,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [{"name": "ci/e2e", "status": "IN_PROGRESS", "conclusion": ""}],
                    }
                ),
                encoding="utf-8",
            )
            env = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            _, prep1, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "11", "--pr-meta", str(pr_meta_pending)],
                env=env,
            )
            sdir1 = Path(str(prep1["session_dir"]))
            run_script("impact_scan.py", ["--session", str(sdir1)], env=env)
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir1), "--verdict", "OK", "--verified-tests", "tests/test_slow.py"],
                env=env,
            )
            rc_t, ci_t, _, _ = run_script(
                "run_checks.py",
                ["--session", str(sdir1), "--timeout", "1"],
                env=env,
            )
            self.assertEqual(rc_t, 0)
            by_tool = {str(c["tool"]): str(c["status"]) for c in ci_t["checks"] if isinstance(c, dict)}
            self.assertEqual(by_tool.get("test"), "timed_out")
            self.assertEqual(by_tool.get("gh_pr_checks"), "pending")
            self.assertEqual(ci_t["overall_status"], "timed_out")
            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir1)], env=env)

            # 2. Live gh pr view with empty statusCheckRollup=[] MUST fall through to `gh pr checks 12`!
            (repo / "tests" / "test_slow.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_fast(self) -> None:\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            fast_head = git_commit_all(repo, "fast test")

            stub_bin = root / "gh_bin"
            stub_bin.mkdir()
            view_json = stub_bin / "view12.json"
            view_json.write_text(
                json.dumps(
                    {
                        "number": 12,
                        "title": "PR 12",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-branch",
                        "headRefOid": fast_head,
                        "isCrossRepository": False,
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            gh_stub = stub_bin / "gh"
            gh_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "sys.dont_write_bytecode = True\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['pr', 'view']:\n"
                f"    sys.stdout.write(pathlib.Path({str(view_json)!r}).read_text(encoding='utf-8'))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['pr', 'checks']:\n"
                "    sys.stdout.write('build\\tpending\\t12s\\thttps://example.com/1\\n')\n"
                "    sys.exit(8)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IXUSR)

            env_live = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            _, prep2, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "12"],
                env=env_live,
            )
            sdir2 = Path(str(prep2["session_dir"]))
            run_script("impact_scan.py", ["--session", str(sdir2)], env=env_live)
            run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir2), "--verdict", "OK", "--verified-tests", "tests/test_slow.py"],
                env=env_live,
            )
            rc_l, ci_l, _, _ = run_script("run_checks.py", ["--session", str(sdir2)], env=env_live)
            self.assertEqual(rc_l, 0)
            gh_chk = next(c for c in ci_l["checks"] if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertEqual(gh_chk["cmd"], ["gh", "pr", "checks", "12"])
            self.assertEqual(gh_chk["status"], "pending")
            self.assertEqual(ci_l["overall_status"], "pending")
            run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir2)], env=env_live)


if __name__ == "__main__":
    unittest.main()
