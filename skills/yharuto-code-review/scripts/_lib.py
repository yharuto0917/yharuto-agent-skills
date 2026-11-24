#!/usr/bin/env python3
"""Shared utilities, HMAC state sealing, session root resolution, and Markdown parsers."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Mapping, NoReturn, Sequence

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None  # type: ignore[assignment]

CMD_TIMEOUT_SECONDS = 30
SESSION_PREFIX = "yharuto-review-"
SESSION_SUBPATH = ("yharuto-code-review",)
GEMINI_TMP_PARTS = (".gemini", "tmp", "yharuto-code-review")
STALE_SESSION_MAX_AGE_SECONDS = 12 * 3600

TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:__tests__|__mocks__|tests?|specs?|fixtures?|testing)/"
    r"|(?:\.(?:test|spec)\.[A-Za-z0-9]+$)"
    r"|(?:^|/)test_[^/]+\.py$"
    r"|(?:_test\.(?:py|go|rs)$)"
    r"|(?:^|/)(?:conftest\.py|setupTests\.[A-Za-z0-9]+|vitest\.setup\.[A-Za-z0-9]+|jest\.setup\.[A-Za-z0-9]+)$"
)

FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TABLE_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")

CI_CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        "package.json",
        "Makefile",
        "GNUmakefile",
        "makefile",
        "pyproject.toml",
        "setup.py",
        "build.rs",
        "justfile",
        "Taskfile.yml",
        "Taskfile.yaml",
        ".npmrc",
    }
)

CI_SCRIPT_HAZARD_RE = re.compile(
    r"(?:\bnode\b[^\n]*(?:[\s\"']-(?:e\b|-eval\b)|--input-type\b)"
    r"|\bpython3?\b[^\n]*[\s\"']-c\b"
    r"|\b(?:sh|bash|zsh|dash|ksh)\b[^\n]*[\s\"']-c\b"
    r"|\b(?:curl|wget|nc|ncat|netcat|socat)\s"
    r"|\beval\s*(?:\(|[\"'`$])"
    r"|\b(?:writeFileSync|appendFileSync|child_process|execSync|spawnSync|execFileSync)\b"
    r"|\b(?:os\.system|subprocess\.(?:Popen|run|call|check_output|check_call)|urllib\.request|http\.client|socket\.connect)\b"
    r"|(?:^|\n)\s*(?:script-shell|onload-script|node-options|rustc-wrapper|rustc-workspace-wrapper|runner)\s*="
    r"|\b(?:RUSTC_WRAPPER|LD_PRELOAD|DYLD_INSERT_LIBRARIES)\b"
    r"|(?:/dev/tcp/|base64\s+-d|\|\s*(?:sh|bash)\b))"
)


@dataclass(frozen=True)
class FencedBlock:
    start_line: int
    end_line: int
    lang: str
    body_lines: tuple[str, ...]


@dataclass(frozen=True)
class SectionSlice:
    heading_line: int
    heading_text: str
    body_start_line: int
    body_end_line: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def emit_json(payload: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def fail_with_code(
    message: str,
    code: int = 2,
    extra: Mapping[str, object] | None = None,
) -> NoReturn:
    body: dict[str, object] = {"status": "error", "error": message, "exit_code": code}
    if extra:
        body.update(extra)
    emit_json(body)
    sys.exit(code)


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def read_json_object(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        fail_with_code(f"{label} does not exist: {path}", 2)
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail_with_code(f"Failed to read {label} ({path}): {exc}", 2)
    if not isinstance(raw, dict):
        fail_with_code(f"{label} must be a JSON object: {path}", 2)
    return {str(k): v for k, v in raw.items()}


def _child_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ if base_env is None else base_env)
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    return merged


def normalize_rel_path(rel_path: str) -> str:
    """Normalize path separators to '/' and strip only leading './' prefixes (preserving leading dotfile '.')."""
    norm = rel_path.replace("\\", "/")
    was_abs = norm.startswith("/")
    while norm.startswith("./"):
        norm = norm[2:]
        if not was_abs:
            norm = norm.lstrip("/")
    return norm


def split_nul_paths(raw_stdout: str) -> list[str]:
    """Parse NUL-delimited (`-z`) git path output without losing non-ASCII or space-containing paths."""
    return [item for item in raw_stdout.split("\x00") if item]


def _prepare_exec_argv(cmd: Sequence[str]) -> list[str]:
    argv = list(cmd)
    if argv and argv[0] == "git" and "core.quotepath=false" not in argv[:4]:
        return ["git", "-c", "core.quotepath=false", *argv[1:]]
    return argv


def run_cmd(
    cmd: Sequence[str],
    cwd: Path,
    timeout: int = CMD_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = _prepare_exec_argv(cmd)
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=_child_env(env),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        fail_with_code(f"Required command not found ({cmd[0]}): {exc}", 2)
    except subprocess.TimeoutExpired as exc:
        fail_with_code(f"Command timed out after {timeout}s: {' '.join(cmd)} ({exc})", 2)


def run_subproc(
    cmd: Sequence[str],
    cwd: Path,
    timeout: int = 20,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    argv = _prepare_exec_argv(cmd)
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=_child_env(env),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def build_git_diff_cmd(*diff_args: str) -> list[str]:
    """Build a git diff command immune to diff.noprefix, diff.mnemonicPrefix, color.diff, and diff.external."""
    return ["git", "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", *diff_args]


def is_git_ancestor(repo_dir: Path, ancestor_ref: str, descendant_ref: str) -> bool:
    """Return True when ancestor_ref is an ancestor of (or equal to) descendant_ref."""
    if not ancestor_ref or not descendant_ref:
        return False
    res = run_subproc(["git", "merge-base", "--is-ancestor", ancestor_ref, descendant_ref], cwd=repo_dir, timeout=15)
    return res is not None and res.returncode == 0


def is_git_metadata_rel_path(rel_posix: str) -> bool:
    """Return True when a repo-relative path points to .git metadata (e.g., worktree .git pointer file)."""
    norm = normalize_rel_path(rel_posix)
    if not norm:
        return False
    return any(part == ".git" for part in Path(norm).parts)


def has_surviving_changed_files(state: Mapping[str, object], workdir: Path) -> bool:
    """Return True when at least one file in state['changed_files'] exists as a regular file in workdir."""
    changed_raw = state.get("changed_files")
    if not isinstance(changed_raw, list) or len(changed_raw) == 0:
        return True
    workdir_resolved = workdir.resolve()
    for item in changed_raw:
        if not isinstance(item, str) or not item.strip():
            continue
        norm = normalize_rel_path(item)
        if not norm or is_git_metadata_rel_path(norm):
            continue
        cand = (workdir_resolved / norm).resolve()
        try:
            cand.relative_to(workdir_resolved)
        except ValueError:
            continue
        if cand.is_file():
            return True
    return False


def is_deleted_changed_file_in_base(
    state: Mapping[str, object],
    workdir: Path,
    rel_posix: str,
) -> bool:
    """Return True when rel_posix is a deleted file from changed_files that existed as a blob in the base commit."""
    norm = normalize_rel_path(rel_posix)
    if not norm or is_git_metadata_rel_path(norm):
        return False
    changed_raw = state.get("changed_files")
    if not isinstance(changed_raw, list):
        return False
    changed_set = {normalize_rel_path(str(x)) for x in changed_raw if isinstance(x, str) and x.strip()}
    if norm not in changed_set:
        return False
    workdir_resolved = workdir.resolve()
    raw_cand = workdir_resolved / norm
    cand = raw_cand.resolve()
    try:
        cand.relative_to(workdir_resolved)
    except ValueError:
        return False
    if raw_cand.is_symlink() or cand.exists() or cand.is_symlink():
        return False
    repo_dir_str = str(state.get("repo_dir") or "").strip()
    if not repo_dir_str:
        return False
    repo_dir = Path(repo_dir_str).resolve()
    if not repo_dir.is_dir():
        return False
    base_candidates = [
        str(state.get(k) or "").strip()
        for k in ("review_base_sha", "merge_base_sha", "base_sha")
    ]
    for base_ref in dict.fromkeys(b for b in base_candidates if b):
        res = run_subproc(["git", "cat-file", "-t", f"{base_ref}:{norm}"], cwd=repo_dir, timeout=10)
        if res is not None and res.returncode == 0 and res.stdout.strip() == "blob":
            return True
    return False


def sanitize_slug(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return (cleaned[:32] or "repo").lower()


# ---------------------------------------------------------------------------
# Markdown Fence & Table Parsers
# ---------------------------------------------------------------------------


def parse_fenced_blocks(lines: Sequence[str]) -> tuple[list[FencedBlock], set[int]]:
    blocks: list[FencedBlock] = []
    fenced_lines: set[int] = set()
    in_fence = False
    fence_char = ""
    fence_len = 0
    fence_lang = ""
    fence_start = 0
    current_body: list[str] = []

    for idx_zero, raw_line in enumerate(lines):
        line_no = idx_zero + 1
        if not in_fence:
            m = FENCE_OPEN_RE.match(raw_line)
            if m is not None:
                marker = m.group(2)
                info = m.group(3).strip()
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                fence_lang = info.split()[0].lower() if info else ""
                fence_start = line_no
                current_body = []
                fenced_lines.add(line_no)
        else:
            fenced_lines.add(line_no)
            stripped = raw_line.strip()
            if len(stripped) >= fence_len and all(ch == fence_char for ch in stripped):
                blocks.append(
                    FencedBlock(
                        start_line=fence_start,
                        end_line=line_no,
                        lang=fence_lang,
                        body_lines=tuple(current_body),
                    )
                )
                in_fence = False
            else:
                current_body.append(raw_line)

    if in_fence:
        blocks.append(
            FencedBlock(
                start_line=fence_start,
                end_line=len(lines),
                lang=fence_lang,
                body_lines=tuple(current_body),
            )
        )
    return blocks, fenced_lines


def find_fenced_lines(lines: Sequence[str]) -> set[int]:
    _, fenced = parse_fenced_blocks(lines)
    return fenced


def split_markdown_table_row(line: str) -> list[str]:
    """Split a Markdown table row on unescaped '|' outside inline backtick spans."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]

    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(stripped[i + 1])
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
            i += 1
            continue
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def is_separator_row(cells: Sequence[str], expected_cols: int | None = None) -> bool:
    if not cells:
        return False
    if expected_cols is not None and len(cells) != expected_cols:
        return False
    return all(TABLE_SEP_CELL_RE.match(c.strip()) is not None for c in cells)


