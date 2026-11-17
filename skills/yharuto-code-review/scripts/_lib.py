#!/usr/bin/env python3
"""Shared utilities, HMAC state sealing, session root resolution, and Markdown parsers."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

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
import time
from typing import Mapping, NoReturn, Sequence

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
    r"(?:\bnode\b[^\n]*(?:\s-e\b|\s--eval\b|--input-type\b)"
    r"|\bpython3?\b[^\n]*\s-c\b"
    r"|\b(?:sh|bash|zsh|dash|ksh)\b[^\n]*\s-c\b"
    r"|\b(?:curl|wget|nc|ncat|netcat|socat)\s"
    r"|\beval\s*(?:\(|[\"'`$])"
    r"|\b(?:writeFileSync|appendFileSync|child_process|execSync|spawnSync|execFileSync)\b"
    r"|\b(?:os\.system|subprocess\.(?:Popen|run|call|check_output|check_call)|urllib\.request|http\.client|socket\.connect)\b"
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


def run_cmd(
    cmd: Sequence[str],
    cwd: Path,
    timeout: int = CMD_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(cmd),
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
    try:
        return subprocess.run(
            list(cmd),
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


def write_sealed_gate_state(session_dir: Path, state: dict[str, object]) -> None:
    key_bytes = load_session_hmac_key(session_dir)
    if key_bytes is None:
        fail_with_code(f"Cannot seal gate_state.json: HMAC key missing for {session_dir}", 2)
    state_copy: dict[str, object] = {
        str(k): v for k, v in state.items() if str(k) not in {"state_hmac", "hmac_verified"}
    }
    state_copy["state_hmac"] = compute_state_hmac(key_bytes, state_copy)
    write_json_atomic(session_dir / "gate_state.json", state_copy)
    state["state_hmac"] = state_copy["state_hmac"]


def read_verified_gate_state(session_dir: Path) -> dict[str, object]:
    state_path = session_dir / "gate_state.json"
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
    norm = rel_path.replace("\\", "/").lstrip("./")
    p = Path(norm)
    if p.name in CI_CONFIG_FILENAMES:
        return True
    if norm.endswith(".cargo/config.toml") or norm.endswith(".cargo/config"):
        return True
    return False


def detect_ci_script_hazard_in_diff(diff_text: str) -> tuple[bool, list[str]]:
    """Inspect unified diff added lines in CI/build config files for inline execution or exfil payloads."""
    reasons: list[str] = []
    current_file = ""
    in_ci_config = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            in_ci_config = is_ci_config_path(current_file)
            continue
        if raw_line.startswith("+++ /dev/null"):
            current_file = ""
            in_ci_config = False
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

    for rel_cand in ("Makefile", "GNUmakefile", "pyproject.toml", "setup.py", "build.rs", ".cargo/config.toml"):
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
