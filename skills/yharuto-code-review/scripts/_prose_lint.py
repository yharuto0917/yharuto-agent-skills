#!/usr/bin/env python3
"""Portable zero-dependency Japanese prose & review style linter with optional yomiyasu integration."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _lib import emit_json, parse_fenced_blocks  # noqa: E402

LINT_TIMEOUT_SECONDS = 15

ALLOWED_VERDICTS: tuple[str, ...] = (
    "🛑 Test Gate NG（テスト修正差し戻し）",
    "🔴 Request changes",
    "🟡 Comment",
    "🟢 Approve",
    "🔴 要修正（重大な懸念あり）",
    "🟡 要確認（構造・保守上の懸念あり）",
    "🟢 健全（重大な懸念なし）",
)

EXEMPT_VERDICT_RE = re.compile(
    r"^## レビュー判定: (" + "|".join(re.escape(v) for v in ALLOWED_VERDICTS) + r")$"
)

EXEMPT_FINDING_RE = re.compile(
    r"^### (🔴 Bug|🟡 Risk|🔵 Nit|❓ Question|🛑 Test Issue): [^\U0001F000-\U0001FAFF\u2600-\u27BF]+$"
)

EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u231A-\u23FA\u2B50\u2B55]")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->")
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
MD_LINK_EXTRACT_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
CODE_LIKE_LABEL_RE = re.compile(
    r"^[A-Za-z0-9_./\\-]+(?::L?\d+(?:-L?\d+)?)?$"
)
ALERT_TAG_RE = re.compile(r"^>\s*\[![A-Z]+\]\s*$")

BUILTIN_PROSE_RULES: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "metaphor_verb",
        "warn",
        re.compile(
            r"(?:静かに(?:壊れ|落ち|失敗|沈黙)"
            r"|黙って(?:失敗|無視|捨て|スキップ|破棄)"
            r"|壊れ(?:る|ます|た|ました|て|ない|かね|やす)"
            r"|潰(?:す|し|さ|せ|され)"
            r"|握り(?:つぶ|潰)(?:す|し|さ|せ|され))"
        ),
        "比喩動詞（「壊れる」「静かに壊れる」「黙って失敗」「潰す」「握りつぶす」等）を避け、具体的な挙動を記述してください。",
    ),
    (
        "personification_or_calque",
        "warn",
        re.compile(r"(?:事情を知りすぎ|偽緑|根因)"),
        "不自然な擬人化・英語直訳語（「事情を知りすぎ」「偽緑」「根因」）を避け、自然な日本語に置き換えてください。",
    ),
    (
        "ai_verbose_expression",
        "warn",
        re.compile(
            r"(?:ことが可能(?:です|だ|である|となり|になり|な)?"
            r"|を行うことができ(?:ます|る|ました|た)"
            r"|多角的な(?:観点|視点)(?:から)?"
            r"|整合性を担保"
            r"|検証を実施"
            r"|ファンクション)"
        ),
        "AI特有の冗長・硬直表現（「〜することが可能です」「〜を行うことができます」「多角的な観点から」「整合性を担保」「検証を実施」「ファンクション」）を簡潔な表現に改めてください。",
    ),
    (
        "bare_katakana_gate",
        "warn",
        re.compile(
            r"(?:(?<![ァ-ヴー])|(?<=レビュー)|(?<=リリース)|(?<=チェック)|(?<=デプロイ)|(?<=マージ)|(?<=テスト)|(?<=品質)|(?<=検証))"
            r"(?<!搭乗)(?<!改札)ゲート(?!ウ[ェエ]イ|ボール|キーパー)"
        ),
        "地の文での素のカタカナ「ゲート」は避け、「関門」「検査」「判定」等の日本語にするか固有名はバッククォートで囲んでください。",
    ),
    (
        "preamble_filler",
        "warn",
        re.compile(
            r"(?:(?:重要|大切|大事)なのは"
            r"|結論から(?:言|い)(?:う|い|え)|結論から申し上げ"
            r"|特筆すべき(?:は|点|こと)"
            r"|言うまでもなく)"
        ),
        "前置きフィラー（「重要なのは」「結論から言うと」「特筆すべきは」「言うまでもなく」）を削除し、本題から記述してください。",
    ),
    (
        "decorative_dash",
        "warn",
        re.compile(r"(?:\s[\u2014\u2013]\s|[\u2014\u2015]{2,})"),
        "装飾ダッシュ（「 — 」「――」）の使用を避け、読点や括弧・句点で構造化してください。",
    ),
)

PLAIN_FORM_CLAUSE_END_RE = re.compile(
    r"(?:である|(?<![ァ-ヴーA-Za-z0-9])だ|ではない|だった"
    r"|(?<!まし)(?<!でし)た"
    r"|ない"
    r"|(?:する|なる|れる|られる|いる|ある|くる|おく|いく|[ぁ-ん]る))$"
)
TRAILING_CLOSERS_RE = re.compile(r"[）)」』\"']+$")


def is_exempt_emoji_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    return (
        EXEMPT_VERDICT_RE.fullmatch(stripped) is not None
        or EXEMPT_FINDING_RE.fullmatch(stripped) is not None
    )


def discover_yomiyasu_lint(
    explicit_path: str | Path | None = None,
    *,
    strict_explicit: bool = False,
) -> Path | None:
    """Locate optional external yomiyasu_lint.py using explicit flag, env var, or Path.home()."""
    disable_flag = os.environ.get("YOMIYASU_LINT_DISABLE", "").strip().lower()
    if disable_flag in {"1", "true", "yes", "on"}:
        return None

    if explicit_path is not None and str(explicit_path).strip():
        cand = Path(explicit_path).expanduser().resolve()
        if cand.is_file():
            return cand
        if strict_explicit:
            raise FileNotFoundError(f"Specified --yomiyasu-lint path does not exist: {cand}")
        return None

    env_path = os.environ.get("YOMIYASU_LINT_PATH", "").strip()
    if env_path:
        env_cand = Path(env_path).expanduser().resolve()
        if env_cand.is_file():
            return env_cand
        if strict_explicit:
            raise FileNotFoundError(f"YOMIYASU_LINT_PATH does not exist: {env_cand}")

    home = Path.home()
    candidates = (
        home / ".gemini" / "config" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        home / ".agents" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        home / ".claude" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        home / ".codex" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
    )
    for item in candidates:
        if item.is_file():
            return item.resolve()
    return None


def mask_inline_code_and_links(raw_line: str) -> str:
    """Mask inline code spans and Markdown link targets while preserving visible Japanese text."""
    without_comments = HTML_COMMENT_RE.sub(" ", raw_line)
    masked_code = INLINE_CODE_RE.sub("‹CODE›", without_comments)

    def _replace_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        if not label or CODE_LIKE_LABEL_RE.match(label):
            return "‹LINK›"
        return label

    return MD_LINK_EXTRACT_RE.sub(_replace_link, masked_code)


def _check_plain_form_sentences(visible_line: str) -> str | None:
    """Return offending plain-form clause snippet if a polite-mode sentence ends in plain form."""
    parts = visible_line.split("。")
    # Only parts[:-1] actually ended with '。'
    for raw_clause in parts[:-1]:
        clause = raw_clause.strip()
        if not clause:
            continue
        cleaned = TRAILING_CLOSERS_RE.sub("", clause).strip()
        if not cleaned:
            continue
        if PLAIN_FORM_CLAUSE_END_RE.search(cleaned) is not None:
            return clause[-40:] + "。"
    return None


def lint_prose_builtin(
    review_text: str,
    *,
    lines: Sequence[str] | None = None,
    fenced_lines: set[int] | None = None,
    doc_type: str = "pr",
    exempt_review_headings: bool = True,
) -> list[dict[str, object]]:
    """Run zero-dependency built-in Japanese prose & review formatting checks."""
    doc_lines = list(lines) if lines is not None else review_text.splitlines()
    if fenced_lines is None:
        _, protected_lines = parse_fenced_blocks(doc_lines)
    else:
        protected_lines = fenced_lines

    findings: list[dict[str, object]] = []

    for idx_zero, raw_line in enumerate(doc_lines):
        line_no = idx_zero + 1
        if line_no in protected_lines:
            continue

        stripped = raw_line.strip()
        if not stripped:
            continue

        is_exempt_heading = exempt_review_headings and is_exempt_emoji_line(stripped)
        visible = mask_inline_code_and_links(raw_line)
        visible_stripped = visible.strip()

        # 1. Emoji restriction
        if not is_exempt_heading and EMOJI_RE.search(visible) is not None:
            findings.append(
                {
                    "line": line_no,
                    "severity": "warn",
                    "rule": "emoji_prohibited",
                    "message": "許可された判定・指摘見出し以外での絵文字使用は禁止されています。",
                    "snippet": stripped[:100],
                }
            )

        # 2. Built-in vocabulary, filler, and dash patterns
        for rule_id, severity, pattern, msg in BUILTIN_PROSE_RULES:
            m = pattern.search(visible)
            if m is not None:
                findings.append(
                    {
                        "line": line_no,
                        "severity": severity,
                        "rule": rule_id,
                        "message": f"{msg}（検出: '{m.group(0)}'）",
                        "snippet": stripped[:100],
                    }
                )

        # 3. Trailing colon at end of prose/bullet line
        is_heading = stripped.startswith("#")
        is_table = stripped.startswith("|")
        is_alert_tag = ALERT_TAG_RE.match(stripped) is not None
        if not is_heading and not is_table and not is_alert_tag:
            if re.search(r"[:：]\s*$", visible_stripped) is not None:
                findings.append(
                    {
                        "line": line_no,
                        "severity": "warn",
                        "rule": "trailing_colon",
                        "message": "文末コロン（: / ：）は避け、完結した文または見出しで記述してください。",
                        "snippet": stripped[:100],
                    }
                )

        # 4. Polite form consistency (です・ます) for PR review outputs
        if doc_type == "pr" and not is_heading and not is_table and not is_alert_tag:
            if not (stripped.startswith("> **") and stripped.endswith("**")):
                bad_clause = _check_plain_form_sentences(visible_stripped)
                if bad_clause is not None:
                    findings.append(
                        {
                            "line": line_no,
                            "severity": "warn",
                            "rule": "style_mix_plain_form",
                            "message": f"レビュー出力の文体は丁寧語（です・ます調）に統一してください（常体混入: '{bad_clause}'）。",
                            "snippet": stripped[:100],
                        }
                    )

    return findings


def run_prose_lint(
    review_text: str,
    *,
    explicit_yomiyasu_path: str | Path | None = None,
    lines: Sequence[str] | None = None,
    fenced_lines: set[int] | None = None,
    doc_type: str = "pr",
    reader: str = "expert",
    exempt_review_headings: bool = True,
    strict_explicit: bool = False,
) -> tuple[list[dict[str, object]], str]:
    """Run built-in prose linter and optionally merge external yomiyasu_lint.py findings."""
    doc_lines = list(lines) if lines is not None else review_text.splitlines()
    if fenced_lines is None:
        _, protected_lines = parse_fenced_blocks(doc_lines)
    else:
        protected_lines = fenced_lines

    builtin_findings = lint_prose_builtin(
        review_text,
        lines=doc_lines,
        fenced_lines=protected_lines,
        doc_type=doc_type,
        exempt_review_headings=exempt_review_headings,
    )

    yomiyasu_path = discover_yomiyasu_lint(
        explicit_yomiyasu_path,
        strict_explicit=strict_explicit,
    )
    if yomiyasu_path is None:
        return builtin_findings, "builtin"

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(yomiyasu_path),
                "--json",
                "--doc-type",
                doc_type,
                "--reader",
                reader,
                "-",
            ],
            input=review_text,
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LINT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            [
                {
                    "line": 1,
                    "severity": "error",
                    "rule": "yomiyasu_exec_error",
                    "message": str(exc),
                    "snippet": "",
                }
            ],
            "yomiyasu_error",
        )

    if proc.returncode not in (0, 1):
        return (
            [
                {
                    "line": 1,
                    "severity": "error",
                    "rule": "yomiyasu_exit_error",
                    "message": f"yomiyasu_lint.py exited with {proc.returncode}: {proc.stderr.strip()}",
                    "snippet": "",
                }
            ],
            "yomiyasu_error",
        )

    try:
        lint_obj: object = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return (
            [
                {
                    "line": 1,
                    "severity": "error",
                    "rule": "yomiyasu_json_error",
                    "message": f"Invalid JSON from yomiyasu_lint.py: {exc}",
                    "snippet": "",
                }
            ],
            "yomiyasu_error",
        )

    merged: list[dict[str, object]] = list(builtin_findings)
    seen_line_rules: set[tuple[int, str]] = {
        (int(item["line"]), str(item["rule"]))
        for item in merged
        if isinstance(item.get("line"), int)
    }

    if isinstance(lint_obj, dict) and isinstance(lint_obj.get("findings"), list):
        for entry in lint_obj["findings"]:
            if not isinstance(entry, dict):
                continue
            sev = str(entry.get("severity") or "")
            if sev not in {"error", "warn"}:
                continue
            rule = str(entry.get("rule") or "")
            line_val = entry.get("line", 1)
            line_no = line_val if isinstance(line_val, int) else 1
            raw_line = doc_lines[line_no - 1] if 1 <= line_no <= len(doc_lines) else ""

            if rule == "emoji_prohibited" and exempt_review_headings and is_exempt_emoji_line(raw_line):
                continue
            if (line_no, rule) in seen_line_rules:
                continue
            seen_line_rules.add((line_no, rule))
            merged.append(
                {
                    "line": line_no,
                    "severity": sev,
                    "rule": rule,
                    "message": str(entry.get("message") or ""),
                    "snippet": str(entry.get("snippet") or raw_line.strip()),
                }
            )

    merged.sort(key=lambda item: (int(item["line"]) if isinstance(item.get("line"), int) else 1, str(item.get("rule") or "")))
    return merged, "builtin+yomiyasu_lint"


def check_review_prose(
    review_text: str,
    explicit_yomiyasu_path: str | Path | None = None,
) -> tuple[bool, list[dict[str, object]], str]:
    """Convenience helper returning (passed, findings, backend)."""
    findings, backend = run_prose_lint(
        review_text,
        explicit_yomiyasu_path=explicit_yomiyasu_path,
        doc_type="pr",
        reader="expert",
        exempt_review_headings=True,
    )
    return len(findings) == 0, findings, backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="_prose_lint.py",
        description="Portable Japanese prose & review linter (built-in + optional yomiyasu_lint.py).",
    )
    parser.add_argument("target", nargs="?", default="-", help="Markdown file path or '-' for stdin.")
    parser.add_argument("--doc-type", default="pr", help="Document type (default: pr).")
    parser.add_argument("--reader", default="expert", help="Reader level (default: expert).")
    parser.add_argument("--yomiyasu-lint", default=None, help="Explicit path to yomiyasu_lint.py.")
    parser.add_argument("--no-exempt-headings", action="store_true", help="Disable canonical review heading emoji exemption.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when any error or warn finding is present.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.target == "-":
        text = sys.stdin.read()
    else:
        tpath = Path(str(args.target)).expanduser().resolve()
        if not tpath.is_file():
            emit_json({"status": "error", "error": f"File not found: {tpath}", "exit_code": 2})
            return 2
        text = tpath.read_text(encoding="utf-8")

    try:
        findings, backend = run_prose_lint(
            text,
            explicit_yomiyasu_path=args.yomiyasu_lint,
            doc_type=str(args.doc_type),
            reader=str(args.reader),
            exempt_review_headings=not bool(args.no_exempt_headings),
            strict_explicit=True,
        )
    except FileNotFoundError as exc:
        emit_json({"status": "error", "error": str(exc), "exit_code": 2})
        return 2

    if args.json:
        emit_json(
            {
                "passed": len(findings) == 0,
                "backend": backend,
                "finding_count": len(findings),
                "findings": findings,
            }
        )
    else:
        for f in findings:
            sys.stdout.write(f"L{f['line']} [{f['severity']}:{f['rule']}] {f['message']}\n")
        if not findings:
            sys.stdout.write(f"OK ({backend}: 0 findings)\n")

    if (args.strict or args.json) and len(findings) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