def slice_sections_by_prefix(
    lines: Sequence[str],
    fenced_lines: set[int],
    start_line: int,
    end_line: int,
    heading_prefix: str,
) -> list[SectionSlice]:
    found_headings: list[tuple[int, str]] = []
    for ln in range(start_line, end_line + 1):
        if ln in fenced_lines:
            continue
        raw = lines[ln - 1]
        if raw.startswith(heading_prefix) and not raw.startswith(heading_prefix.rstrip() + "#"):
            found_headings.append((ln, raw.rstrip("\r\n")))

    slices: list[SectionSlice] = []
    for idx, (h_line, h_text) in enumerate(found_headings):
        b_end = found_headings[idx + 1][0] - 1 if idx + 1 < len(found_headings) else end_line
        slices.append(
            SectionSlice(
                heading_line=h_line,
                heading_text=h_text,
                body_start_line=h_line + 1,
                body_end_line=b_end,
            )
        )
    return slices


# ---------------------------------------------------------------------------
# Session Root Resolution, Safe Cleanup & Stale Remnant Pruning
# ---------------------------------------------------------------------------


def _probe_writable_dir(target: Path) -> bool:
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / f".probe-{secrets.token_hex(4)}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _prepare_project_session_root(base_repo: Path) -> tuple[Path, list[str]]:
    created_dirs: list[str] = []
    chain = [
        base_repo / ".gemini",
        base_repo / ".gemini" / "tmp",
        base_repo / ".gemini" / "tmp" / "yharuto-code-review",
    ]
    for dpath in chain:
        if not dpath.exists():
            created_dirs.append(str(dpath.resolve()))
    root = chain[-1]
    if not _probe_writable_dir(root):
        fail_with_code(f"Project session root is not writable: {root}", 2)
    return root.resolve(), created_dirs


def resolve_session_root(
    scope: str = "global",
    repo_dir: Path | None = None,
) -> tuple[Path, str, list[str]]:
    """Resolve session root directory.

    Returns (resolved_root_path, effective_scope, newly_created_project_dirs).
    """
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"global", "project"}:
        fail_with_code(f"Invalid --session-scope '{scope}': expected 'global' or 'project'.", 2)

    env_override = os.environ.get("YHARUTO_SESSION_ROOT", "").strip()
    if normalized_scope == "global":
        if env_override:
            override_path = Path(env_override).expanduser()
            if _probe_writable_dir(override_path):
                return override_path.resolve(), "global", []
            if repo_dir is not None:
                proj_root, created = _prepare_project_session_root(repo_dir.resolve())
                return proj_root, "project", created
            fail_with_code(f"YHARUTO_SESSION_ROOT is not writable: {override_path}", 2)

        global_candidate = Path.home() / ".gemini" / "tmp" / "yharuto-code-review"
        if _probe_writable_dir(global_candidate):
            return global_candidate.resolve(), "global", []

        if repo_dir is not None:
            proj_root, created = _prepare_project_session_root(repo_dir.resolve())
            return proj_root, "project", created
        fail_with_code(f"Global session root is not writable and no repo_dir fallback: {global_candidate}", 2)

    target_repo = repo_dir.resolve() if repo_dir is not None else Path.cwd().resolve()
    proj_root, created = _prepare_project_session_root(target_repo)
    return proj_root, "project", created


def is_safe_session_dir(session_dir: Path) -> bool:
    if not session_dir.name.startswith(SESSION_PREFIX):
        return False
    resolved_parent = session_dir.resolve().parent

    # Allow any <dir>/.gemini/tmp/yharuto-code-review parent
    if len(resolved_parent.parts) >= 3 and resolved_parent.parts[-3:] == GEMINI_TMP_PARTS:
        return True

    allowed_roots: set[Path] = {
        (Path.home() / ".gemini" / "tmp" / "yharuto-code-review").resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
    }
    env_override = os.environ.get("YHARUTO_SESSION_ROOT", "").strip()
    if env_override:
        allowed_roots.add(Path(env_override).expanduser().resolve())
    env_tmp = os.environ.get("TMPDIR", "").strip()
    if env_tmp:
        allowed_roots.add(Path(env_tmp).expanduser().resolve())

    return resolved_parent in allowed_roots


def unlink_symlinks_under(root: Path) -> list[str]:
    unlinked: list[str] = []
    if not root.exists() and not root.is_symlink():
        return unlinked
    if root.is_symlink():
        root.unlink()
        return [str(root)]

    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        cur = Path(current_root)
        kept_dirs: list[str] = []
        for dname in dirnames:
            dpath = cur / dname
            if dpath.is_symlink():
                dpath.unlink()
                unlinked.append(str(dpath))
            else:
                kept_dirs.append(dname)
        dirnames[:] = kept_dirs

        for fname in filenames:
            fpath = cur / fname
            if fpath.is_symlink():
                fpath.unlink()
                unlinked.append(str(fpath))
    return unlinked


def prune_created_project_dirs(
    session_root: Path,
    created_project_dirs: Sequence[str] | None = None,
) -> list[str]:
    """Remove empty .keys and empty .gemini/tmp/yharuto-code-review -> .gemini/tmp -> .gemini if empty.

    Preserves any directory that existed prior to session creation (when created_project_dirs is provided)
    or is non-empty.
    """
    pruned: list[str] = []
    keys_dir = session_root / ".keys"
    if keys_dir.is_dir():
        try:
            if not any(keys_dir.iterdir()):
                keys_dir.rmdir()
                pruned.append(str(keys_dir))
        except OSError:
            pass

    if len(session_root.parts) < 3 or session_root.parts[-3:] != GEMINI_TMP_PARTS:
        return pruned

    allowed_set: set[Path] | None = None
    if created_project_dirs is not None:
        allowed_set = {Path(p).resolve() for p in created_project_dirs if p}

    candidates = [
        session_root.resolve(),
        session_root.resolve().parent,
        session_root.resolve().parent.parent,
    ]
    for dpath in candidates:
        if allowed_set is not None and dpath not in allowed_set:
            continue
        if not dpath.is_dir() or dpath.is_symlink():
            continue
        try:
            if not any(dpath.iterdir()):
                dpath.rmdir()
                pruned.append(str(dpath))
            else:
                break
        except OSError:
            break
    return pruned


