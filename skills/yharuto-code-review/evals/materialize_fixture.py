#!/usr/bin/env python3
"""Materialize a static code-review eval fixture into an isolated Git repository.

Creates under `<output_root>/yharuto-eval-<slug>-<id>/` (defaulting to
`~/.gemini/tmp/yharuto-code-review-evals/` outside any workspace Git tree):
- `<container_dir>/repo`: Git repository with `main` (from `base/`, plus optional
  `base_advance/` commit after branching) and `pr-branch` (from `head/`).
- `<container_dir>/<slug>_pr_meta.json`: Adjacent PR metadata file outside `repo_dir`
  so `git status --porcelain` inside `repo_dir` remains completely clean.

Standard library only (Python 3.9+), `shell=False`, strict typing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

FIXTURE_SLUGS: tuple[str, ...] = (
    "stage1-test-gate-ng",
    "ci-fail-and-recall-slop",
    "precision-five-traps",
    "boundary-and-injection-trap",
    "mock-defensive-and-comment",
)

FIXTURE_ALIASES: dict[str, str] = {
    "1": "stage1-test-gate-ng",
    "eval-1": "stage1-test-gate-ng",
    "eval-1-stage1-test-gate-ng": "stage1-test-gate-ng",
    "2": "ci-fail-and-recall-slop",
    "eval-2": "ci-fail-and-recall-slop",
    "eval-2-ci-fail-and-recall-slop": "ci-fail-and-recall-slop",
    "3": "precision-five-traps",
    "eval-3": "precision-five-traps",
    "eval-3-precision-five-traps": "precision-five-traps",
    "4": "boundary-and-injection-trap",
    "eval-4": "boundary-and-injection-trap",
    "eval-4-boundary-and-injection-trap": "boundary-and-injection-trap",
    "5": "mock-defensive-and-comment",
    "eval-5": "mock-defensive-and-comment",
    "eval-5-mock-defensive-and-comment": "mock-defensive-and-comment",
}

FORBIDDEN_FIXTURE_ARTIFACTS: frozenset[str] = frozenset({".git", "__pycache__", ".DS_Store"})
STALE_MAX_AGE_SECONDS: float = 12.0 * 3600.0
DEFAULT_SUBDIR_NAME = "yharuto-code-review-evals"


class FixtureMaterializeError(RuntimeError):
    """Raised when fixture validation or Git repository construction fails."""


def resolve_fixture_slug(raw_name: str) -> str:
    cleaned = raw_name.strip()
    if cleaned in FIXTURE_SLUGS:
        return cleaned
    if cleaned in FIXTURE_ALIASES:
        return FIXTURE_ALIASES[cleaned]
    raise FixtureMaterializeError(
        f"Unknown fixture '{raw_name}'. Expected one of: {', '.join(FIXTURE_SLUGS)}"
    )


def _ensure_writable_dir(candidate: Path) -> Path | None:
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe_fd, probe_path = tempfile.mkstemp(prefix=".probe-", dir=str(candidate))
        os.close(probe_fd)
        Path(probe_path).unlink(missing_ok=True)
        return candidate.resolve()
    except OSError:
        return None


def resolve_eval_output_root(explicit_root: Path | None) -> Path:
    """Resolve output directory for materialized eval fixtures without dirtying Git."""
    skill_root = Path(__file__).resolve().parent.parent

    if explicit_root is not None:
        resolved = explicit_root.expanduser().resolve()
        try:
            resolved.relative_to(skill_root)
            inside_skill_tree = True
        except ValueError:
            inside_skill_tree = False
        if inside_skill_tree:
            raise FixtureMaterializeError(
                f"Refusing to materialize fixture inside skill tree: {resolved}"
            )
        ensured = _ensure_writable_dir(resolved)
        if ensured is None:
            raise FixtureMaterializeError(f"Output root is not writable: {resolved}")
        return ensured

    env_override = os.environ.get("YHARUTO_EVAL_ROOT", "").strip()
    if env_override:
        env_path = Path(env_override).expanduser().resolve()
        ensured_env = _ensure_writable_dir(env_path)
        if ensured_env is None:
            raise FixtureMaterializeError(
                f"YHARUTO_EVAL_ROOT is not writable: {env_path}"
            )
        return ensured_env

    global_gemini_tmp = Path.home() / ".gemini" / "tmp" / DEFAULT_SUBDIR_NAME
    ensured_global = _ensure_writable_dir(global_gemini_tmp)
    if ensured_global is not None:
        return ensured_global

    fallback_tmp = Path(tempfile.gettempdir()).resolve() / DEFAULT_SUBDIR_NAME
    ensured_fallback = _ensure_writable_dir(fallback_tmp)
    if ensured_fallback is not None:
        return ensured_fallback

    raise FixtureMaterializeError(
        "Unable to create a writable eval output directory in ~/.gemini/tmp or system temp."
    )


def purge_stale_containers(root_base: Path, max_age_sec: float = STALE_MAX_AGE_SECONDS) -> int:
    """Remove stale `yharuto-eval-*` directories older than `max_age_sec`."""
    if not root_base.is_dir():
        return 0
    now = time.time()
    removed = 0
    for entry in root_base.iterdir():
        if not entry.name.startswith("yharuto-eval-") or not entry.is_dir() or entry.is_symlink():
            continue
        try:
            age = now - entry.stat().st_mtime
            if age > max_age_sec:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_container(container_dir: Path) -> dict[str, object]:
    resolved = container_dir.expanduser().resolve()
    if not resolved.name.startswith("yharuto-eval-"):
        raise FixtureMaterializeError(
            f"Refusing to delete directory without 'yharuto-eval-' prefix: {resolved}"
        )
    existed = resolved.exists()
    if existed:
        if resolved.is_symlink() or not resolved.is_dir():
            raise FixtureMaterializeError(f"Invalid container directory: {resolved}")
        shutil.rmtree(resolved)
    return {
        "cleaned": True,
        "existed": existed,
        "container_dir": str(resolved),
    }


def run_git(repo_dir: Path, args: list[str], timeout_sec: int = 30) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise FixtureMaterializeError(
            f"git {' '.join(args)} timed out after {timeout_sec}s in {repo_dir}"
        ) from exc
    except OSError as exc:
        raise FixtureMaterializeError(f"Failed to execute git command: {exc}") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise FixtureMaterializeError(
            f"git {' '.join(args)} failed (exit {completed.returncode}): {stderr}"
        )
    return completed.stdout.strip()


def copy_tree_filtered(src_dir: Path, dst_dir: Path) -> None:
    for entry in sorted(src_dir.iterdir()):
        if entry.name in FORBIDDEN_FIXTURE_ARTIFACTS or entry.suffix == ".pyc":
            continue
        target = dst_dir / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            copy_tree_filtered(entry, target)
        elif entry.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)


def clear_worktree_except_git(repo_dir: Path) -> None:
    for entry in list(repo_dir.iterdir()):
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def assert_fixture_clean(fixture_dir: Path) -> None:
    for artifact in fixture_dir.rglob("*"):
        if artifact.name in FORBIDDEN_FIXTURE_ARTIFACTS or artifact.suffix == ".pyc":
            raise FixtureMaterializeError(
                f"Forbidden artifact ({artifact.name}) found inside static fixture: {artifact}"
            )


def materialize_fixture(slug_input: str, output_root: Path | None = None) -> dict[str, object]:
    slug = resolve_fixture_slug(slug_input)
    evals_dir = Path(__file__).resolve().parent
    fixture_dir = evals_dir / "fixtures" / slug

    if not fixture_dir.is_dir():
        raise FixtureMaterializeError(f"Fixture directory does not exist: {fixture_dir}")

    assert_fixture_clean(fixture_dir)

    base_dir = fixture_dir / "base"
    head_dir = fixture_dir / "head"
    base_advance_dir = fixture_dir / "base_advance"
    pr_meta_src = fixture_dir / "pr_meta.json"

    if not base_dir.is_dir() or not head_dir.is_dir():
        raise FixtureMaterializeError(
            f"Fixture '{slug}' must contain both 'base/' and 'head/' directories"
        )
    if not pr_meta_src.is_file():
        raise FixtureMaterializeError(f"Fixture '{slug}' is missing pr_meta.json")

    try:
        loaded_meta: object = json.loads(pr_meta_src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureMaterializeError(f"Invalid JSON in {pr_meta_src}: {exc}") from exc
    if not isinstance(loaded_meta, dict):
        raise FixtureMaterializeError(f"Expected JSON object in {pr_meta_src}")
    pr_meta_data: dict[str, object] = {str(k): v for k, v in loaded_meta.items()}

    root_base = resolve_eval_output_root(output_root)
    purge_stale_containers(root_base)

    container_dir = Path(tempfile.mkdtemp(prefix=f"yharuto-eval-{slug}-", dir=str(root_base)))
    repo_dir = container_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    run_git(repo_dir, ["init"])
    run_git(repo_dir, ["symbolic-ref", "HEAD", "refs/heads/main"])
    run_git(repo_dir, ["config", "user.name", "Eval Fixture Bot"])
    run_git(repo_dir, ["config", "user.email", "eval-fixture@example.com"])
    run_git(repo_dir, ["config", "commit.gpgsign", "false"])
    run_git(repo_dir, ["config", "tag.gpgsign", "false"])

    # 1. Commit base/ onto main
    copy_tree_filtered(base_dir, repo_dir)
    run_git(repo_dir, ["add", "-A"])
    run_git(repo_dir, ["commit", "-m", "chore: initial base state"])
    initial_base_sha = run_git(repo_dir, ["rev-parse", "HEAD"])

    # 2. Branch pr-branch and commit head/ snapshot
    run_git(repo_dir, ["checkout", "-b", "pr-branch"])
    clear_worktree_except_git(repo_dir)
    copy_tree_filtered(head_dir, repo_dir)
    run_git(repo_dir, ["add", "-A"])
    pr_title = str(pr_meta_data.get("title") or f"feat: changes for {slug}")
    run_git(repo_dir, ["commit", "-m", pr_title])
    head_sha = run_git(repo_dir, ["rev-parse", "HEAD"])

    # 3. Optionally advance main after PR branch split (tests 3-dot merge-base isolation)
    if base_advance_dir.is_dir():
        run_git(repo_dir, ["checkout", "main"])
        copy_tree_filtered(base_advance_dir, repo_dir)
        run_git(repo_dir, ["add", "-A"])
        run_git(
            repo_dir,
            ["commit", "-m", "chore(main): unrelated commit on main after branch split"],
        )
        run_git(repo_dir, ["checkout", "pr-branch"])

    base_sha = run_git(repo_dir, ["rev-parse", "main"])
    merge_base_sha = run_git(repo_dir, ["merge-base", "main", "pr-branch"])
    if merge_base_sha != initial_base_sha:
        raise FixtureMaterializeError(
            f"Unexpected merge-base SHA {merge_base_sha} (expected {initial_base_sha})"
        )

    porcelain = run_git(repo_dir, ["status", "--porcelain=v1", "-uall"])
    if porcelain:
        raise FixtureMaterializeError(
            f"Materialized repository working tree is unexpectedly dirty: {porcelain}"
        )

    # Write adjacent <slug>_pr_meta.json outside repo_dir with explicit OIDs
    pr_meta_out_data: dict[str, object] = {
        **pr_meta_data,
        "base_branch": "main",
        "head_branch": "pr-branch",
        "baseRefName": "main",
        "headRefName": "pr-branch",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "baseRefOid": base_sha,
        "headRefOid": head_sha,
        "merge_base_sha": merge_base_sha,
    }
    pr_meta_path = container_dir / f"{slug}_pr_meta.json"
    pr_meta_path.write_text(
        json.dumps(pr_meta_out_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "repo_dir": str(repo_dir),
        "base_branch": "main",
        "head_branch": "pr-branch",
        "range_spec": "main..pr-branch",
        "pr_meta_path": str(pr_meta_path),
        "fixture": slug,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base_sha": merge_base_sha,
        "container_dir": str(container_dir),
        "output_root": str(root_base),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a static eval fixture into an isolated Git repository "
            "(defaults to ~/.gemini/tmp/yharuto-code-review-evals/)."
        )
    )
    parser.add_argument(
        "--fixture",
        "--eval-id",
        dest="fixture",
        type=str,
        help=f"Fixture slug or eval ID/alias ({', '.join(FIXTURE_SLUGS)})",
    )
    parser.add_argument(
        "--output-root",
        "--dest-root",
        dest="output_root",
        type=Path,
        default=None,
        help=(
            "Optional parent directory for materialized fixtures "
            "(defaults to YHARUTO_EVAL_ROOT or ~/.gemini/tmp/yharuto-code-review-evals/)."
        ),
    )
    parser.add_argument(
        "--cleanup",
        type=Path,
        default=None,
        help="Remove a previously materialized yharuto-eval-* container directory and exit.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print available fixture slugs as JSON and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps({"fixtures": list(FIXTURE_SLUGS)}, ensure_ascii=False, indent=2))
        return 0

    if args.cleanup is not None:
        try:
            cleaned = cleanup_container(args.cleanup)
        except FixtureMaterializeError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))
        return 0

    if not args.fixture:
        parser.error("--fixture is required unless --list or --cleanup is specified")

    try:
        result = materialize_fixture(args.fixture, args.output_root)
    except FixtureMaterializeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
