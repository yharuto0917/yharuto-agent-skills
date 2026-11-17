#!/usr/bin/env python3
"""Stage 1 & Stage 3 symbol reference, test/mock, comparative sibling, and build-hash scanner."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import (  # noqa: E402
    emit_json,
    fail_with_code,
    normalize_rel_path,
    read_verified_gate_state,
    run_subproc,
    sha256_file,
    split_nul_paths,
    utc_now_iso,
    write_json_atomic,
    write_sealed_gate_state,
)

MAX_SYMBOLS = 20
MAX_REFS_PER_SYMBOL = 30
SEARCH_TIMEOUT_SECONDS = 10
GIT_TIMEOUT_SECONDS = 20

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".gemini",
        ".claude",
        ".codex",
        ".agents",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".astro",
        "coverage",
        "target",
        "vendor",
    }
)

IGNORED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "bun.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "go.sum",
    }
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp4",
        ".webm",
        ".zip",
        ".tar",
        ".gz",
        ".pyc",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".wasm",
    }
)

CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".astro",
        ".vue",
        ".svelte",
        ".py",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".php",
        ".cs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".sh",
    }
)

NOISE_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "function",
        "class",
        "const",
        "let",
        "var",
        "export",
        "default",
        "async",
        "await",
        "import",
        "from",
        "constructor",
        "render",
        "main",
        "init",
        "setup",
        "teardown",
        "describe",
        "it",
        "test",
        "expect",
        "true",
        "false",
        "null",
        "none",
        "self",
        "cls",
        "err",
        "error",
        "req",
        "res",
        "ctx",
        "app",
        "data",
        "result",
        "value",
        "item",
        "args",
        "kwargs",
    }
)

SYMBOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("function", re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]{2,})\b")),
    ("class", re.compile(r"\b(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_][\w]{2,})\b")),
    ("interface_or_type", re.compile(r"\b(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_][\w]{2,})\b")),
    (
        "const_binding",
        re.compile(
            r"\b(?:export\s+)?const\s+([A-Za-z_$][\w$]{2,})\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
        ),
    ),
    ("const_export", re.compile(r"\bexport\s+(?:const|let)\s+([A-Za-z_$][\w$]{2,})\b")),
    ("py_def", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]{2,})\s*\(")),
    ("go_func", re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][\w]{2,})\s*\(")),
    ("go_type", re.compile(r"^\s*type\s+([A-Za-z_][\w]{2,})\s+(?:struct|interface)\b")),
    ("rust_item", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][\w]{2,})\b")),
)

EXPLICIT_TEST_FILENAME_RE = re.compile(
    r"(?:\.(?:test|spec)\.[A-Za-z0-9]+$)"
    r"|^(?:test_[^/]+\.py|[^/]+_test\.(?:py|go|rs))$",
    re.IGNORECASE,
)

EXPLICIT_MOCK_OR_SETUP_FILENAME_RE = re.compile(
    r"(?:\.mock\.[A-Za-z0-9]+$)"
    r"|^(?:conftest\.py|(?:setupTests|vitest\.setup|jest\.setup)\.[A-Za-z0-9]+)$",
    re.IGNORECASE,
)

TEST_OR_MOCK_DIR_NAMES: frozenset[str] = frozenset(
    {
        "__tests__",
        "__mocks__",
        "test",
        "tests",
        "spec",
        "specs",
        "mock",
        "mocks",
        "testing",
        "test-utils",
        "test_helpers",
    }
)

FIXTURE_DIR_NAMES: frozenset[str] = frozenset({"fixture", "fixtures"})
MONOREPO_CONTAINER_DIR_NAMES: frozenset[str] = frozenset({"packages", "apps", "services"})

MOCK_HINT_RE = re.compile(r"(?:__mocks__|/mocks?/|mock|fixture|helper|setup|stub|fake)", re.IGNORECASE)


def _find_fixture_dir_index(dir_parts: Sequence[str]) -> int | None:
    for idx, seg in enumerate(dir_parts):
        if seg not in FIXTURE_DIR_NAMES:
            continue
        # Do not treat a monorepo workspace package named `packages/fixtures/...` as a static fixture tree
        if idx == 1 and dir_parts[0] in MONOREPO_CONTAINER_DIR_NAMES:
            continue
        return idx
    return None


def is_test_or_mock_file(rel_path: str) -> bool:
    """Return True iff `rel_path` is an executable test or code mock/helper (excluding non-code and fixture snapshots)."""
    norm = normalize_rel_path(rel_path)
    p = Path(norm)
    if p.suffix.lower() not in CODE_EXTENSIONS:
        return False

    dir_parts = [seg.lower() for seg in p.parts[:-1]]
    fixture_idx = _find_fixture_dir_index(dir_parts)
    test_dir_idx = next((i for i, seg in enumerate(dir_parts) if seg in TEST_OR_MOCK_DIR_NAMES), None)

    if fixture_idx is not None:
        if test_dir_idx is None or fixture_idx < test_dir_idx:
            return False
        return bool(
            EXPLICIT_TEST_FILENAME_RE.search(p.name)
            or EXPLICIT_MOCK_OR_SETUP_FILENAME_RE.search(p.name)
            or re.search(r"(?:mock|stub|fake)", p.stem, re.IGNORECASE)
        )

    if EXPLICIT_TEST_FILENAME_RE.search(p.name) or EXPLICIT_MOCK_OR_SETUP_FILENAME_RE.search(p.name):
        return True
    return test_dir_idx is not None


def classify_test_or_mock_kind(rel_path: str) -> str:
    norm = normalize_rel_path(rel_path)
    name = Path(norm).name
    if EXPLICIT_TEST_FILENAME_RE.search(name):
        return "test"
    if EXPLICIT_MOCK_OR_SETUP_FILENAME_RE.search(name) or MOCK_HINT_RE.search(norm):
        return "mock_or_helper"
    return "test"


def is_non_production_path(rel_path: str) -> bool:
    """Return True if `rel_path` belongs to tests, mocks, or static fixture trees rather than production code."""
    norm = normalize_rel_path(rel_path)
    p = Path(norm)
    dir_parts = [seg.lower() for seg in p.parts[:-1]]
    if _find_fixture_dir_index(dir_parts) is not None or any(seg in TEST_OR_MOCK_DIR_NAMES for seg in dir_parts):
        return True
    return bool(
        EXPLICIT_TEST_FILENAME_RE.search(p.name)
        or EXPLICIT_MOCK_OR_SETUP_FILENAME_RE.search(p.name)
    )


BUILD_CONFIG_FILENAME_RE = re.compile(
    r"(?:^(?:vite|webpack|rollup|astro|next|nuxt|rspack|esbuild|tsup)\.config\.[A-Za-z0-9]+$"
    r"|(?:^|[-_.])(?:build|bundle|hash|manifest|fingerprint)[^/]*\.(?:ts|js|mjs|cjs|py|sh|go|rs)$"
    r"|^Makefile$)",
    re.IGNORECASE,
)

BUILD_HASH_CONTENT_RE = re.compile(
    r"(?:createHash\s*\(|hashlib\.(?:sha256|sha1|md5|blake2)|contenthash|chunkhash|"
    r"assetFileNames|entryFileNames|cacheKey|computeHash|fileHash|manifestHash)",
    re.IGNORECASE,
)


def walk_repo_files(workdir: Path) -> list[str]:
    results: list[str] = []
    for root, dirs, files in os.walk(workdir, topdown=True, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (Path(root) / d).is_symlink())
        rel_root = Path(root).relative_to(workdir)
        for fname in sorted(files):
            if fname in IGNORED_FILENAMES:
                continue
            fpath = Path(root) / fname
            if fpath.is_symlink():
                continue
            if fpath.suffix.lower() in BINARY_EXTENSIONS:
                continue
            rel_posix = (rel_root / fname).as_posix() if str(rel_root) != "." else fname
            results.append(rel_posix)
    return results


def get_diff_and_changed_files(
    workdir: Path,
    repo_dir: Path,
    mode: str,
    merge_base_sha: str,
    head_sha: str,
    preset_changed: Sequence[str] | None,
    audit_paths: Sequence[str],
    all_files: Sequence[str],
) -> tuple[list[str], str]:
    if mode in ("pr", "range") and merge_base_sha and head_sha:
        diff_res = run_subproc(["git", "diff", "-U0", merge_base_sha, head_sha], cwd=repo_dir, timeout=GIT_TIMEOUT_SECONDS)
        diff_text = diff_res.stdout if diff_res and diff_res.returncode == 0 else ""
        if preset_changed is not None:
            return list(preset_changed), diff_text
        names_res = run_subproc(["git", "diff", "--name-only", "-z", merge_base_sha, head_sha], cwd=repo_dir, timeout=GIT_TIMEOUT_SECONDS)
        changed = split_nul_paths(names_res.stdout) if names_res else []
        return changed, diff_text

    if mode == "local":
        git_check = run_subproc(["git", "rev-parse", "--is-inside-work-tree"], cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
        if git_check is None or git_check.returncode != 0:
            changed_fallback = list(preset_changed) if preset_changed is not None else list(all_files)
            snap_lines: list[str] = []
            for rel in changed_fallback:
                p = workdir / rel
                if p.is_file() and p.suffix.lower() in CODE_EXTENSIONS:
                    try:
                        snap_lines.append(f"+++ b/{rel}")
                        snap_lines.extend("+" + ln for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines())
                    except OSError:
                        continue
            return changed_fallback, "\n".join(snap_lines)

        head_chk = run_subproc(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
        has_head = head_chk is not None and head_chk.returncode == 0
        diff_cmd = ["git", "diff", "-U0", "HEAD"] if has_head else ["git", "diff", "--cached", "-U0"]
        names_cmd = ["git", "diff", "--name-only", "-z", "HEAD"] if has_head else ["git", "diff", "--cached", "--name-only", "-z"]
        diff_res = run_subproc(diff_cmd, cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
        names_res = run_subproc(names_cmd, cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
        untracked_res = run_subproc(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=workdir,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        tracked = split_nul_paths(names_res.stdout) if names_res else []
        untracked = split_nul_paths(untracked_res.stdout) if untracked_res else []
        changed = list(preset_changed) if preset_changed is not None else list(dict.fromkeys(tracked + untracked))
        extra_lines: list[str] = []
        for rel in untracked:
            p = workdir / rel
            if p.is_file() and p.suffix.lower() in CODE_EXTENSIONS:
                try:
                    extra_lines.append(f"+++ b/{rel}")
                    extra_lines.extend("+" + ln for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines())
                except OSError:
                    continue
        diff_text = (diff_res.stdout if diff_res else "") + ("\n" + "\n".join(extra_lines) if extra_lines else "")
        return changed, diff_text

    # audit mode
    norm_prefixes = [
        cleaned
        for p in audit_paths
        if p.strip()
        for cleaned in (normalize_rel_path(p.strip()).strip("/"),)
        if cleaned and cleaned != "."
    ]
    if norm_prefixes:
        filtered = [
            f for f in all_files if any(f == pref or f.startswith(pref + "/") for pref in norm_prefixes)
        ]
    else:
        filtered = list(preset_changed) if preset_changed is not None else list(all_files)
    sampled_lines: list[str] = []
    for rel in filtered[:40]:
        fpath = workdir / rel
        if fpath.is_file() and fpath.suffix.lower() in CODE_EXTENSIONS:
            try:
                sampled_lines.append(f"+++ b/{rel}")
                sampled_lines.extend("+" + ln for ln in fpath.read_text(encoding="utf-8", errors="ignore").splitlines()[:300])
            except OSError:
                continue
    return filtered, "\n".join(sampled_lines)


def extract_changed_symbols(diff_text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    current_file = ""

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            continue
        if current_file and is_non_production_path(current_file):
            continue
        if not ((line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---"))):
            continue
        code_line = line[1:]
        for kind, pattern in SYMBOL_PATTERNS:
            for match in pattern.finditer(code_line):
                sym = match.group(1)
                if sym.lower() in NOISE_IDENTIFIERS or sym.startswith("__"):
                    continue
                if sym not in seen:
                    seen.add(sym)
                    found.append((sym, kind))
                    if len(found) >= MAX_SYMBOLS:
                        return found
    return found


def choose_search_backend(workdir: Path) -> str:
    if shutil.which("rg") is not None:
        return "rg"
    if (workdir / ".git").exists() and shutil.which("git") is not None:
        return "git_grep"
    return "python_scan"


def parse_grep_lines(raw_stdout: str) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    for line in raw_stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel_file = normalize_rel_path(parts[0])
        if any(seg in IGNORED_DIRS for seg in Path(rel_file).parts) or Path(rel_file).name in IGNORED_FILENAMES:
            continue
        if not parts[1].isdigit():
            continue
        refs.append(
            {
                "file": rel_file,
                "line": int(parts[1]),
                "excerpt": parts[2].strip()[:200],
            }
        )
    return refs


def search_symbol_refs(
    workdir: Path,
    symbol: str,
    backend: str,
    cached_files: Sequence[str],
) -> list[dict[str, object]]:
    if backend == "rg":
        cmd = [
            "rg",
            "-n",
            "--no-heading",
            "--color",
            "never",
            "-w",
            "--glob",
            "!node_modules/**",
            "--glob",
            "!.git/**",
            "--glob",
            "!.gemini/**",
            "--glob",
            "!.claude/**",
            "--glob",
            "!.venv/**",
            "--glob",
            "!dist/**",
            "--glob",
            "!build/**",
            "--glob",
            "!*lock*",
            symbol,
            ".",
        ]
        res = run_subproc(cmd, cwd=workdir, timeout=SEARCH_TIMEOUT_SECONDS)
        if res is not None and res.returncode in (0, 1):
            return parse_grep_lines(res.stdout)

    if backend in ("rg", "git_grep") and (workdir / ".git").exists():
        res = run_subproc(["git", "grep", "-n", "-I", "-w", symbol], cwd=workdir, timeout=SEARCH_TIMEOUT_SECONDS)
        if res is not None and res.returncode in (0, 1):
            return parse_grep_lines(res.stdout)

    word_re = re.compile(rf"\b{re.escape(symbol)}\b")
    py_refs: list[dict[str, object]] = []
    for rel in cached_files:
        fpath = workdir / rel
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if symbol not in text:
            continue
        for idx, raw_ln in enumerate(text.splitlines(), start=1):
            if word_re.search(raw_ln):
                py_refs.append({"file": rel, "line": idx, "excerpt": raw_ln.strip()[:200]})
    return py_refs


def stem_without_compound_ext(rel_path: str) -> str:
    norm = normalize_rel_path(rel_path)
    name = Path(norm).name
    for suffix in (".test", ".spec", ".mock", ".d"):
        idx = name.lower().find(suffix + ".")
        if idx > 0:
            return name[:idx]
    return Path(name).stem


def find_related_tests_and_mocks(
    workdir: Path,
    all_files: Sequence[str],
    changed_files: Sequence[str],
    symbol_results: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}

    def record(rel_file: str, reason: str) -> None:
        if rel_file in found:
            return
        kind = classify_test_or_mock_kind(rel_file)
        found[rel_file] = {"file": rel_file, "kind": kind, "reason": reason}

    changed_stems: set[str] = set()
    for ch in changed_files:
        if is_test_or_mock_file(ch) and (workdir / ch).is_file():
            record(ch, "modified_in_diff")
        elif Path(ch).suffix.lower() in CODE_EXTENSIONS and not is_non_production_path(ch):
            st = stem_without_compound_ext(ch)
            if len(st) >= 2 and st.lower() not in {"index", "main", "utils", "types"}:
                changed_stems.add(st.lower())

    for sym_entry in symbol_results:
        sym_name = str(sym_entry.get("name") or "")
        refs = sym_entry.get("refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            rfile = str(ref.get("file") or "")
            if rfile and is_test_or_mock_file(rfile) and (workdir / rfile).is_file():
                record(rfile, f"references_symbol:{sym_name}")

    candidate_test_files = [f for f in all_files if is_test_or_mock_file(f)]
    stem_patterns: list[tuple[str, str, re.Pattern[str]]] = [
        (
            c_stem,
            re.sub(r"[_-]+", "", c_stem),
            re.compile(
                rf"(?<![a-z0-9_]){re.escape(c_stem)}(?![a-z0-9_])"
                if (len(c_stem) >= 4 or not c_stem.isascii())
                else rf"[/'\".\\]{re.escape(c_stem)}(?![a-z0-9_])"
            ),
        )
        for c_stem in sorted(changed_stems)
    ]
    for tf in candidate_test_files:
        tf_stem = stem_without_compound_ext(tf).lower()
        tf_core = tf_stem[5:] if tf_stem.startswith("test_") else tf_stem[:-5] if tf_stem.endswith("_test") else tf_stem
        tf_norm = re.sub(r"[_-]+", "", tf_core)
        for c_stem, c_norm, _ in stem_patterns:
            if tf_stem == c_stem or tf_stem == f"test_{c_stem}" or tf_stem == f"{c_stem}_test" or (c_norm and tf_norm == c_norm):
                record(tf, f"matches_module_stem:{c_stem}")
                break
        if tf in found or not stem_patterns:
            continue
        try:
            content = (workdir / tf).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        content_lower = content.lower()
        for c_stem, _, pat in stem_patterns:
            if pat.search(content_lower):
                record(tf, f"mentions_module:{c_stem}")
                break

    has_related_test = any(entry["kind"] == "test" for entry in found.values())
    if has_related_test and len(candidate_test_files) <= 25:
        for tf in candidate_test_files:
            if tf not in found and classify_test_or_mock_kind(tf) == "mock_or_helper":
                record(tf, "shared_test_mock_or_helper")

    return [found[k] for k in sorted(found)]


def find_comparative_files(
    all_files: Sequence[str],
    changed_files: Sequence[str],
) -> list[dict[str, str]]:
    changed_set = set(changed_files)
    changed_code = [
        f
        for f in changed_files
        if Path(f).suffix.lower() in CODE_EXTENSIONS and not is_non_production_path(f)
    ]
    changed_dirs = {str(Path(f).parent) for f in changed_code}

    comparative: dict[str, dict[str, str]] = {}
    for candidate in all_files:
        if candidate in changed_set or is_non_production_path(candidate):
            continue
        if Path(candidate).suffix.lower() not in CODE_EXTENSIONS:
            continue
        parent_str = str(Path(candidate).parent)
        if parent_str in changed_dirs:
            comparative[candidate] = {
                "file": candidate,
                "reason": f"sibling_in_directory:{parent_str}",
            }
            if len(comparative) >= 20:
                break

    if len(comparative) < 20:
        role_re = re.compile(r"(?:admin|auth|route|handler|controller|api|message|sync|theme|bus)", re.IGNORECASE)
        changed_has_role = any(role_re.search(f) for f in changed_code)
        if changed_has_role:
            for candidate in all_files:
                if candidate in changed_set or candidate in comparative or is_non_production_path(candidate):
                    continue
                if Path(candidate).suffix.lower() in CODE_EXTENSIONS and role_re.search(candidate):
                    comparative[candidate] = {"file": candidate, "reason": "matching_architectural_role"}
                    if len(comparative) >= 20:
                        break

    changed_leaf_pairs = (
        {
            (Path(f).parent.name.lower(), Path(f).suffix.lower())
            for f in changed_code
            if len(Path(f).parts) >= 3 and Path(f).parent.name.lower() not in {"src", "lib", "app", "pkg"}
        }
        if not comparative
        else set()
    )
    for candidate in all_files if changed_leaf_pairs else ():
        if candidate in changed_set or is_non_production_path(candidate):
            continue
        cand_path = Path(candidate)
        pair = (cand_path.parent.name.lower(), cand_path.suffix.lower())
        if len(cand_path.parts) >= 3 and pair in changed_leaf_pairs:
            comparative[candidate] = {
                "file": candidate,
                "reason": f"parallel_directory:{cand_path.parent.name}",
            }
            if len(comparative) >= 20:
                break

    return [comparative[k] for k in sorted(comparative)]


def find_build_hash_configs(workdir: Path, all_files: Sequence[str]) -> list[dict[str, object]]:
    configs: list[dict[str, object]] = []
    seen: set[str] = set()

    for rel in all_files:
        if is_non_production_path(rel):
            continue
        fname = Path(rel).name
        is_build_name = BUILD_CONFIG_FILENAME_RE.search(fname) is not None or rel.startswith(".github/workflows/")
        suffix = Path(rel).suffix.lower()
        if not is_build_name and suffix not in CODE_EXTENSIONS and suffix not in {".yml", ".yaml", ".json"}:
            continue

        fpath = workdir / rel
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        match = BUILD_HASH_CONTENT_RE.search(text)
        if match is not None:
            line_no = text.count("\n", 0, match.start()) + 1
            configs.append(
                {
                    "file": rel,
                    "line": line_no,
                    "reason": f"hash_or_cache_logic:{match.group(0)[:40]}",
                }
            )
            seen.add(rel)
        elif is_build_name and rel not in seen:
            configs.append(
                {
                    "file": rel,
                    "line": 1,
                    "reason": "build_or_bundler_config",
                }
            )
            seen.add(rel)

        if len(configs) >= 20:
            break

    return configs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="impact_scan.py",
        description="Scan symbol references, related tests/mocks, comparative files, and build hash configs.",
    )
    parser.add_argument("--session", default=None, help="Session directory created by prepare_worktree.py.")
    parser.add_argument("--workdir", "--repo", dest="workdir", default=None, help="Standalone workdir path.")
    parser.add_argument("--base", default=None, help="Base commit SHA for standalone mode.")
    parser.add_argument("--head", default=None, help="Head commit SHA for standalone mode.")
    parser.add_argument("--range", default=None, help="Commit range <base>..<head> for standalone mode.")
    parser.add_argument("--local", action="store_true", help="Scan uncommitted local changes in standalone mode.")
    parser.add_argument("--audit", nargs="*", default=None, help="Audit mode paths in standalone mode.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    session_dir: Path | None = None
    state: dict[str, object] = {}
    preset_changed: list[str] | None = None
    audit_paths: list[str] = []

    if args.session is not None:
        session_dir = Path(str(args.session)).expanduser().resolve()
        state = read_verified_gate_state(session_dir)

        workdir = Path(str(state.get("workdir") or "")).resolve()
        repo_dir = Path(str(state.get("repo_dir") or workdir)).resolve()
        mode = str(state.get("mode") or "local")
        merge_base_sha = str(state.get("merge_base_sha") or state.get("base_sha") or "")
        head_sha = str(state.get("head_sha") or "")
        ch_raw = state.get("changed_files")
        if isinstance(ch_raw, list):
            preset_changed = [str(x) for x in ch_raw]
        ap_raw = state.get("audit_paths")
        if isinstance(ap_raw, list):
            audit_paths = [str(x) for x in ap_raw]
    elif args.workdir is not None:
        workdir = Path(str(args.workdir)).expanduser().resolve()
        repo_dir = workdir
        if not workdir.is_dir():
            fail_with_code(f"--workdir does not exist: {workdir}", 2)
        if args.audit is not None:
            mode = "audit"
            merge_base_sha = ""
            head_sha = ""
            audit_paths = [str(x) for x in args.audit]
        elif args.range:
            mode = "range"
            spec = str(args.range)
            left, right = spec.split("...", 1) if "..." in spec else spec.split("..", 1) if ".." in spec else ("", "")
            if not left or not right:
                fail_with_code(f"Invalid --range: {spec}", 2)
            mb_res = run_subproc(["git", "merge-base", left.strip(), right.strip()], cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
            hd_res = run_subproc(["git", "rev-parse", right.strip()], cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
            merge_base_sha = mb_res.stdout.strip() if mb_res and mb_res.returncode == 0 else left.strip()
            head_sha = hd_res.stdout.strip() if hd_res and hd_res.returncode == 0 else right.strip()
        elif args.base and args.head:
            mode = "range"
            mb_res = run_subproc(["git", "merge-base", str(args.base), str(args.head)], cwd=workdir, timeout=GIT_TIMEOUT_SECONDS)
            merge_base_sha = mb_res.stdout.strip() if mb_res and mb_res.returncode == 0 else str(args.base)
            head_sha = str(args.head)
        else:
            mode = "local"
            merge_base_sha = ""
            head_sha = ""
    else:
        fail_with_code("Specify either --session <dir> or --workdir <dir>.", 2)

    all_files = walk_repo_files(workdir)
    changed_files, diff_text = get_diff_and_changed_files(
        workdir=workdir,
        repo_dir=repo_dir,
        mode=mode,
        merge_base_sha=merge_base_sha,
        head_sha=head_sha,
        preset_changed=preset_changed,
        audit_paths=audit_paths,
        all_files=all_files,
    )

    backend = choose_search_backend(workdir)
    extracted_symbols = extract_changed_symbols(diff_text)

    symbols_out: list[dict[str, object]] = []
    for sym_name, sym_kind in extracted_symbols:
        all_refs = search_symbol_refs(workdir, sym_name, backend, all_files)
        total = len(all_refs)
        symbols_out.append(
            {
                "name": sym_name,
                "kind": sym_kind,
                "refs": all_refs[:MAX_REFS_PER_SYMBOL],
                "total_count": total,
                "overflow": total > MAX_REFS_PER_SYMBOL,
            }
        )

    related_tests = find_related_tests_and_mocks(workdir, all_files, changed_files, symbols_out)
    comparative_files = find_comparative_files(all_files, changed_files)
    build_hash_configs = find_build_hash_configs(workdir, all_files)

    result: dict[str, object] = {
        "symbols": symbols_out,
        "related_tests_and_mocks": related_tests,
        "comparative_files": comparative_files,
        "build_hash_configs": build_hash_configs,
        "search_backend": backend,
    }

    if session_dir is not None:
        impact_path = session_dir / "impact_scan.json"
        write_json_atomic(impact_path, result)
        state["stage1_scan"] = {
            "executed": True,
            "sha256": sha256_file(impact_path),
            "updated_at": utc_now_iso(),
        }
        write_sealed_gate_state(session_dir, state)

    emit_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