def cleanup_stale_sessions(
    session_root: Path,
    exclude_session: Path | None = None,
    max_age_seconds: int = STALE_SESSION_MAX_AGE_SECONDS,
) -> list[str]:
    """Remove stale yharuto-review-* session directories and orphan HMAC keys older than max_age_seconds."""
    cleaned_stale: list[str] = []
    if not session_root.is_dir():
        return cleaned_stale

    now = time.time()
    excluded_resolved = exclude_session.resolve() if exclude_session is not None else None

    for entry in sorted(session_root.iterdir()):
        if not entry.name.startswith(SESSION_PREFIX):
            continue
        try:
            resolved_entry = entry.resolve()
            if excluded_resolved is not None and resolved_entry == excluded_resolved:
                continue
            stat_info = entry.lstat()
        except OSError:
            continue

        if (now - stat_info.st_mtime) <= max_age_seconds:
            continue

        state_file = entry / "gate_state.json"
        if state_file.is_file():
            try:
                raw_st: object = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(raw_st, dict):
                    r_dir_str = str(raw_st.get("repo_dir") or "")
                    wt_created = bool(raw_st.get("worktree_created", False))
                    wt_path = entry / "worktree"
                    if wt_created and r_dir_str and Path(r_dir_str).is_dir():
                        run_subproc(["git", "worktree", "remove", "--force", str(wt_path)], cwd=Path(r_dir_str))
                        run_subproc(["git", "worktree", "prune"], cwd=Path(r_dir_str))
            except (OSError, json.JSONDecodeError):
                pass

        unlink_symlinks_under(entry)
        delete_session_hmac_key(entry)
        shutil.rmtree(entry, ignore_errors=True)
        cleaned_stale.append(str(entry))

    keys_dir = session_root / ".keys"
    if keys_dir.is_dir():
        for key_file in sorted(keys_dir.iterdir()):
            if not key_file.name.endswith(".key"):
                continue
            try:
                kstat = key_file.lstat()
                if (now - kstat.st_mtime) > max_age_seconds:
                    key_file.unlink()
            except OSError:
                continue

    return cleaned_stale


# ---------------------------------------------------------------------------
# HMAC-SHA256 State Sealing
# ---------------------------------------------------------------------------


def extract_session_id(session_dir: Path) -> str:
    name = session_dir.name
    if name.startswith(SESSION_PREFIX) and "-" in name[len(SESSION_PREFIX) :]:
        return name.rsplit("-", 1)[-1]
    return name


def session_key_path(session_dir: Path) -> Path:
    resolved = session_dir.resolve()
    sid = extract_session_id(resolved)
    return resolved.parent / ".keys" / f"{sid}.key"


def init_session_hmac_key(session_dir: Path) -> bytes:
    kpath = session_key_path(session_dir)
    keys_dir = kpath.parent
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(keys_dir, 0o700)
    except OSError:
        pass

    secret_hex = secrets.token_hex(32)
    secret_bytes = secret_hex.encode("ascii")
    fd = os.open(str(kpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret_bytes)
    finally:
        os.close(fd)
    try:
        os.chmod(kpath, 0o600)
    except OSError:
        pass
    return secret_bytes


def load_session_hmac_key(session_dir: Path) -> bytes | None:
    kpath = session_key_path(session_dir)
    if not kpath.is_file():
        return None
    try:
        raw = kpath.read_bytes().strip()
        return raw if raw else None
    except OSError:
        return None


def delete_session_hmac_key(session_dir: Path) -> bool:
    kpath = session_key_path(session_dir)
    if not kpath.exists() and not kpath.is_symlink():
        return False
    try:
        kpath.unlink()
        return True
    except OSError:
        return False


def compute_state_hmac(key_bytes: bytes, state: Mapping[str, object]) -> str:
    canonical = {
        str(k): v
        for k, v in state.items()
        if str(k) not in {"state_hmac", "hmac_verified"}
    }
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key_bytes, serialized, hashlib.sha256).hexdigest()


def verify_state_hmac(session_dir: Path, state: Mapping[str, object]) -> tuple[bool, str]:
    key_bytes = load_session_hmac_key(session_dir)
    if key_bytes is None:
        return False, "Session HMAC secret key is missing (session uninitialized or already cleaned)."

    recorded_hmac = state.get("state_hmac")
    if not isinstance(recorded_hmac, str) or not recorded_hmac.strip():
        return False, "gate_state.json is missing state_hmac signature."

    expected_hmac = compute_state_hmac(key_bytes, state)
    if not hmac.compare_digest(recorded_hmac.strip(), expected_hmac):
        return False, "gate_state.json HMAC verification failed (manual tampering detected)."

    recorded_session = str(state.get("session_dir") or "")
    if recorded_session:
        try:
            if Path(recorded_session).resolve() != session_dir.resolve():
                return False, "gate_state.json session_dir mismatch (cross-session replay detected)."
        except OSError:
            return False, "Invalid session_dir recorded in gate_state.json."

    return True, "ok"


_LOCK_REGISTRY_GUARD = threading.Lock()
_PATH_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_HELD_SESSION_LOCK_COUNTS: dict[Path, int] = {}


def _get_path_thread_lock(resolved: Path) -> threading.RLock:
    with _LOCK_REGISTRY_GUARD:
        lock = _PATH_THREAD_LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _PATH_THREAD_LOCKS[resolved] = lock
        return lock


@contextlib.contextmanager
def session_state_lock(session_dir: Path, *, create_dir: bool = True) -> Iterator[None]:
    """Acquire an exclusive, reentrant thread- and process-safe advisory lock for gate_state.json updates."""
    resolved = session_dir.resolve()
    thread_lock = _get_path_thread_lock(resolved)
    with thread_lock:
        held = _HELD_SESSION_LOCK_COUNTS.get(resolved, 0)
        if held > 0:
            _HELD_SESSION_LOCK_COUNTS[resolved] = held + 1
            try:
                yield
            finally:
                remaining = _HELD_SESSION_LOCK_COUNTS.get(resolved, 1) - 1
                if remaining <= 0:
                    _HELD_SESSION_LOCK_COUNTS.pop(resolved, None)
                else:
                    _HELD_SESSION_LOCK_COUNTS[resolved] = remaining
            return

        if create_dir:
            resolved.mkdir(parents=True, exist_ok=True)
        elif not resolved.is_dir():
            yield
            return
        lock_path = resolved / ".gate_state.lock"
        try:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        except FileNotFoundError:
            if not create_dir:
                yield
                return
            raise
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX)
            _HELD_SESSION_LOCK_COUNTS[resolved] = 1
            try:
                yield
            finally:
                remaining = _HELD_SESSION_LOCK_COUNTS.get(resolved, 1) - 1
                if remaining <= 0:
                    _HELD_SESSION_LOCK_COUNTS.pop(resolved, None)
                else:
                    _HELD_SESSION_LOCK_COUNTS[resolved] = remaining
        finally:
            if _fcntl is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def get_state_version(state: Mapping[str, object]) -> int:
    raw = state.get("state_version")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return 0


def write_sealed_gate_state(
    session_dir: Path,
    state: dict[str, object],
    *,
    expected_version: int | None = None,
) -> None:
    with session_state_lock(session_dir):
        key_bytes = load_session_hmac_key(session_dir)
        if key_bytes is None:
            fail_with_code(f"Cannot seal gate_state.json: HMAC key missing for {session_dir}", 2)

        state_path = session_dir / "gate_state.json"
        if expected_version is not None:
            if not state_path.is_file():
                fail_with_code(f"Cannot update gate_state.json: state file missing in {session_dir}", 2)
            current_state = read_verified_gate_state(session_dir)
            current_ver = get_state_version(current_state)
            if current_ver != expected_version:
                fail_with_code(
                    f"Concurrent gate_state.json update detected (expected state_version={expected_version}, "
                    f"current={current_ver}); re-run against the latest session state.",
                    2,
                    extra={
                        "expected_state_version": expected_version,
                        "current_state_version": current_ver,
                    },
                )

        base_ver = expected_version if expected_version is not None else get_state_version(state)
        next_ver = base_ver + 1
        state_copy: dict[str, object] = {
            str(k): v for k, v in state.items() if str(k) not in {"state_hmac", "hmac_verified"}
        }
        state_copy["state_version"] = next_ver
        state_copy["state_hmac"] = compute_state_hmac(key_bytes, state_copy)
        write_json_atomic(state_path, state_copy)
        state["state_version"] = next_ver
        state["state_hmac"] = state_copy["state_hmac"]


