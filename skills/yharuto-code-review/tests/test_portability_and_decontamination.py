#!/usr/bin/env python3
"""Unit tests for v3 §3 & §7: zero hardcoded user paths, zero bytecode/Any/shell=True, and Evals Decontamination."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast
from pathlib import Path
import re
import unittest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _helpers import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, SKILL_ROOT, TESTS_DIR  # noqa: E402

GENERIC_FIXTURE_STEMS: frozenset[str] = frozenset(
    {
        "index",
        "types",
        "utils",
        "config",
        "helper",
        "helpers",
        "setup",
        "package",
        "tsconfig",
        "readme",
        "review",
        "pr_meta",
        "lint",
        "typecheck",
        "router",
        "server",
        "client",
        "schema",
        "logger",
    }
)

EXPLICIT_BANNED_CONTAMINATION_TERMS: tuple[str, ...] = (
    "executeRefund",
    "refundService",
    "PlayerView",
    "toggleFullscreen",
    "talk-player-stage",
    "billingSync",
    "syncInvoices",
    "taxCalculator",
    "calculateTax",
    "checkoutBadge",
    "currencyFormatter",
    "conversionRate",
    "purgeCache",
    "previewEmbed",
    "templateRegistry",
    "campaignHero",
    "legacyBanner",
    "customerAccountDataList",
    "THEME_STORAGE_KEY",
    "initThemeSync",
    "header-prefs",
)

EXPORTED_IDENTIFIER_RE = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function|const|class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]{5,})\b"
)


class TestPortabilityAndDecontamination(unittest.TestCase):
    def test_zero_hardcoded_user_paths_across_skill(self) -> None:
        # Build prefix dynamically so this test file itself never contains the literal prefix
        forbidden_macos = "/" + "Users/"
        forbidden_linux = "/" + "home/"

        offenders: list[str] = []
        for path in sorted(SKILL_ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(SKILL_ROOT).parts
            if any(part in {".git", "__pycache__", ".gemini", ".claude", "node_modules", ".venv"} for part in rel_parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(SKILL_ROOT).as_posix()
            for idx, line in enumerate(text.splitlines(), start=1):
                if forbidden_macos in line or forbidden_linux in line:
                    offenders.append(f"{rel}:L{idx}: {line.strip()[:100]}")

        self.assertEqual(offenders, [], "Hardcoded user home paths found:\n" + "\n".join(offenders))

    def test_zero_bytecode_or_os_junk_and_strict_ast_invariants(self) -> None:
        junk: list[str] = []
        for path in SKILL_ROOT.rglob("*"):
            rel_parts = path.relative_to(SKILL_ROOT).parts
            if any(part in {".git", ".gemini", ".claude", "node_modules", ".venv"} for part in rel_parts):
                continue
            if path.name in {"__pycache__", ".DS_Store"} or path.suffix in {".pyc", ".pyo"}:
                junk.append(path.relative_to(SKILL_ROOT).as_posix())
        self.assertEqual(junk, [], f"Bytecode or OS junk found: {junk}")

        py_files = sorted(list(SCRIPTS_DIR.glob("*.py")) + list(TESTS_DIR.glob("*.py")))
        self.assertTrue(len(py_files) >= 10)

        for py_file in py_files:
            src = py_file.read_text(encoding="utf-8")
            rel = py_file.relative_to(SKILL_ROOT).as_posix()
            tree = ast.parse(src, filename=rel)

            for node in ast.walk(tree):
                # 1. Forbid typing.Any import or annotation
                if isinstance(node, ast.ImportFrom) and node.module == "typing":
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "Any", f"{rel}: importing typing.Any is forbidden")
                if isinstance(node, ast.Name) and node.id == "Any":
                    self.fail(f"{rel}:L{node.lineno}: identifier 'Any' is forbidden")

                # 2. Forbid shell=True on any call
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self.fail(f"{rel}:L{node.lineno}: shell=True is strictly forbidden")

    def test_skill_markdown_docs_have_zero_eval_fixture_contamination(self) -> None:
        """Verify SKILL.md, README.md, references/, and guidelines/ contain zero fixture-specific leaks (#110 §3)."""
        fixture_terms: set[str] = set(EXPLICIT_BANNED_CONTAMINATION_TERMS)

        if FIXTURES_DIR.is_dir():
            for fpath in sorted(FIXTURES_DIR.rglob("*")):
                if not fpath.is_file():
                    continue
                name = fpath.name
                for compound in (".test.", ".spec.", ".mock.", ".d."):
                    pos = name.find(compound)
                    if pos > 0:
                        name = name[:pos]
                        break
                else:
                    name = fpath.stem

                if len(name) >= 5 and name.lower() not in GENERIC_FIXTURE_STEMS:
                    fixture_terms.add(name)

                if fpath.suffix in {".ts", ".tsx", ".js", ".mjs", ".astro", ".py"}:
                    try:
                        code = fpath.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    for m in EXPORTED_IDENTIFIER_RE.finditer(code):
                        ident = m.group(1)
                        if ident.lower() not in GENERIC_FIXTURE_STEMS:
                            fixture_terms.add(ident)

        doc_files: list[Path] = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "README.md",
            REPO_ROOT / "README.md",
        ]
        doc_files.extend(sorted((SKILL_ROOT / "references").rglob("*.md")))
        doc_files.extend(sorted((SKILL_ROOT / "guidelines").rglob("*.md")))

        compiled_patterns: list[tuple[str, re.Pattern[str]]] = []
        for term in sorted(fixture_terms):
            if re.fullmatch(r"[A-Za-z0-9_]+", term):
                pat = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            else:
                pat = re.compile(re.escape(term), re.IGNORECASE)
            compiled_patterns.append((term, pat))

        leaks: list[str] = []
        for doc in doc_files:
            if not doc.is_file():
                continue
            text = doc.read_text(encoding="utf-8")
            rel_doc = doc.relative_to(REPO_ROOT).as_posix()
            for line_no, raw_line in enumerate(text.splitlines(), start=1):
                for term, pat in compiled_patterns:
                    if pat.search(raw_line):
                        leaks.append(f"{rel_doc}:L{line_no} matched fixture term '{term}': {raw_line.strip()[:90]}")

        self.assertEqual(
            leaks,
            [],
            "Eval fixture contamination detected in skill documentation:\n" + "\n".join(leaks),
        )


if __name__ == "__main__":
    unittest.main()
