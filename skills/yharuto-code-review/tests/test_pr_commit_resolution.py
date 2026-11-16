#!/usr/bin/env python3
"""Unit tests for v3 §1: strict --pr commit resolution with zero implicit branch fallback."""

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

from _helpers import git_commit_all, init_git_repo, run_git, run_script  # noqa: E402


def _write_gh_stub(bin_dir: Path, pr_view_payload: dict[str, object]) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    payload_file = bin_dir / "pr_view.json"
    payload_file.write_text(json.dumps(pr_view_payload, ensure_ascii=False), encoding="utf-8")
    gh_script = bin_dir / "gh"
    gh_script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "sys.dont_write_bytecode = True\n"
        "args = sys.argv[1:]\n"
        "if len(args) >= 2 and args[0] == 'pr' and args[1] == 'view':\n"
        f"    sys.stdout.write(pathlib.Path({str(payload_file)!r}).read_text(encoding='utf-8'))\n"
        "    sys.exit(0)\n"
        "sys.stderr.write('unexpected gh call\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    gh_script.chmod(gh_script.stat().st_mode | stat.S_IXUSR)


class TestPrCommitResolution(unittest.TestCase):
    def test_stub_gh_with_nonexistent_head_oid_never_falls_back_to_local_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)

            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            main_sha = git_commit_all(repo, "main commit")

            # Create unrelated local 'pr-branch' and 'feature/other' traps
            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "app.py").write_text("x = 999  # unrelated local pr-branch\n", encoding="utf-8")
            git_commit_all(repo, "unrelated pr-branch")

            run_git(repo, ["checkout", "-b", "feature/other", "main"])
            (repo / "app.py").write_text("x = 777\n", encoding="utf-8")
            git_commit_all(repo, "other branch")

            stub_bin = root / "bin"
            _write_gh_stub(
                stub_bin,
                {
                    "number": 99,
                    "title": "PR 99",
                    "body": "",
                    "baseRefName": "main",
                    "baseRefOid": main_sha,
                    "headRefName": "pr-branch",
                    "headRefOid": "deadbeef" * 5,
                    "isCrossRepository": False,
                    "statusCheckRollup": [],
                },
            )

            env = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rc, out, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "99"],
                env=env,
            )
            self.assertEqual(rc, 2)
            self.assertEqual(out.get("status"), "error")
            self.assertIn("deadbeef", str(out.get("error", "")))

    def test_live_origin_pull_ref_fetch_and_oid_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"

            # Bare origin repository
            origin_bare = root / "origin.git"
            origin_bare.mkdir()
            run_git(origin_bare, ["init", "--bare", "-b", "main"])

            # Producer repo pushes main and refs/pull/42/head to origin
            producer = root / "producer"
            init_git_repo(producer)
            run_git(producer, ["remote", "add", "origin", str(origin_bare)])
            (producer / "service.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
            main_sha = git_commit_all(producer, "main")
            run_git(producer, ["push", "origin", "main"])

            run_git(producer, ["checkout", "-b", "author-topic"])
            (producer / "service.py").write_text("def run() -> int:\n    return 42\n", encoding="utf-8")
            pr42_sha = git_commit_all(producer, "pr 42 commit")
            run_git(producer, ["push", "origin", f"HEAD:refs/pull/42/head"])

            # Client clone ONLY has main + a misleading local 'pr-branch'
            client = root / "client"
            run_git(root, ["clone", str(origin_bare), str(client)])
            run_git(client, ["config", "user.name", "Client"])
            run_git(client, ["config", "user.email", "client@example.com"])
            run_git(client, ["checkout", "-b", "pr-branch"])
            (client / "service.py").write_text("def run() -> int:\n    return -1\n", encoding="utf-8")
            git_commit_all(client, "decoy local pr-branch")
            run_git(client, ["checkout", "main"])

            # Case 1: gh pr view reports exact pr42_sha -> fetches origin pull/42/head and resolves pr42_sha!
            stub_bin = root / "bin_ok"
            _write_gh_stub(
                stub_bin,
                {
                    "number": 42,
                    "title": "PR 42",
                    "body": "",
                    "baseRefName": "main",
                    "baseRefOid": main_sha,
                    "headRefName": "author-topic",
                    "headRefOid": pr42_sha,
                    "isCrossRepository": False,
                    "statusCheckRollup": [],
                },
            )
            env_ok = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rc, prep, _, stderr = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(client), "--pr", "42"],
                env=env_ok,
            )
            self.assertEqual(rc, 0, stderr)
            self.assertEqual(prep["head_sha"], pr42_sha)
            self.assertEqual(prep["base_sha"], main_sha)
            run_script("prepare_worktree.py", ["cleanup", "--session", str(prep["session_dir"])], env=env_ok)

            # Case 2: gh pr view claims a mismatched OID not matching pull/42/head -> MUST exit 2!
            stub_mismatch = root / "bin_mismatch"
            _write_gh_stub(
                stub_mismatch,
                {
                    "number": 42,
                    "title": "PR 42 Mismatched",
                    "body": "",
                    "baseRefName": "main",
                    "baseRefOid": main_sha,
                    "headRefName": "author-topic",
                    "headRefOid": "cafebabe" * 5,
                    "isCrossRepository": False,
                    "statusCheckRollup": [],
                },
            )
            env_bad = {
                "YHARUTO_SESSION_ROOT": str(sess_root),
                "PATH": f"{stub_mismatch}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rc_bad, out_bad, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(client), "--pr", "42"],
                env=env_bad,
            )
            self.assertEqual(rc_bad, 2)
            self.assertEqual(out_bad.get("status"), "error")

    def test_offline_pr_meta_missing_commits_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            repo = root / "repo"
            init_git_repo(repo)
            (repo / "f.py").write_text("a = 1\n", encoding="utf-8")
            git_commit_all(repo, "main")
            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "f.py").write_text("a = 2\n", encoding="utf-8")
            git_commit_all(repo, "pr-branch")

            # Offline meta without head_sha and with missing head_branch must NOT fall back to local pr-branch
            meta_missing_head = root / "meta_no_head.json"
            meta_missing_head.write_text(
                json.dumps({"number": 5, "base_branch": "main", "head_branch": "nonexistent-branch"}),
                encoding="utf-8",
            )
            env = {"YHARUTO_SESSION_ROOT": str(sess_root)}
            rc1, out1, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "5", "--pr-meta", str(meta_missing_head)],
                env=env,
            )
            self.assertEqual(rc1, 2)
            self.assertEqual(out1.get("status"), "error")

            # Offline meta without any base info must NOT fall back to HEAD/main
            meta_missing_base = root / "meta_no_base.json"
            meta_missing_base.write_text(
                json.dumps({"number": 6, "head_branch": "pr-branch"}),
                encoding="utf-8",
            )
            rc2, out2, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--pr", "6", "--pr-meta", str(meta_missing_base)],
                env=env,
            )
            self.assertEqual(rc2, 2)
            self.assertEqual(out2.get("status"), "error")


if __name__ == "__main__":
    unittest.main()