def read_verified_gate_state(session_dir: Path) -> dict[str, object]:
    state_path = session_dir / "gate_state.json"
    if not session_dir.is_dir() or not state_path.is_file():
        fail_with_code(f"gate_state.json does not exist: {state_path}", 2)
    with session_state_lock(session_dir, create_dir=False):
        state = read_json_object(state_path, "gate_state.json")
        ok, err_msg = verify_state_hmac(session_dir, state)
        if not ok:
            fail_with_code(err_msg, 2)
        return state


# ---------------------------------------------------------------------------
# Git Cleanliness & CI Script Hazard Detection
# ---------------------------------------------------------------------------


def collect_repo_porcelain(repo_dir: Path, ignore_project_session: bool = False) -> list[str] | None:
    """Return sorted git status --porcelain=v1 -uall lines, or None if not a git work tree."""
    chk = run_subproc(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_dir, timeout=10)
    if chk is None or chk.returncode != 0:
        return None
    res = run_subproc(["git", "status", "--porcelain=v1", "-uall"], cwd=repo_dir, timeout=15)
    if res is None or res.returncode != 0:
        return None

    entries: list[str] = []
    for raw_ln in res.stdout.splitlines():
        if not raw_ln.strip():
            continue
        if ignore_project_session:
            # Porcelain v1 format: XY PATH (or XY ORIG -> PATH)
            rel_part = raw_ln[3:].strip().strip('"')
            if rel_part.startswith(".gemini/tmp/yharuto-code-review/") or rel_part == ".gemini/tmp/yharuto-code-review":
                continue
        entries.append(raw_ln)
    return sorted(set(entries))


def is_ci_config_path(rel_path: str) -> bool:
    norm = normalize_rel_path(rel_path)
    p = Path(norm)
    if p.name in CI_CONFIG_FILENAMES:
        return True
    if p.parts[-2:] in {(".cargo", "config.toml"), (".cargo", "config")}:
        return True
    return False


def detect_ci_script_hazard_in_diff(diff_text: str) -> tuple[bool, list[str]]:
    """Inspect unified diff added lines in CI/build config files for inline execution or exfil payloads."""
    reasons: list[str] = []
    current_file = ""
    in_ci_config = False
    in_header = True

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            in_header = True
            current_file = ""
            in_ci_config = False
            continue
        if in_header:
            plus_path = parse_diff_header_file_path(raw_line, "+++ ")
            if plus_path is not None:
                in_header = False
                if plus_path == "/dev/null":
                    current_file = ""
                    in_ci_config = False
                else:
                    current_file = plus_path
                    in_ci_config = is_ci_config_path(current_file)
                continue
            if raw_line.startswith("@@ "):
                in_header = False
                continue
            continue
        if not in_ci_config:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added = raw_line[1:]
            m = CI_SCRIPT_HAZARD_RE.search(added)
            if m is not None:
                msg = f"{current_file}: hazardous payload '{m.group(0).strip()[:60]}' in added CI config line"
                if msg not in reasons:
                    reasons.append(msg)

    return len(reasons) > 0, reasons


def detect_ci_script_hazard_in_files(workdir: Path) -> tuple[bool, list[str]]:
    """Inspect CI config files directly inside workdir (used for audit/standalone fallback)."""
    reasons: list[str] = []
    pkg_path = workdir / "package.json"
    if pkg_path.is_file():
        try:
            pkg_obj: object = json.loads(pkg_path.read_text(encoding="utf-8"))
            if isinstance(pkg_obj, dict) and isinstance(pkg_obj.get("scripts"), dict):
                for s_name, s_val in pkg_obj["scripts"].items():
                    if isinstance(s_val, str):
                        m = CI_SCRIPT_HAZARD_RE.search(s_val)
                        if m is not None:
                            reasons.append(
                                f"package.json scripts.{s_name}: hazardous payload '{m.group(0).strip()[:60]}'"
                            )
        except (OSError, json.JSONDecodeError):
            pass

    for rel_cand in (
        "Makefile",
        "GNUmakefile",
        "makefile",
        "pyproject.toml",
        "setup.py",
        "build.rs",
        "justfile",
        "Taskfile.yml",
        "Taskfile.yaml",
        ".npmrc",
        ".cargo/config.toml",
        ".cargo/config",
    ):
        cpath = workdir / rel_cand
        if cpath.is_file():
            try:
                txt = cpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = CI_SCRIPT_HAZARD_RE.search(txt)
            if m is not None:
                reasons.append(f"{rel_cand}: hazardous payload '{m.group(0).strip()[:60]}'")

    return len(reasons) > 0, reasons


ALLOWED_STAGE1_SKIP_STATUSES: frozenset[str] = frozenset({"NG", "SKIPPED_BY_FLAG"})
TEST_CHECK_TOOLS: frozenset[str] = frozenset({"test", "python_test"})
BLOCKED_TEST_STATUSES: frozenset[str] = frozenset(
    {"blocked_fork", "blocked_untrusted_script", "blocked_untrusted_code"}
)
GH_TEST_CHECK_WORD_RE = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:tests?|testing|specs?|pytest|unittests?|jest|vitest|mocha|rspec|phpunit|junit|e2e|playwright|cypress|nextest)"
    r"(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
GH_TEST_CHECK_CAMEL_RE = re.compile(r"(?<=[a-z])(?:Tests?|Specs?)(?![a-z])")
GH_OS_RUNNER_TOKEN_RE = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:ubuntu|macos|windows|linux)[-_](?:latest|[0-9]+(?:\.[0-9]+)?(?:-[a-z0-9]+)?)"
    r"(?=[^a-z0-9]|$)",
    re.IGNORECASE,
)
GH_NON_TEST_CHECK_WORD_RE = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:lint(?:er|ing)?|eslint|prettier|format(?:ter|ting)?|fmt|style(?:lint|check)?|markdownlint|shellcheck|hadolint|"
    r"biome|oxlint|typecheck|type-check|tsc|mypy|pyright|ruff|flake8|pylint|black|isort|clippy|vet|staticcheck|"
    r"build|compile|bundle|docker|image|deploy|release|publish|docs?|pages|storybook|chromatic|"
    r"codecov|coveralls|sonar(?:cloud|qube)?|codeql|security|audit|dependabot|renovate|vercel|netlify|cloudflare|"
    r"label(?:er)?|triage|cla|dco|license|attestation|provenance|sbom|check[-_ ]?latest|contest|coderabbit|"
    r"commitlint|changelog|size|benchmark|perf)"
    r"(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
GH_NON_TEST_WORKFLOW_WORD_RE = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:lint(?:er|ing)?|eslint|prettier|format(?:ter|ting)?|style(?:lint|check)?|markdownlint|shellcheck|"
    r"typecheck|type-check|deploy|release|publish|docs?|pages|storybook|chromatic|"
    r"codecov|coveralls|sonar(?:cloud|qube)?|codeql|security|dependabot|renovate|vercel|netlify|cloudflare|"
    r"label(?:er)?|triage|cla|dco|license|attestation|provenance|sbom|coderabbit|commitlint|changelog)"
    r"(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
