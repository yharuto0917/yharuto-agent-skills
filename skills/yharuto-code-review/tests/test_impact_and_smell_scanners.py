#!/usr/bin/env python3
"""Unit and CLI tests for `impact_scan.py` extraction accuracy and `scan_diff_smells.py` 7-rule detection."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
from pathlib import Path
import tempfile
import unittest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_TESTS_DIR.parent / "scripts") not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR.parent / "scripts"))

from _helpers import (  # noqa: E402
    git_commit_all,
    init_git_repo,
    run_git,
    run_script,
)
from scan_diff_smells import ALL_RULE_IDS, scan_single_file  # noqa: E402


class TestImpactAndSmellScanners(unittest.TestCase):
    def test_impact_scan_extracts_related_tests_comparatives_and_build_hash_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo)
            (repo / "src" / "auth").mkdir(parents=True)
            (repo / "src" / "bus").mkdir(parents=True)
            (repo / "scripts").mkdir()
            (repo / "tests" / "_helpers").mkdir(parents=True)

            (repo / "src" / "auth" / "tokenHandler.ts").write_text(
                "export function verifySessionToken(raw: string): boolean {\n"
                "  return raw.startsWith('tok_');\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "src" / "auth" / "roleHandler.ts").write_text(
                "export function hasAdminRole(role: string): boolean {\n"
                "  return role === 'admin';\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "src" / "bus" / "eventBus.ts").write_text(
                "export function publishEvent(topic: string): string {\n"
                "  return topic;\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "scripts" / "compute-build-hash.ts").write_text(
                "import crypto from 'node:crypto';\n"
                "export function digestAsset(input: string): string {\n"
                "  return crypto.createHash('sha256').update(input).digest('hex');\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "vite.config.ts").write_text(
                "export default { clearScreen: false };\n",
                encoding="utf-8",
            )

            # 1) Stem-matched test (does not call verifySessionToken directly)
            (repo / "tests" / "tokenHandler.test.ts").write_text(
                "import test from 'node:test';\n"
                "test('placeholder stem match', () => {});\n",
                encoding="utf-8",
            )
            # 2) Symbol-referencing test (stem does not match tokenHandler)
            (repo / "tests" / "sessionFlow.spec.ts").write_text(
                "import { verifySessionToken } from '../src/guard.js';\n"
                "verifySessionToken('tok_123');\n",
                encoding="utf-8",
            )
            # 3) Module-mentioning test (mentions tokenHandler in string, no symbol call or stem match)
            (repo / "tests" / "routingSuite.test.ts").write_text(
                "const targetModule = 'auth/tokenHandler';\n"
                "void targetModule;\n",
                encoding="utf-8",
            )
            # 4) Test modified directly in diff
            (repo / "tests" / "modifiedCheck.test.ts").write_text(
                "export const v = 1;\n",
                encoding="utf-8",
            )
            # 5) Shared mock/helper in tests/_helpers (alphabetically before tokenHandler.test.ts)
            (repo / "tests" / "_helpers" / "authStub.ts").write_text(
                "export function createStubSession(): { active: boolean } {\n"
                "  return { active: true };\n"
                "}\n",
                encoding="utf-8",
            )
            # 6) Unrelated test that must NOT be returned
            (repo / "tests" / "unrelatedCatalog.test.ts").write_text(
                "export const catalogCount = 5;\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "base commit")

            run_git(repo, ["checkout", "-b", "pr-branch"])
            (repo / "src" / "auth" / "tokenHandler.ts").write_text(
                "export function verifySessionToken(raw: string, minLen = 8): boolean {\n"
                "  return raw.startsWith('tok_') && raw.length >= minLen;\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "tests" / "modifiedCheck.test.ts").write_text(
                "export const v = 2;\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "tighten session token check")

            rc, out, _, stderr = run_script(
                "impact_scan.py",
                ["--workdir", str(repo), "--range", "main..pr-branch"],
            )
            self.assertEqual(rc, 0, stderr)

            symbols = out.get("symbols")
            self.assertIsInstance(symbols, list)
            symbol_names = [str(s.get("name")) for s in symbols if isinstance(s, dict)]
            self.assertEqual(symbol_names, ["verifySessionToken"])

            self.assertEqual(
                out.get("related_tests_and_mocks"),
                [
                    {
                        "file": "tests/_helpers/authStub.ts",
                        "kind": "mock_or_helper",
                        "reason": "shared_test_mock_or_helper",
                    },
                    {
                        "file": "tests/modifiedCheck.test.ts",
                        "kind": "test",
                        "reason": "modified_in_diff",
                    },
                    {
                        "file": "tests/routingSuite.test.ts",
                        "kind": "test",
                        "reason": "mentions_module:tokenhandler",
                    },
                    {
                        "file": "tests/sessionFlow.spec.ts",
                        "kind": "test",
                        "reason": "references_symbol:verifySessionToken",
                    },
                    {
                        "file": "tests/tokenHandler.test.ts",
                        "kind": "test",
                        "reason": "matches_module_stem:tokenhandler",
                    },
                ],
            )
            self.assertEqual(
                out.get("comparative_files"),
                [
                    {
                        "file": "src/auth/roleHandler.ts",
                        "reason": "sibling_in_directory:src/auth",
                    },
                    {
                        "file": "src/bus/eventBus.ts",
                        "reason": "matching_architectural_role",
                    },
                ],
            )
            self.assertEqual(
                out.get("build_hash_configs"),
                [
                    {
                        "file": "vite.config.ts",
                        "line": 1,
                        "reason": "build_or_bundler_config",
                    },
                    {
                        "file": "scripts/compute-build-hash.ts",
                        "line": 3,
                        "reason": "hash_or_cache_logic:createHash(",
                    },
                ],
            )

    def test_impact_scan_boundary_zero_symbols_no_matching_tests_and_fixture_non_test_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # --- Boundary 1: Diff with 0 extracted symbols (only body line edited) ---
            repo_zero_sym = root / "repo_zero_sym"
            init_git_repo(repo_zero_sym)
            (repo_zero_sym / "src").mkdir()
            (repo_zero_sym / "tests").mkdir()
            (repo_zero_sym / "src" / "priceCalc.ts").write_text(
                "export function calcTotal(unitPrice: number): number {\n"
                "  const fee = 100;\n"
                "  return unitPrice + fee;\n"
                "}\n",
                encoding="utf-8",
            )
            (repo_zero_sym / "tests" / "priceCalc.test.ts").write_text(
                "import { calcTotal } from '../src/priceCalc.js';\n"
                "calcTotal(500);\n",
                encoding="utf-8",
            )
            git_commit_all(repo_zero_sym, "base")
            run_git(repo_zero_sym, ["checkout", "-b", "pr-branch"])
            # Modify only `const fee = 100;` -> `const fee = 150;` (0 symbol declarations in diff -U0)
            (repo_zero_sym / "src" / "priceCalc.ts").write_text(
                "export function calcTotal(unitPrice: number): number {\n"
                "  const fee = 150;\n"
                "  return unitPrice + fee;\n"
                "}\n",
                encoding="utf-8",
            )
            git_commit_all(repo_zero_sym, "adjust fee constant only")

            rc_z, out_z, _, _ = run_script(
                "impact_scan.py",
                ["--workdir", str(repo_zero_sym), "--range", "main..pr-branch"],
            )
            self.assertEqual(rc_z, 0)
            self.assertEqual(out_z.get("symbols"), [])
            self.assertEqual(
                out_z.get("related_tests_and_mocks"),
                [
                    {
                        "file": "tests/priceCalc.test.ts",
                        "kind": "test",
                        "reason": "matches_module_stem:pricecalc",
                    }
                ],
            )

            # --- Boundary 2: Change with NO corresponding test (even with unrelated tests & helpers) ---
            repo_untested = root / "repo_untested"
            init_git_repo(repo_untested)
            (repo_untested / "src").mkdir()
            (repo_untested / "tests" / "helpers").mkdir(parents=True)
            (repo_untested / "src" / "rate.ts").write_text(
                "export function computeTierRate(level: number): number {\n"
                "  return level * 5;\n"
                "}\n",
                encoding="utf-8",
            )
            # Contains substring "rate" inside "iterate" and Jest `it(...)` to verify short-stem & word-boundary safety
            (repo_untested / "src" / "it.ts").write_text(
                "export const localeCode = 'it';\n",
                encoding="utf-8",
            )
            (repo_untested / "tests" / "catalog.test.ts").write_text(
                "export function checkCatalog(): number {\n"
                "  // iterate over static items; it('should stay unrelated')\n"
                "  return 1;\n"
                "}\n",
                encoding="utf-8",
            )
            (repo_untested / "tests" / "helpers" / "stubCatalog.ts").write_text(
                "export const stubCatalog = [];\n",
                encoding="utf-8",
            )
            git_commit_all(repo_untested, "base")
            run_git(repo_untested, ["checkout", "-b", "pr-branch"])
            (repo_untested / "src" / "rate.ts").write_text(
                "export function computeTierRate(level: number, cap = 50): number {\n"
                "  return Math.min(level * 5, cap);\n"
                "}\n",
                encoding="utf-8",
            )
            (repo_untested / "src" / "it.ts").write_text(
                "export const localeCode = 'it-IT';\n",
                encoding="utf-8",
            )
            git_commit_all(repo_untested, "cap rate without adding tests")

            rc_u, out_u, _, _ = run_script(
                "impact_scan.py",
                ["--workdir", str(repo_untested), "--range", "main..pr-branch"],
            )
            self.assertEqual(rc_u, 0)
            u_symbols = out_u.get("symbols", [])
            self.assertIsInstance(u_symbols, list)
            self.assertEqual(
                [str(s.get("name")) for s in u_symbols if isinstance(s, dict)],
                ["localeCode", "computeTierRate"],
            )
            self.assertEqual(out_u.get("related_tests_and_mocks"), [])
            self.assertEqual(out_u.get("comparative_files"), [])
            self.assertEqual(out_u.get("build_hash_configs"), [])

            # --- Boundary 3: Diff with non-test files under fixtures/ and evals/fixtures/ ---
            repo_fix = root / "repo_fixtures"
            init_git_repo(repo_fix)
            (repo_fix / "evals").mkdir()
            (repo_fix / "tests" / "fixtures").mkdir(parents=True)
            (repo_fix / "evals" / "runner.py").write_text(
                "def run_suite() -> int:\n    return 0\n",
                encoding="utf-8",
            )
            git_commit_all(repo_fix, "base")
            run_git(repo_fix, ["checkout", "-b", "pr-branch"])

            # Non-test config, convention, code, and embedded snapshot files under fixtures
            (repo_fix / "fixtures").mkdir()
            (repo_fix / "fixtures" / "config.json").write_text('{"mode": "strict"}\n', encoding="utf-8")
            (repo_fix / "fixtures" / "REVIEW.md").write_text("# Fixture Rules\n", encoding="utf-8")
            (repo_fix / "fixtures" / "sampleModule.ts").write_text(
                "export function fixtureOnlyFunc(): number { return 1; }\n",
                encoding="utf-8",
            )

            case_dir = repo_fix / "evals" / "fixtures" / "demo-case"
            (case_dir / "base" / "src").mkdir(parents=True)
            (case_dir / "head" / "src").mkdir(parents=True)
            (case_dir / "head" / "tests").mkdir(parents=True)
            (case_dir / "head" / "scripts").mkdir(parents=True)
            (case_dir / "pr_meta.json").write_text('{"number": 1}\n', encoding="utf-8")
            (case_dir / "base" / "REVIEW.md").write_text("# Demo Review\n", encoding="utf-8")
            (case_dir / "base" / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
            (case_dir / "base" / "src" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
            (case_dir / "head" / "src" / "app.ts").write_text("export const x = 2;\n", encoding="utf-8")
            (case_dir / "head" / "tests" / "app.test.ts").write_text("export const t = 1;\n", encoding="utf-8")
            (case_dir / "head" / "scripts" / "compute-build-hash.ts").write_text(
                "import crypto from 'node:crypto';\n"
                "export const h = crypto.createHash('sha256');\n",
                encoding="utf-8",
            )

            # Non-test files inside tests/fixtures/
            (repo_fix / "tests" / "fixtures" / "payload.json").write_text('{"ok": true}\n', encoding="utf-8")
            (repo_fix / "tests" / "fixtures" / "REVIEW.md").write_text("# Notes\n", encoding="utf-8")
            (repo_fix / "tests" / "fixtures" / "sampleInput.py").write_text(
                "def sample_record() -> dict[str, int]:\n    return {'id': 1}\n",
                encoding="utf-8",
            )

            # Production script whose filename contains 'fixture' + its real unit test
            (repo_fix / "evals" / "materialize_fixture_loader.py").write_text(
                "def load_named_fixture(slug: str) -> str:\n    return slug.strip()\n",
                encoding="utf-8",
            )
            (repo_fix / "tests" / "test_fixture_loader.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self) -> None:\n"
                "        self.assertEqual(1, 1)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_fix, "add fixtures and loader")

            rc_f, out_f, _, _ = run_script(
                "impact_scan.py",
                ["--workdir", str(repo_fix), "--range", "main..pr-branch"],
            )
            self.assertEqual(rc_f, 0)
            f_symbols = out_f.get("symbols", [])
            self.assertIsInstance(f_symbols, list)
            self.assertEqual(
                [str(s.get("name")) for s in f_symbols if isinstance(s, dict)],
                ["load_named_fixture"],
            )
            self.assertEqual(
                out_f.get("related_tests_and_mocks"),
                [
                    {
                        "file": "tests/test_fixture_loader.py",
                        "kind": "test",
                        "reason": "modified_in_diff",
                    }
                ],
            )
            self.assertEqual(
                out_f.get("comparative_files"),
                [
                    {
                        "file": "evals/runner.py",
                        "reason": "sibling_in_directory:evals",
                    }
                ],
            )
            self.assertEqual(out_f.get("build_hash_configs"), [])

    def test_smell_scanner_all_seven_rules_positive_and_negative_with_exact_lines(self) -> None:
        self.assertEqual(len(ALL_RULE_IDS), 7)

        cases: list[tuple[str, str, str, list[tuple[str, int]], str]] = [
            # 1. STRINGIFIED_CODE_OR_EVAL
            (
                "STRINGIFIED_CODE_OR_EVAL",
                "src/themeInline.ts",
                "export function buildScript(applyTheme: () => void): string {\n"
                "  return applyTheme.toString();\n"
                "}\n",
                [("STRINGIFIED_CODE_OR_EVAL", 2)],
                "export function buildQuery(searchParams: URLSearchParams): string {\n"
                "  setTimeout(() => {}, 50);\n"
                "  return searchParams.toString();\n"
                "}\n",
            ),
            # 2. DUPLICATE_OR_OVERLAPPING_LISTENER
            (
                "DUPLICATE_OR_OVERLAPPING_LISTENER",
                "src/embedListener.ts",
                "export function attachEmbed(onMsg: (ev: MessageEvent) => void): void {\n"
                "  window.addEventListener('message', onMsg);\n"
                "}\n",
                [("DUPLICATE_OR_OVERLAPPING_LISTENER", 2)],
                "export function bindLocalClick(btn: HTMLElement, onOpen: () => void): void {\n"
                "  btn.addEventListener('click', onOpen);\n"
                "  window.removeEventListener('message', onOpen as unknown as EventListener);\n"
                "}\n",
            ),
            # 3. MOCK_DEFENSIVE_GUARD
            (
                "MOCK_DEFENSIVE_GUARD",
                "src/badgeGuard.ts",
                "export function activateBadge(badgeEl: HTMLElement): void {\n"
                "  if (typeof badgeEl.classList?.add === 'function') {\n"
                "    badgeEl.classList.add('active');\n"
                "  }\n"
                "}\n",
                [("MOCK_DEFENSIVE_GUARD", 2)],
                "export function activateBadgeClean(badgeEl: HTMLElement): void {\n"
                "  if (typeof window !== 'undefined' && typeof document.querySelector === 'function') {\n"
                "    badgeEl.classList.add('active');\n"
                "  }\n"
                "}\n",
            ),
            # 4a. TYPE_BYPASS_OR_SILENT_FALLBACK (TypeScript)
            (
                "TYPE_BYPASS_OR_SILENT_FALLBACK",
                "src/typeBypass.ts",
                "export function coerceAmount(input: unknown): number {\n"
                "  // @ts-ignore\n"
                "  return (input as any) + 1;\n"
                "}\n",
                [("TYPE_BYPASS_OR_SILENT_FALLBACK", 2), ("TYPE_BYPASS_OR_SILENT_FALLBACK", 3)],
                "export function coerceAmountSafe(input: unknown): number {\n"
                "  // @ts-expect-error intentional narrow check in caller\n"
                "  const narrowed = input as number;\n"
                "  return narrowed + 1;\n"
                "}\n",
            ),
            # 4b. TYPE_BYPASS_OR_SILENT_FALLBACK (Python)
            (
                "TYPE_BYPASS_OR_SILENT_FALLBACK",
                "src/typeBypass.py",
                "def coerce_amount(val: object) -> int:\n"
                "    # type: ignore\n"
                "    return val + 1  # type: ignore\n",
                [("TYPE_BYPASS_OR_SILENT_FALLBACK", 2), ("TYPE_BYPASS_OR_SILENT_FALLBACK", 3)],
                "def coerce_amount_safe(flags: list[bool]) -> dict[str, bool]:\n"
                "    ok = flags[0]  # type: ignore [index]\n"
                "    return {'valid': any(flags), 'first': ok}\n",
            ),
            # 5. EMPTY_OR_BROAD_CATCH
            (
                "EMPTY_OR_BROAD_CATCH",
                "src/swallowError.py",
                "def sync_orders() -> None:\n"
                "    try:\n"
                "        raise ValueError('bad')\n"
                "    except Exception:\n"
                "        pass\n",
                [("EMPTY_OR_BROAD_CATCH", 4)],
                "def sync_orders_safe() -> None:\n"
                "    try:\n"
                "        raise ValueError('bad')\n"
                "    except ValueError as exc:\n"
                "        raise RuntimeError('sync failed') from exc\n",
            ),
            # 6. DEEP_NESTING_OR_COMPLEX_FLOW
            (
                "DEEP_NESTING_OR_COMPLEX_FLOW",
                "src/complexFlow.ts",
                "export function classifyScore(score: number, items: Array<{ id: string }>): string {\n"
                "                    if (score > 0) {\n"
                "                      void score;\n"
                "                    }\n"
                "  const map = items.reduce((acc, x) => ({ ...acc, [x.id]: true }), {});\n"
                "  void map;\n"
                "  return score > 90 ? 'gold' : score > 70 ? 'silver' : 'bronze';\n"
                "}\n",
                [
                    ("DEEP_NESTING_OR_COMPLEX_FLOW", 2),
                    ("DEEP_NESTING_OR_COMPLEX_FLOW", 5),
                    ("DEEP_NESTING_OR_COMPLEX_FLOW", 7),
                ],
                "type Unwrap<T> = T extends Promise<infer U> ? U extends Array<infer V> ? V : U : T;\n"
                "export function classifySimple(score: number, nums: number[]): string {\n"
                "                if (score > 0) {\n"
                "                  void score;\n"
                "                }\n"
                "  const total = nums.reduce((acc, n) => acc + n, 0);\n"
                "  return total > 90 ? 'gold' : 'standard';\n"
                "}\n",
            ),
            # 7. MISLEADING_OR_PASS_THROUGH
            (
                "MISLEADING_OR_PASS_THROUGH",
                "src/misleadingGetter.ts",
                "export async function getAccountLabel(id: string): Promise<string> {\n"
                "  await auditRepo.bulkCreate([{ id }]);\n"
                "  return id;\n"
                "}\n"
                "export function wrapFormat(v: string) { return renderText(v); }\n",
                [("MISLEADING_OR_PASS_THROUGH", 1), ("MISLEADING_OR_PASS_THROUGH", 5)],
                "export function isPositiveCount(count: number): boolean {\n"
                "  return count > 0;\n"
                "}\n"
                "export async function persistCount(count: number): Promise<void> {\n"
                "  await fetch('/api/counts', { method: 'POST', body: String(count) });\n"
                "}\n"
                "export function wrapFormatScaled(v: number) { return renderText(v * 100); }\n",
            ),
        ]

        covered_rules = {rule_id for rule_id, _, _, _, _ in cases}
        self.assertEqual(covered_rules, set(ALL_RULE_IDS))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            for idx, (rule_id, rel_file, pos_code, expected_pos, neg_code) in enumerate(cases):
                with self.subTest(rule_id=rule_id):
                    pos_dir = root / f"pos_{idx}"
                    pos_path = pos_dir / rel_file
                    pos_path.parent.mkdir(parents=True, exist_ok=True)
                    pos_path.write_text(pos_code, encoding="utf-8")

                    pos_findings, pos_skip = scan_single_file(rel_file, pos_path, None, 50)
                    self.assertIsNone(pos_skip)
                    self.assertEqual(
                        [(str(f["rule_id"]), int(f["line"])) for f in pos_findings],
                        expected_pos,
                    )

                    neg_dir = root / f"neg_{idx}"
                    neg_path = neg_dir / rel_file
                    neg_path.parent.mkdir(parents=True, exist_ok=True)
                    neg_path.write_text(neg_code, encoding="utf-8")

                    neg_findings, neg_skip = scan_single_file(rel_file, neg_path, None, 50)
                    self.assertIsNone(neg_skip)
                    self.assertEqual(neg_findings, [])

                    # Also verify CLI git range mode only flags added lines for positive vs negative commits
                    repo = root / f"repo_{idx}"
                    init_git_repo(repo)
                    target = repo / rel_file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(neg_code, encoding="utf-8")
                    git_commit_all(repo, "clean base")

                    run_git(repo, ["checkout", "-b", "neg-check"])
                    target.write_text(neg_code + "\n", encoding="utf-8")
                    git_commit_all(repo, "benign touch")
                    rc_neg, out_neg, _, _ = run_script(
                        "scan_diff_smells.py",
                        ["--workdir", str(repo), "--range", "main..neg-check"],
                    )
                    self.assertEqual(rc_neg, 0)
                    self.assertEqual(out_neg.get("findings"), [])

                    run_git(repo, ["checkout", "main"])
                    run_git(repo, ["checkout", "-b", "pos-check"])
                    target.write_text(pos_code, encoding="utf-8")
                    git_commit_all(repo, "introduce smell")
                    rc_pos, out_pos, _, _ = run_script(
                        "scan_diff_smells.py",
                        ["--workdir", str(repo), "--range", "main..pos-check"],
                    )
                    self.assertEqual(rc_pos, 0)
                    cli_findings = out_pos.get("findings", [])
                    self.assertIsInstance(cli_findings, list)
                    self.assertEqual(
                        [
                            (str(f.get("rule_id")), int(f.get("line", 0)))
                            for f in cli_findings
                            if isinstance(f, dict)
                        ],
                        expected_pos,
                    )

    def test_smell_scanner_boundary_max_findings_cap_and_zero_added_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            # --- Boundary 1: Reaching candidate cap (scan_single_file & --max-findings in session mode) ---
            many_smells_code = (
                "export function noisyModule(el: HTMLElement, raw: unknown): void {\n"
                "  const a = raw as any;\n"
                "  const b = raw as any;\n"
                "  window.addEventListener('message', () => {});\n"
                "  try { el.click(); } catch (e) {}\n"
                "  if (typeof el.classList?.add === 'function') { el.classList.add(String(a ?? b)); }\n"
                "}\n"
            )
            cap_file = root / "noisy.ts"
            cap_file.write_text(many_smells_code, encoding="utf-8")

            zero_cap_findings, _ = scan_single_file("src/noisy.ts", cap_file, None, 0)
            self.assertEqual(zero_cap_findings, [])

            capped_three, skip_three = scan_single_file("src/noisy.ts", cap_file, None, 3)
            self.assertIsNone(skip_three)
            self.assertEqual(
                [(str(f["rule_id"]), int(f["line"])) for f in capped_three],
                [
                    ("TYPE_BYPASS_OR_SILENT_FALLBACK", 2),
                    ("TYPE_BYPASS_OR_SILENT_FALLBACK", 3),
                    ("DUPLICATE_OR_OVERLAPPING_LISTENER", 4),
                ],
            )

            repo_cap = root / "repo_cap"
            init_git_repo(repo_cap)
            (repo_cap / "src").mkdir()
            (repo_cap / "tests").mkdir()
            (repo_cap / "src" / "noisy.ts").write_text("export const clean = 1;\n", encoding="utf-8")
            (repo_cap / "tests" / "test_noisy.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self) -> None:\n"
                "        self.assertEqual(1, 1)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_cap, "base")
            run_git(repo_cap, ["checkout", "-b", "pr-cap"])
            (repo_cap / "src" / "noisy.ts").write_text(many_smells_code, encoding="utf-8")
            git_commit_all(repo_cap, "add 5 smells")

            rc_p1, prep1, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_cap), "--range", "main..pr-cap"],
                env=env,
            )
            self.assertEqual(rc_p1, 0)
            sdir1 = Path(str(prep1["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir1)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir1), "--verdict", "OK", "--verified-tests", "tests/test_noisy.py"],
                    env=env,
                )[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir1)], env=env)[0], 0)

            rc_sm_cap, sm_cap_out, _, _ = run_script(
                "scan_diff_smells.py",
                ["--session", str(sdir1), "--max-findings", "2"],
                env=env,
            )
            self.assertEqual(rc_sm_cap, 0)
            cap_findings = sm_cap_out.get("findings", [])
            self.assertIsInstance(cap_findings, list)
            self.assertEqual(len(cap_findings), 2)
            summary_cap = sm_cap_out.get("summary_by_rule", {})
            self.assertIsInstance(summary_cap, dict)
            self.assertEqual(sum(int(v) for v in summary_cap.values() if isinstance(v, int)), 2)

            state1 = json.loads((sdir1 / "gate_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state1["stage3"]["smell_count"], 2)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir1)], env=env)[0], 0)

            # --- Boundary 2: Zero added lines (allowed_lines=set() and deletion-only commit) ---
            empty_allowed_findings, empty_skip = scan_single_file("src/noisy.ts", cap_file, set(), 50)
            self.assertIsNone(empty_skip)
            self.assertEqual(empty_allowed_findings, [])

            repo_del = root / "repo_del"
            init_git_repo(repo_del)
            (repo_del / "src").mkdir()
            (repo_del / "tests").mkdir()
            (repo_del / "src" / "legacySmelly.ts").write_text(
                "export function keepExisting(x: unknown): number {\n"
                "  const unusedDeadLine = 42;\n"
                "  try { return (x as any) + 1; } catch (e) {}\n"
                "  return 0;\n"
                "}\n",
                encoding="utf-8",
            )
            (repo_del / "tests" / "test_legacy.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self) -> None:\n"
                "        self.assertEqual(1, 1)\n",
                encoding="utf-8",
            )
            git_commit_all(repo_del, "base with preexisting smells")
            run_git(repo_del, ["checkout", "-b", "pr-delete-only"])
            # Delete `const unusedDeadLine = 42;` ONLY (0 '+' lines in diff, preexisting smells untouched)
            (repo_del / "src" / "legacySmelly.ts").write_text(
                "export function keepExisting(x: unknown): number {\n"
                "  try { return (x as any) + 1; } catch (e) {}\n"
                "  return 0;\n"
                "}\n",
                encoding="utf-8",
            )
            git_commit_all(repo_del, "delete dead line only")

            rc_p2, prep2, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo_del), "--range", "main..pr-delete-only"],
                env=env,
            )
            self.assertEqual(rc_p2, 0)
            sdir2 = Path(str(prep2["session_dir"]))
            self.assertEqual(run_script("impact_scan.py", ["--session", str(sdir2)], env=env)[0], 0)
            self.assertEqual(
                run_script(
                    "prepare_worktree.py",
                    ["gate-stage1", "--session", str(sdir2), "--verdict", "OK", "--verified-tests", "tests/test_legacy.py"],
                    env=env,
                )[0],
                0,
            )
            self.assertEqual(run_script("run_checks.py", ["--session", str(sdir2)], env=env)[0], 0)

            rc_sm_del, sm_del_out, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir2)], env=env)
            self.assertEqual(rc_sm_del, 0)
            self.assertEqual(sm_del_out.get("findings"), [])
            self.assertEqual(
                sm_del_out.get("summary_by_rule"),
                {rid: 0 for rid in ALL_RULE_IDS},
            )

            # Because smell_count == 0 on 0 added lines, Stage 4 accepts an empty draft without dismissed_smells
            draft2 = Path(str(prep2["draft_findings_path"]))
            draft2.write_text("[]\n", encoding="utf-8")
            rc_s4_ok, _, _, _ = run_script(
                "prepare_worktree.py",
                ["prepare-stage4", "--session", str(sdir2), "--draft", str(draft2)],
                env=env,
            )
            self.assertEqual(rc_s4_ok, 0)
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir2)], env=env)[0], 0)

    def test_non_ascii_japanese_filenames_across_prepare_impact_and_smells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_root = root / "sessions"
            env = {"YHARUTO_SESSION_ROOT": str(sess_root), "YOMIYASU_LINT_DISABLE": "1"}

            repo = root / "repo_jp"
            init_git_repo(repo)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "README.md").write_text("# JP Filename Test\n", encoding="utf-8")
            git_commit_all(repo, "base")

            run_git(repo, ["checkout", "-b", "pr-jp"])
            (repo / "src" / "割引計算.py").write_text(
                "def calc_discount_jpy(amount: int) -> int:\n"
                "    try:\n"
                "        return max(0, amount // 10)\n"
                "    except Exception:\n"
                "        pass\n"
                "    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_割引計算.py").write_text(
                "import unittest\n"
                "from src.割引計算 import calc_discount_jpy\n\n"
                "class TestJpDiscount(unittest.TestCase):\n"
                "    def test_basic(self) -> None:\n"
                "        self.assertEqual(calc_discount_jpy(200), 20)\n",
                encoding="utf-8",
            )
            git_commit_all(repo, "add Japanese-named Python module and test")

            rc_prep, prep, _, stderr_prep = run_script(
                "prepare_worktree.py",
                ["prepare", "--repo", str(repo), "--range", "main..pr-jp"],
                env=env,
            )
            self.assertEqual(rc_prep, 0, stderr_prep)
            self.assertEqual(prep["changed_files"], ["src/割引計算.py", "tests/test_割引計算.py"])
            self.assertEqual(prep["testable_code_files"], ["src/割引計算.py"])

            sdir = Path(str(prep["session_dir"]))
            rc_imp, imp_out, _, _ = run_script("impact_scan.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_imp, 0)
            self.assertEqual(
                imp_out.get("related_tests_and_mocks"),
                [{"file": "tests/test_割引計算.py", "kind": "test", "reason": "modified_in_diff"}],
            )

            rc_g1, _, _, _ = run_script(
                "prepare_worktree.py",
                ["gate-stage1", "--session", str(sdir), "--verdict", "OK", "--verified-tests", "tests/test_割引計算.py"],
                env=env,
            )
            self.assertEqual(rc_g1, 0)

            rc_ci, ci_out, _, _ = run_script("run_checks.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_ci, 0)
            self.assertEqual(ci_out["overall_status"], "passed")

            rc_sm, smells_out, _, _ = run_script("scan_diff_smells.py", ["--session", str(sdir)], env=env)
            self.assertEqual(rc_sm, 0)
            smell_items = smells_out.get("findings", [])
            self.assertIsInstance(smell_items, list)
            self.assertEqual(
                [
                    (str(item.get("file")), int(item.get("line", 0)), str(item.get("rule_id")))
                    for item in smell_items
                    if isinstance(item, dict)
                ],
                [("src/割引計算.py", 4, "EMPTY_OR_BROAD_CATCH")],
            )
            self.assertEqual(run_script("prepare_worktree.py", ["cleanup", "--session", str(sdir)], env=env)[0], 0)


if __name__ == "__main__":
    unittest.main()