GH_FAIL_STATUS_TOKENS: frozenset[str] = frozenset(
    {
        "FAIL",
        "FAILED",
        "FAILURE",
        "ERROR",
        "CANCEL",
        "CANCELLED",
        "CANCELED",
        "TIMED_OUT",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "STALE",
    }
)
GH_PENDING_STATUS_TOKENS: frozenset[str] = frozenset(
    {
        "PENDING",
        "IN_PROGRESS",
        "QUEUED",
        "WAITING",
        "REQUESTED",
        "EXPECTED",
    }
)
GH_CONFIRMED_PASS_STATUS_TOKENS: frozenset[str] = frozenset({"PASS", "PASSED", "SUCCESS"})
GH_NEUTRAL_PASS_STATUS_TOKENS: frozenset[str] = frozenset({"NEUTRAL", "SKIPPED", "SKIPPING"})
GH_ALL_STATUS_TOKENS: frozenset[str] = (
    GH_FAIL_STATUS_TOKENS
    | GH_PENDING_STATUS_TOKENS
    | GH_CONFIRMED_PASS_STATUS_TOKENS
    | GH_NEUTRAL_PASS_STATUS_TOKENS
    | frozenset({"COMPLETED"})
)


def reset_stage5_state() -> dict[str, object]:
    """Return a clean unvalidated Stage 5 state dictionary."""
    return {
        "validated": False,
        "review_file": None,
        "review_sha256": None,
        "mode": None,
        "verdict": None,
        "validated_at": None,
    }


def is_test_check_name(check_name: str) -> bool:
    """Return True if a CI check name explicitly names a test suite."""
    cleaned = check_name.strip()
    if not cleaned:
        return False
    return GH_TEST_CHECK_WORD_RE.search(cleaned) is not None or GH_TEST_CHECK_CAMEL_RE.search(cleaned) is not None


def is_non_test_check_name(check_name: str) -> bool:
    """Return True if a CI check name clearly identifies a non-test job (lint/typecheck/build/etc.)."""
    cleaned = check_name.strip()
    if not cleaned or is_test_check_name(cleaned):
        return False
    stripped_runners = GH_OS_RUNNER_TOKEN_RE.sub(" ", cleaned)
    return GH_NON_TEST_CHECK_WORD_RE.search(stripped_runners) is not None


def _find_last_top_level_delim(text: str, delim: str) -> int:
    """Return index of rightmost delim outside parentheses, or -1 if absent."""
    depth = 0
    last_idx = -1
    for idx, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        elif depth == 0 and text.startswith(delim, idx):
            last_idx = idx
    return last_idx


def _split_check_workflow_and_job(name: str, workflow: str = "") -> tuple[str, str]:
    """Split workflow prefix and explicit job name outside parentheses (handling 'wf / job' and 'wf/job')."""
    cleaned_name = name.strip()
    cleaned_wf = workflow.strip()
    if cleaned_name:
        sep_idx = _find_last_top_level_delim(cleaned_name, " / ")
        if sep_idx >= 0:
            wf_prefix = cleaned_name[:sep_idx].strip()
            job_part = cleaned_name[sep_idx + 3 :].strip()
            if job_part:
                if wf_prefix:
                    cleaned_wf = f"{cleaned_wf} / {wf_prefix}" if cleaned_wf else wf_prefix
                cleaned_name = job_part
        elif not cleaned_wf:
            slash_idx = _find_last_top_level_delim(cleaned_name, "/")
            if slash_idx > 0 and slash_idx + 1 < len(cleaned_name):
                wf_prefix = cleaned_name[:slash_idx].strip()
                job_part = cleaned_name[slash_idx + 1 :].strip()
                if wf_prefix and job_part:
                    cleaned_wf = wf_prefix
                    cleaned_name = job_part
    return cleaned_wf, cleaned_name


def is_potential_test_check(name: str, workflow: str = "") -> bool:
    """Return True when a CI check is a test job or a generic CI job that may run tests (fail-closed)."""
    cleaned_wf, cleaned_name = _split_check_workflow_and_job(name, workflow)
    if not cleaned_name and not cleaned_wf:
        return False
    if cleaned_name:
        if is_test_check_name(cleaned_name):
            return True
        if is_non_test_check_name(cleaned_name):
            return False
    if cleaned_wf:
        if is_test_check_name(cleaned_wf):
            return True
        wf_no_runners = GH_OS_RUNNER_TOKEN_RE.sub(" ", cleaned_wf)
        if GH_NON_TEST_WORKFLOW_WORD_RE.search(wf_no_runners) is not None:
            return False
    return True


def classify_gh_status_token(raw_status: str) -> str | None:
    """Classify a concise GitHub check status/conclusion token into 'failed'|'pending'|'passed'|'neutral'|None."""
    upper = raw_status.strip().upper()
    if not upper:
        return None
    normalized = (
        upper.replace("IN PROGRESS", "IN_PROGRESS")
        .replace("TIMED OUT", "TIMED_OUT")
        .replace("ACTION REQUIRED", "ACTION_REQUIRED")
        .replace("STARTUP FAILURE", "STARTUP_FAILURE")
    )
    tokens = [tok for tok in re.split(r"[\s()/,]+", normalized) if tok]
    if not tokens or len(tokens) > 2:
        return None
    if not all(tok in GH_ALL_STATUS_TOKENS for tok in tokens):
        return None
    if any(tok in GH_FAIL_STATUS_TOKENS for tok in tokens):
        return "failed"
    if any(tok in GH_PENDING_STATUS_TOKENS for tok in tokens):
        return "pending"
    if any(tok in GH_CONFIRMED_PASS_STATUS_TOKENS for tok in tokens):
        return "passed"
    if any(tok in GH_NEUTRAL_PASS_STATUS_TOKENS for tok in tokens):
        return "neutral"
    return None


def _is_gh_failed_status_token(raw_status: str) -> bool:
    return classify_gh_status_token(raw_status) == "failed"


def parse_gh_pr_check_rows(lines: Sequence[str], *, tsv_only: bool = False) -> list[dict[str, str]]:
    """Parse structured check rows from `gh pr checks` TSV output or (unless tsv_only) `name: STATUS` summary lines."""
    rows: list[dict[str, str]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) >= 2 and cols[0]:
                cls = classify_gh_status_token(cols[1])
                if cls is not None:
                    rows.append({"name": cols[0], "workflow": "", "status": cls})
            continue
        if not tsv_only and ": " in line:
            name_part, status_part = line.rsplit(": ", 1)
            cls = classify_gh_status_token(status_part)
            if name_part.strip() and cls is not None:
                rows.append({"name": name_part.strip(), "workflow": "", "status": cls})
    return rows


def extract_failed_gh_check_names(lines: Sequence[str]) -> list[str]:
    """Extract failing check names from `gh pr checks` TSV lines or `name: STATUS` summary lines."""
    return [row["name"] for row in parse_gh_pr_check_rows(lines) if row["status"] == "failed"]


def _extract_gh_check_entries(chk: Mapping[str, object]) -> list[dict[str, str]]:
    """Extract normalized {name, workflow, status} entries from a gh_pr_checks record."""
    raw_entries = chk.get("check_entries")
    if isinstance(raw_entries, list) and raw_entries:
        parsed: list[dict[str, str]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            workflow = str(item.get("workflow") or "").strip()
            status = str(item.get("status") or "").strip().lower()
            if name and status in {"passed", "failed", "pending", "neutral"}:
                parsed.append({"name": name, "workflow": workflow, "status": status})
        if parsed:
            return parsed

    entries: list[dict[str, str]] = []
    seen_failed: set[str] = set()
    raw_failed = chk.get("failed_checks")
    if isinstance(raw_failed, list):
        for name_obj in raw_failed:
            if isinstance(name_obj, str) and name_obj.strip():
                nm = name_obj.strip()
                seen_failed.add(nm)
                entries.append({"name": nm, "workflow": "", "status": "failed"})

    raw_pending = chk.get("pending_checks")
    seen_pending: set[str] = set()
    if isinstance(raw_pending, list):
        for name_obj in raw_pending:
            if isinstance(name_obj, str) and name_obj.strip():
                nm = name_obj.strip()
                seen_pending.add(nm)
                entries.append({"name": nm, "workflow": "", "status": "pending"})

    raw_passed = chk.get("passed_checks")
    seen_passed: set[str] = set()
    if isinstance(raw_passed, list):
        for name_obj in raw_passed:
            if isinstance(name_obj, str) and name_obj.strip():
                nm = name_obj.strip()
                seen_passed.add(nm)
                entries.append({"name": nm, "workflow": "", "status": "passed"})

    summary_lines = chk.get("summary_lines")
    if isinstance(summary_lines, list):
        str_lines = [ln for ln in summary_lines if isinstance(ln, str)]
        for row in parse_gh_pr_check_rows(str_lines):
            nm = row["name"]
            st = row["status"]
            if st == "failed" and (isinstance(raw_failed, list) or nm in seen_failed):
                continue
            if st == "pending" and (isinstance(raw_pending, list) or nm in seen_pending):
                continue
            if st == "passed" and (isinstance(raw_passed, list) or nm in seen_passed):
                continue
            entries.append(row)
    return entries


def find_stage2_test_failures(checks: object) -> list[str]:
    """Return tool labels for Stage 2 checks that represent failing tests (status == 'failed')."""
    if not isinstance(checks, list):
        return []
    failed_tools: list[str] = []
    for chk in checks:
        if not isinstance(chk, dict) or chk.get("status") != "failed":
            continue
        tool = str(chk.get("tool") or "").strip()
        if tool in TEST_CHECK_TOOLS:
            failed_tools.append(tool)
            continue
        if tool == "gh_pr_checks":
            entries = _extract_gh_check_entries(chk)
            failed_entries = [e for e in entries if e["status"] == "failed"]
            if failed_entries:
                if any(is_potential_test_check(e["name"], e["workflow"]) for e in failed_entries):
                    failed_tools.append(tool)
            else:
                # Failed gh_pr_checks with unparseable job names cannot prove tests passed -> fail-closed
                failed_tools.append(tool)
    return failed_tools


def has_stage2_test_failure(state: Mapping[str, object]) -> bool:
    """Return True when recorded Stage 2 checks contain a regression test failure."""
    stage2 = state.get("stage2")
    if not isinstance(stage2, dict):
        return False
    return len(find_stage2_test_failures(stage2.get("checks"))) > 0


def evaluate_skip_test_stage2_status(state: Mapping[str, object]) -> tuple[str, str]:
    """Evaluate --skip-test readiness against Stage 2 results.

    Returns (status, reason) where status is one of:
    - 'not_applicable': session is not using an active Stage 1 --skip-test bypass
    - 'revoked': Stage 2 test failure permanently revoked --skip-test
    - 'stage2_not_executed': Stage 2 has not run yet
    - 'confirmed': required tests passed with no failing, pending, or unverified test checks
    - 'pending': required remote/CI test checks are still running (bypass on hold, not revoked)
    - 'unverified': no passing test result was confirmed (timed_out, env_unavailable, etc.)
    """
    stage1 = state.get("stage1")
    s1_status = stage1.get("status") if isinstance(stage1, dict) else None
    if s1_status in {"OK", "EXEMPT_NO_TESTABLE_CODE"}:
        return "not_applicable", ""
    if not bool(state.get("skip_test", False)) or s1_status not in ALLOWED_STAGE1_SKIP_STATUSES:
        return "not_applicable", ""

    if bool(state.get("skip_test_revoked", False)) or has_stage2_test_failure(state):
        reason = str(state.get("skip_test_revoked_reason") or "").strip()
        if not reason:
            stage2_rev = state.get("stage2")
            failed = find_stage2_test_failures(
                stage2_rev.get("checks") if isinstance(stage2_rev, dict) else None
            )
            tools_csv = ", ".join(failed) if failed else "test"
            reason = f"Stage 2 test check(s) failed ({tools_csv}); --skip-test cannot bypass failing tests."
        return "revoked", reason

    stage2 = state.get("stage2")
    if not isinstance(stage2, dict) or stage2.get("executed") is not True:
        return "stage2_not_executed", ""

    checks_raw = stage2.get("checks")
    checks = [c for c in checks_raw if isinstance(c, dict)] if isinstance(checks_raw, list) else []

    local_tests = [c for c in checks if str(c.get("tool") or "").strip() in TEST_CHECK_TOOLS]
    gh_checks = [c for c in checks if str(c.get("tool") or "").strip() == "gh_pr_checks"]

    for gh_chk in gh_checks:
        if gh_chk.get("head_sha_mismatch") is True:
            expected_sha = str(gh_chk.get("expected_head_sha") or "")[:12]
            live_sha = str(gh_chk.get("live_head_sha") or "")[:12]
            return (
                "unverified",
                f"Live PR head commit changed ({expected_sha} -> {live_sha}); "
                "re-run prepare_worktree.py for the new head before continuing.",
            )

    gh_test_pending = False
    gh_test_passed = False
    for gh_chk in gh_checks:
        gh_st = str(gh_chk.get("status") or "").strip()
        entries = _extract_gh_check_entries(gh_chk)
        if entries:
            if any(e["status"] == "pending" and is_potential_test_check(e["name"], e["workflow"]) for e in entries):
                gh_test_pending = True
            # Fail-closed confirmation: require an explicit test check name/workflow to have passed,
            # while rejecting known non-test jobs (e.g. eslint) even inside a Tests workflow.
            for e in entries:
                if e["status"] != "passed":
                    continue
                wf, nm = _split_check_workflow_and_job(e["name"], e["workflow"])
                if is_test_check_name(nm) or (not is_non_test_check_name(nm) and is_test_check_name(wf)):
                    gh_test_passed = True
                    break
        elif gh_st == "pending":
            gh_test_pending = True

    if gh_test_pending:
        return (
            "pending",
            "Stage 2 remote test checks are still pending/in-progress; --skip-test bypass is on hold "
            "(not revoked) until required tests finish passing.",
        )

    all_local_passed = len(local_tests) > 0 and all(c.get("status") == "passed" for c in local_tests)
    if all_local_passed:
        return "confirmed", ""

    local_inconclusive = [
        c
        for c in local_tests
        if str(c.get("status") or "").strip() not in ({"passed", "not_configured"} | BLOCKED_TEST_STATUSES)
    ]
    if gh_test_passed and not local_inconclusive:
        return "confirmed", ""

    local_statuses = [f"{c.get('tool')}={c.get('status')}" for c in local_tests]
    gh_statuses = [f"gh_pr_checks={c.get('status')}" for c in gh_checks]
    detail = ", ".join(local_statuses + gh_statuses) or "no test checks recorded"
    return (
        "unverified",
        f"Stage 2 did not confirm a passing test run ({detail}); --skip-test requires a confirmed passing test.",
    )


def is_stage1_passed_or_skipped(
    state: Mapping[str, object],
    *,
    require_stage2_test_pass: bool = False,
) -> bool:
    """Return True when Stage 1 passed normally or is allowed to continue via --skip-test."""
    stage1 = state.get("stage1")
    s1_status = stage1.get("status") if isinstance(stage1, dict) else None
    if s1_status in {"OK", "EXEMPT_NO_TESTABLE_CODE"}:
        return True
    if bool(state.get("skip_test", False)) and s1_status in ALLOWED_STAGE1_SKIP_STATUSES:
        bypass_status, _ = evaluate_skip_test_stage2_status(state)
        if bypass_status == "revoked":
            return False
        if require_stage2_test_pass:
            return bypass_status == "confirmed"
        return True
    return False


# ---------------------------------------------------------------------------
# Re-review Scope Filtering & Cross-Session Review History
# ---------------------------------------------------------------------------

MAX_HISTORY_ENTRIES = 30
_C_ESCAPE_BYTES: Mapping[str, int] = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    "\\": 0x5C,
    '"': 0x22,
}


def normalize_only_files(raw_entries: Sequence[str] | None) -> list[str]:
    """Normalize repeatable/comma-separated --only-files entries into unique repo-relative POSIX paths."""
    if raw_entries is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_entries:
        for part in str(raw_item).split(","):
            cleaned = part.strip()
            if not cleaned:
                continue
            raw_norm = normalize_rel_path(cleaned)
            p = Path(raw_norm)
            if not raw_norm or p.is_absolute() or ".." in p.parts:
                fail_with_code(
                    f"Invalid --only-files path '{cleaned}': must be a repository-relative path without '..'.",
                    2,
                )
            canonical = normalize_rel_path(p.as_posix()).rstrip("/")
            if not canonical or canonical == ".":
                fail_with_code(
                    f"Invalid --only-files path '{cleaned}': must be a repository-relative path without '..'.",
                    2,
                )
            if canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
    if not result:
        fail_with_code(
            "Empty --only-files value: provide at least one repository-relative file path.",
            2,
        )
    return result


def _unquote_git_path(raw: str) -> str:
    """Decode a Git C-quoted path (if wrapped in double quotes) into UTF-8 text."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
        buf = bytearray()
        i = 0
        n = len(inner)
        while i < n:
            ch = inner[i]
            if ch == "\\" and i + 1 < n:
                nxt = inner[i + 1]
                if nxt in _C_ESCAPE_BYTES:
                    buf.append(_C_ESCAPE_BYTES[nxt])
                    i += 2
                    continue
                if nxt in "01234567" and i + 3 < n and all(c in "01234567" for c in inner[i + 1 : i + 4]):
                    buf.append(int(inner[i + 1 : i + 4], 8) & 0xFF)
                    i += 4
                    continue
                buf.extend(nxt.encode("utf-8"))
                i += 2
                continue
            buf.extend(ch.encode("utf-8"))
            i += 1
        return buf.decode("utf-8", errors="replace")
    return raw


def parse_diff_header_file_path(raw_line: str, prefix: str) -> str | None:
    """Parse a unified diff file header line ('+++ ', '--- ', 'rename to ', 'rename from ')."""
    if not raw_line.startswith(prefix):
        return None
    payload = raw_line[len(prefix) :].rstrip("\r\n")
    if not payload:
        return None
    if payload == "/dev/null":
        return "/dev/null"
    if payload.startswith('"'):
        # Extract up to closing unescaped double quote (ignoring optional trailing tab/timestamp)
        i = 1
        n = len(payload)
        while i < n:
            if payload[i] == "\\" and i + 1 < n:
                i += 2
                continue
            if payload[i] == '"':
                payload = payload[: i + 1]
                break
            i += 1
        decoded = _unquote_git_path(payload)
    else:
        if "\t" in payload:
            payload = payload.split("\t", 1)[0]
        decoded = payload
    if prefix == "+++ ":
        if decoded.startswith("b/"):
            decoded = decoded[2:]
        else:
            return None
    elif prefix == "--- ":
        if decoded.startswith("a/"):
            decoded = decoded[2:]
        else:
            return None
    return normalize_rel_path(decoded)


def _consume_git_quoted_token(text: str, start: int) -> tuple[str, int]:
    i = start + 1
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if text[i] == '"':
            return _unquote_git_path(text[start : i + 1]), i + 1
        i += 1
    return _unquote_git_path(text[start:]), n


def _extract_git_diff_header_paths(diff_git_line: str) -> tuple[str, str]:
    rest = diff_git_line[len("diff --git ") :].rstrip("\r\n")
    if not rest:
        return "", ""

    def _strip_ab(val: str, pref: str) -> str:
        return normalize_rel_path(val[len(pref) :] if val.startswith(pref) else val)

    # Case 1: first token is C-quoted
    if rest.startswith('"'):
        tok1, idx = _consume_git_quoted_token(rest, 0)
        rem = rest[idx:].lstrip(" ")
        if rem.startswith('"'):
            tok2, _ = _consume_git_quoted_token(rem, 0)
        else:
            tok2 = rem
        return _strip_ab(tok1, "a/"), _strip_ab(tok2, "b/")

    # Case 2: first token unquoted, second token C-quoted (e.g. `a/plain.py "b/tab\t.py"`)
    q_marker = ' "b/'
    q_idx = rest.find(q_marker)
    if rest.startswith("a/") and q_idx > 2 and rest.endswith('"'):
        tok1 = rest[:q_idx]
        tok2, _ = _consume_git_quoted_token(rest, q_idx + 1)
        return _strip_ab(tok1, "a/"), _strip_ab(tok2, "b/")

    # Case 3: both unquoted — exact midpoint match handles paths containing ' b/' when old == new
    if rest.startswith("a/") and (len(rest) - 1) % 2 == 0:
        mid = (len(rest) - 1) // 2
        if rest[mid] == " " and rest[mid + 1 :].startswith("b/") and rest[2:mid] == rest[mid + 3 :]:
            path_val = normalize_rel_path(rest[2:mid])
            return path_val, path_val

    marker = " b/"
    idx = rest.rfind(marker)
    if rest.startswith("a/") and idx > 2:
        return normalize_rel_path(rest[2:idx]), normalize_rel_path(rest[idx + 3 :])
    return "", ""


def filter_diff_by_files(diff_text: str, allowed_files: set[str]) -> str:
    """Filter unified diff to files in allowed_files (matching git diff --name-only semantics)."""
    if not diff_text or not allowed_files:
        return ""
    norm_allowed = {normalize_rel_path(f) for f in allowed_files if f}
    blocks: list[list[str]] = []
    current: list[str] = []
    in_git_block = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            if current:
                blocks.append(current)
            current = [raw_line]
            in_git_block = True
        elif not in_git_block and parse_diff_header_file_path(raw_line, "+++ ") is not None:
            if current:
                blocks.append(current)
            current = [raw_line]
        else:
            if current:
                current.append(raw_line)
    if current:
        blocks.append(current)

    kept: list[str] = []
    for blk in blocks:
        old_h = ""
        new_h = ""
        if blk[0].startswith("diff --git "):
            old_h, new_h = _extract_git_diff_header_paths(blk[0])

        plus_path: str | None = None
        minus_path: str | None = None
        rename_to: str | None = None
        for ln in blk:
            # Stop header parsing as soon as the first hunk begins so diff body lines never forge headers
            if ln.startswith("@@ "):
                break
            if plus_path is None:
                p_cand = parse_diff_header_file_path(ln, "+++ ")
                if p_cand is not None:
                    plus_path = p_cand
                    continue
            if minus_path is None:
                m_cand = parse_diff_header_file_path(ln, "--- ")
                if m_cand is not None:
                    minus_path = m_cand
                    continue
            if rename_to is None:
                r_cand = parse_diff_header_file_path(ln, "rename to ")
                if r_cand is not None:
                    rename_to = r_cand

        if plus_path == "/dev/null":
            canonical_path = (minus_path if minus_path and minus_path != "/dev/null" else "") or old_h
        else:
            canonical_path = plus_path or rename_to or new_h

        if canonical_path and canonical_path in norm_allowed:
            kept.append("\n".join(blk))
    return "\n".join(kept) + ("\n" if kept else "")


def get_current_git_branch(repo_dir: Path) -> str:
    """Return current checked-out git branch name, or empty string when detached / non-git."""
    res = run_subproc(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_dir, timeout=10)
    if res is not None and res.returncode == 0:
        return res.stdout.strip()
    return ""


def resolve_history_dir(
    session_root: Path,
    effective_scope: str,
    *,
    create_if_missing: bool = False,
) -> Path | None:
    """Resolve persistent history directory outside the target repository."""
    if effective_scope == "global":
        cand_dir = session_root.resolve() / ".history"
        return cand_dir if (create_if_missing or cand_dir.is_dir()) else None

    env_override = os.environ.get("YHARUTO_SESSION_ROOT", "").strip()
    if env_override:
        base_cand = Path(env_override).expanduser()
    else:
        base_cand = Path.home() / ".gemini" / "tmp" / "yharuto-code-review"

    if not create_if_missing:
        hist_cand = base_cand.resolve() / ".history"
        return hist_cand if hist_cand.is_dir() else None

    if _probe_writable_dir(base_cand):
        return base_cand.resolve() / ".history"
    return None


def _history_repo_key(repo_dir: Path) -> str:
    return hashlib.sha256(str(repo_dir.resolve()).encode("utf-8")).hexdigest()[:16]


def _read_history_file_status(path: Path) -> tuple[list[dict[str, object]], bool]:
    """Read history entries from path and report (entries, is_corrupt)."""
    if not path.is_file():
        return [], False
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], True
    if not isinstance(raw, dict):
        return [], True
    entries_raw = raw.get("entries")
    if isinstance(entries_raw, list):
        return [{str(k): v for k, v in item.items()} for item in entries_raw if isinstance(item, dict)], False
    return [{str(k): v for k, v in raw.items()}], False


def _read_history_file_entries(path: Path) -> list[dict[str, object]]:
    entries, _ = _read_history_file_status(path)
    return entries


def should_advance_review_history(
    session_dir: Path,
    state: Mapping[str, object],
) -> bool:
    """Return True only when the reviewed scope continuously covers from merge_base or prior saved head."""
    mode = str(state.get("mode") or "")
    if mode not in {"pr", "range", "local"}:
        return False

    only_files_raw = state.get("only_files")
    if isinstance(only_files_raw, list) and len(only_files_raw) > 0:
        return False

    is_rereview = bool(state.get("is_rereview", False))
    if not is_rereview:
        return True

    repo_dir_str = str(state.get("repo_dir") or "").strip()
    if not repo_dir_str:
        return False
    repo_dir = Path(repo_dir_str).resolve()
    session_root = session_dir.resolve().parent
    effective_scope = str(state.get("session_scope") or "global")
    merge_base_sha = str(state.get("merge_base_sha") or "").strip()
    review_base_sha = str(state.get("review_base_sha") or "").strip()

    if mode in {"pr", "range"}:
        if not review_base_sha or (merge_base_sha and review_base_sha == merge_base_sha):
            return True
        pr_raw = state.get("pr_number")
        pr_number = pr_raw if isinstance(pr_raw, int) and not isinstance(pr_raw, bool) else None
        candidates, exact_pr_match = load_review_history_candidates(
            session_root=session_root,
            effective_scope=effective_scope,
            repo_dir=repo_dir,
            pr_number=pr_number if mode == "pr" else None,
        )
        if mode == "pr":
            if not (exact_pr_match and candidates):
                return False
            prev_entry = candidates[0]
            prev_head = str(prev_entry.get("head_sha") or "").strip()
            prev_mb = str(prev_entry.get("merge_base_sha") or "").strip()
            if not prev_head or (prev_mb and merge_base_sha and prev_mb != merge_base_sha):
                return False
            return review_base_sha == prev_head or is_git_ancestor(repo_dir, review_base_sha, prev_head)

        for entry in candidates:
            commits_rev = entry.get("commits_reviewed")
            is_commit_history = (
                commits_rev is True
                if isinstance(commits_rev, bool)
                else str(entry.get("mode") or "") in {"pr", "range"}
            )
            if not is_commit_history:
                continue
            prev_head = str(entry.get("head_sha") or "").strip()
            prev_mb = str(entry.get("merge_base_sha") or "").strip()
            if not prev_head or (prev_mb and merge_base_sha and prev_mb != merge_base_sha):
                continue
            if review_base_sha == prev_head or is_git_ancestor(repo_dir, review_base_sha, prev_head):
                return True
        return False

    # mode == "local"
    if not review_base_sha:
        return True
    history_dir = resolve_history_dir(session_root, effective_scope, create_if_missing=False)
    if history_dir is not None and history_dir.is_dir():
        repo_key = _history_repo_key(repo_dir)
        for hist_path in (history_dir / f"{repo_key}-runs.json", history_dir / f"{repo_key}-local.json"):
            _, is_corrupt = _read_history_file_status(hist_path)
            if is_corrupt:
                return False
    current_branch = get_current_git_branch(repo_dir)
    candidates, _ = load_review_history_candidates(
        session_root=session_root,
        effective_scope=effective_scope,
        repo_dir=repo_dir,
        pr_number=None,
    )
    matching_prev_heads: list[str] = []
    for entry in candidates:
        entry_pr = entry.get("pr_number")
        entry_branch = str(entry.get("branch") or "").strip()
        if entry_pr is not None:
            if not current_branch or entry_branch != current_branch:
                continue
        elif current_branch and entry_branch and entry_branch != current_branch:
            continue
        prev_head = str(entry.get("head_sha") or "").strip()
        if prev_head:
            matching_prev_heads.append(prev_head)
    if not matching_prev_heads:
        return True
    latest_prev_head = matching_prev_heads[0]
    return review_base_sha == latest_prev_head or is_git_ancestor(repo_dir, review_base_sha, latest_prev_head)


def save_review_history(
    session_dir: Path,
    state: Mapping[str, object],
    expected_mode: str,
    verdict: str | None,
) -> Path | None:
    """Persist lightweight review metadata outside the target repo for future --rereview runs."""
    if not should_advance_review_history(session_dir, state):
        return None
    repo_dir_str = str(state.get("repo_dir") or "").strip()
    if not repo_dir_str:
        return None
    repo_dir = Path(repo_dir_str).resolve()
    session_root = session_dir.resolve().parent
    effective_scope = str(state.get("session_scope") or "global")
    history_dir = resolve_history_dir(session_root, effective_scope, create_if_missing=True)
    if history_dir is None:
        return None

    mode = str(state.get("mode") or "")
    if mode not in {"pr", "range", "local"}:
        return None

    pr_raw = state.get("pr_number")
    pr_number = pr_raw if isinstance(pr_raw, int) and not isinstance(pr_raw, bool) else None
    repo_key = _history_repo_key(repo_dir)
    ch_raw = state.get("changed_files")
    changed_files = [str(x) for x in ch_raw] if isinstance(ch_raw, list) else []
    head_sha = str(state.get("head_sha") or "")
    review_base_sha = str(state.get("review_base_sha") or "")
    branch_name = ""
    if mode == "local":
        branch_name = get_current_git_branch(repo_dir)
    elif head_sha:
        head_res = run_subproc(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo_dir, timeout=10)
        if head_res is not None and head_res.returncode == 0 and head_res.stdout.strip() == head_sha:
            branch_name = get_current_git_branch(repo_dir)

    entry: dict[str, object] = {
        "schema_version": 2,
        "repo_dir": str(repo_dir),
        "mode": mode,
        "commits_reviewed": mode in {"pr", "range"},
        "branch": branch_name,
        "pr_number": pr_number,
        "base_sha": str(state.get("base_sha") or ""),
        "merge_base_sha": str(state.get("merge_base_sha") or ""),
        "review_base_sha": review_base_sha,
        "head_sha": head_sha,
        "changed_files": changed_files,
        "verdict": verdict,
        "review_mode": expected_mode,
        "reviewed_at": utc_now_iso(),
    }

    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(history_dir, 0o700)
        except OSError:
            pass

        if pr_number is not None:
            dest = history_dir / f"{repo_key}-pr-{pr_number}.json"
            write_json_atomic(dest, entry)
        else:
            dest = history_dir / f"{repo_key}-runs.json"
            existing = _read_history_file_entries(dest)
            legacy_path = history_dir / f"{repo_key}-local.json"
            if not existing and legacy_path.is_file():
                existing = _read_history_file_entries(legacy_path)
            dedup_key = (
                mode,
                branch_name,
                str(entry["merge_base_sha"]),
                str(entry["head_sha"]),
            )
            filtered = [
                item
                for item in existing
                if (
                    str(item.get("mode") or ""),
                    str(item.get("branch") or ""),
                    str(item.get("merge_base_sha") or ""),
                    str(item.get("head_sha") or ""),
                )
                != dedup_key
            ]
            entries_out = [entry, *filtered][:MAX_HISTORY_ENTRIES]
            write_json_atomic(dest, {"schema_version": 2, "entries": entries_out})
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        return dest
    except OSError:
        return None


def load_review_history_candidates(
    session_root: Path,
    effective_scope: str,
    repo_dir: Path,
    pr_number: int | None,
) -> tuple[list[dict[str, object]], bool]:
    """Load review history candidates sorted newest-first without creating directories.

    Returns (candidates_newest_first, exact_pr_match).
    """
    history_dir = resolve_history_dir(session_root, effective_scope, create_if_missing=False)
    if history_dir is None or not history_dir.is_dir():
        return [], False

    repo_key = _history_repo_key(repo_dir)
    if pr_number is not None:
        exact_entries = _read_history_file_entries(history_dir / f"{repo_key}-pr-{pr_number}.json")
        return (exact_entries, True) if exact_entries else ([], False)

    candidates: list[dict[str, object]] = []
    for path in sorted(history_dir.glob(f"{repo_key}-*.json")):
        candidates.extend(_read_history_file_entries(path))
    candidates.sort(key=lambda item: str(item.get("reviewed_at") or ""), reverse=True)
    return candidates, False
