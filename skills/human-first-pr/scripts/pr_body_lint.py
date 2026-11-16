#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pr_body_lint.py - Gate 3 完了前の Human-First PR 本文検証 CLI。

TL;DR 長さ・Size別禁止要素・Scope バナー・AI クリシェ・削減自慢・
Mermaid GFM 構文とノード数・Reading Order カバレッジ・依存名言及・
検証チェックボックス・絵文字ハイジーン・テンプレート整合性・分類ラベル箇条書き・
補助動詞漢字を検査し、任意で yomiyasu_lint.py と連携する。
Python 3.9+ 標準ライブラリのみで動作する。
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True

import argparse
import errno
import json
import os
import re
import subprocess
import traceback
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Set, Tuple

__version__ = "1.0.0"
SCHEMA_VERSION = 1

STANDARD_CLASSDEFS: Dict[str, Dict[str, str]] = {
    "added": {"fill": "#dcfce7", "stroke": "#16a34a"},
    "modified": {"fill": "#fef9c3", "stroke": "#ca8a04"},
    "risk": {"fill": "#fee2e2", "stroke": "#dc2626"},
    "base": {"fill": "#f3f4f6", "stroke": "#6b7280"},
}

MERMAID_RESERVED_IDS: Set[str] = {
    "end",
    "class",
    "subgraph",
    "graph",
    "flowchart",
    "style",
    "click",
    "default",
    "direction",
    "linkstyle",
    "classdef",
}

SPECIAL_MERMAID_CHARS_RE = re.compile(r"[/()\[\]{}:;#&<>@]")

# 許可バッジ4種: 🎯 (U+1F3AF), 🧠 (U+1F9E0), 👀 (U+1F440), ⏭️ (U+23ED U+FE0F?)
ALLOWED_BADGE_EMOJIS: Set[str] = {"\U0001f3af", "\U0001f9e0", "\U0001f440", "\u23ed", "\u23ed\ufe0f"}
TEXT_BADGE_RE = re.compile(r"\[(?:SCOPE|TARGET|MUST|SKIM|SKIP)\]")

# Unicode 絵文字検出（◎ ○ △ × → ← ↑ ↓ ✓ 等は誤検出しない）
EMOJI_RE = re.compile(
    "(?:"
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\ufe0f?\u20e3"
    r"|[\U0001F300-\U0001FAFF](?:\ufe0f|\U0001F3FB-\U0001F3FF)?(?:\u200d[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?)*"
    r"|[\u231A-\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA]\ufe0f?"
    r"|[\u2600-\u2604\u260E\u2611\u2614-\u2615\u2618\u261D\u2620\u2622-\u2623\u2626\u262A\u262E-\u262F\u2638-\u263A\u2640\u2642\u2648-\u2653\u265F-\u2660\u2663\u2665-\u2666\u2668\u267B\u267E-\u267F\u2692-\u2697\u2699\u269B-\u269C\u26A0-\u26A1\u26A7\u26AA-\u26AB\u26B0-\u26B1\u26BD-\u26BE\u26C4-\u26C5\u26C8\u26CE-\u26CF\u26D1\u26D3-\u26D4\u26E9-\u26EA\u26F0-\u26F5\u26F7-\u26FA\u26FD]\ufe0f?"
    r"|[\u2702\u2705\u2708-\u270D\u270F\u2712\u2714\u2716\u271D\u2721\u2728\u2733-\u2734\u2744\u2747\u274C\u274E\u2753-\u2755\u2757\u2763-\u2764\u2795-\u2797\u27A1\u27B0\u27BF]\ufe0f?"
    r"|[\u2B1B-\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299]\ufe0f?"
    r"|[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9-\u21AA\u24C2\u25AA-\u25AB\u25B6\u25C0\u25FB-\u25FE\u2934-\u2935\u2B05-\u2B07]\ufe0f"
    ")"
)

AI_CLICHE_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"堅牢性[をが](?:向上|強化|高め)"), "「堅牢性を向上」を禁止。防ぐ異常条件・リトライ上限・返却コードを書いてください。"),
    (re.compile(r"(?:安定性|信頼性)[をが](?:向上|強化|高め)"), "「安定性/信頼性を向上」を禁止。具体的な失敗時の挙動を書いてください。"),
    (re.compile(r"クリーンで保守性(?:の高い|に優れた)?|(?:保守性|可読性|メンテナンス性)[をが](?:向上|改善|高め)"), "主観的評価語を禁止。分割した責務・廃止した重複・抽出した型名を書いてください。"),
    (re.compile(r"シームレス(?:に|な)(?:統合|連携|移行|動作)?|スムーズ(?:に|な)(?:統合|連携)"), "「シームレス/スムーズ」を禁止。境界インターフェースやエラー伝播仕様を書いてください。"),
    (re.compile(r"包括的なエラーハンドリング|適切に(?:処理|ハンドリング|対応)(?:し|する|した|され)"), "「包括的/適切に処理」を禁止。捕捉する例外型・ログ出力・返却ステータスを書いてください。"),
    (re.compile(r"(?:大幅|劇的|飛躍的)に(?:最適化|高速化|改善)"), "誇張表現を禁止。解消手法（N+1 解消等）と計測条件・実測値のみ書いてください。"),
    (re.compile(r"将来の拡張性(?:を見据え|を考慮し)|柔軟な(?:対応|設計|拡張)(?:が可能|を実現)?"), "「柔軟/将来の拡張性」を禁止。現在の設定項目・具象インターフェース名で書いてください。"),
    (re.compile(r"エッジケースを網羅|(?:リスク|エラー|バグ|不具合)を未然に防[ぎぐ]"), "「網羅/未然に防ぐ」を禁止。検証した境界条件（空配列・0件・UTF-8 等）を列挙してください。"),
    (re.compile(r"という挙動を(?:排除|防止|阻止)|静かに壊れ|安全側に倒[すし]"), "比喩/抽象回避を禁止。中断条件と送出例外・記録先テーブルを書いてください。"),
    (re.compile(r"(?:開発者体験|DX)[のを通]?(?:向上|改善)|直感的な(?:API|設計|インターフェース)"), "「DX/直感的」を禁止。変更した型定義・引数・エラーメッセージ改善を書いてください。"),
    (re.compile(r"(?:全体の)?(?:整合性|一貫性)[をが](?:担保|確保)"), "「整合性を担保」を禁止。トランザクション境界・一意制約・検証ルールを書いてください。"),
    (
        re.compile(
            r"\b(?:robust(?:ness)?|seamless(?:ly)?|comprehensive error handling|clean and maintainable|streamline[sd]?|leverage[sd]?|future[- ]proof(?:ing)?)\b",
            re.IGNORECASE,
        ),
        "英語 AI クリシェを禁止。具体的な入力・例外・契約変化に書き換えてください。",
    ),
)

REDUCTION_BOAST_PROSE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:AIスロップ|スロップ|冗長(?:な)?(?:防衛)?コード|重複コード|不要(?:な)?コード|Whatコメント).*?(?:削除|削減|除去|整理|ダイエット)"),
    re.compile(r"コードダイエット(?:の(?:成果|結果))?"),
    re.compile(r"(?:合計?\s*)?\d+\s*行(?:を|の)?ダイエット"),
    re.compile(r"(?:初回実装|当初の実装|リファクタ前|整理前)から.*?(?:削減|減らし)"),
    re.compile(r"スリム化(?:に成功|を達成|しました|した)"),
    re.compile(
        r"\b(?:code diet|removed \d+ lines of (?:ai )?slop|slop reduction|reduced by \d+ lines from initial)\b",
        re.IGNORECASE,
    ),
)

REDUCTION_BOAST_MERMAID_RE = re.compile(r"(?:削減|ダイエット|slop|removed lines)", re.IGNORECASE)

WHAT_COMMENTARY_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:\d+行目|L\d+(?:-L?\d+)?)(?:を|で|に)(?:編集|追加|変更|削除|修正)"),
    re.compile(r"行を(?:編集|追加|変更|削除)"),
    re.compile(r"(?:関数|メソッド|クラス|変数|ファイル)\s*[A-Za-z0-9_.-]+\s*を(?:追加|編集|変更|作成)し"),
    re.compile(r"\bif\s*\([^)]+\)\s*を(?:追加|入れて)"),
    re.compile(r"\b(?:edited line \d+|added (?:function|method|if statement) \w+ and called)\b", re.IGNORECASE),
)

UNVERIFIED_CLAIM_RE = re.compile(
    r"(?:問題ないはず|問題ないと思|通る見込み|通るはず|動くはず|動く見込み|"
    r"目視(?:のみ|で確認|確認)|おそらく(?:通過|成功|問題なし)|たぶん(?:大丈夫|通る)|"
    r"推測|未実行|未検証|確認予定|あとで確認|CI待ち|CI任せ|"
    r"\b(?:should pass|probably works|by inspection|not run|untested|pending CI)\b)",
    re.IGNORECASE,
)

EXEC_EVIDENCE_RE = re.compile(
    r"(?:`[^`]+`|\b(?:pytest|unittest|python3?|npm|pnpm|yarn|bun|cargo|go|ruff|mypy|tsc|eslint|vitest|jest|playwright|make|rspec|phpunit|mvn|gradle)\b)",
    re.IGNORECASE,
)

BOLD_LABEL_COLON_RE = re.compile(
    r"^\s*(?:[-*+]|\d+\.)\s+\*\*([^*]+?)\*\*\s*[:：]\s*(.+)$"
)
BOLD_LABEL_COLON_INSIDE_RE = re.compile(
    r"^\s*(?:[-*+]|\d+\.)\s+\*\*([^*]+?)[:：]\*\*\s*(.+)$"
)

CLASSIFICATION_LABEL_WORDS: Set[str] = {
    "改善点",
    "変更点",
    "変更内容",
    "修正点",
    "修正内容",
    "注意点",
    "補足",
    "背景",
    "目的",
    "課題",
    "対応内容",
    "効果",
    "メリット",
    "リスク",
    "ポイント",
    "詳細",
    "概要",
    "summary",
    "changes",
    "improvements",
    "notes",
    "details",
    "impact",
    "fix",
    "why",
    "what",
}

VALUE_LABEL_WORDS: Set[str] = {
    "対象ファイル",
    "ファイル",
    "対象",
    "ブランチ",
    "ベース",
    "関連issue",
    "関連チケット",
    "依存追加",
    "依存更新",
    "コマンド",
    "実行コマンド",
    "環境",
    "参照",
    "url",
    "path",
    "file",
    "target",
    "branch",
    "issue",
    "status",
    "pr",
    "owner",
}

AUXILIARY_KANJI_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[ぁ-ん](?:事|為)(?=[はがをにで、。のともやへかだな\s）)]|$)"), "形式名詞「事」「為」はひらがな（「こと」「ため」）にしてください。"),
    (re.compile(r"[ぁ-ん](?:下さ|頂)(?:い|き|く|け|いた)"), "補助動詞「下さい」「頂く」はひらがな（「ください」「いただく」）にしてください。"),
    (re.compile(r"(?:て|で|[ぁ-ん][がはもを])出来(?:ます|ません|る|ない|た|ず)|出来(?:ます|ません)"), "補助動詞・可能表現「出来る」はひらがな（「できる」）にしてください。"),
)

CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

TLDR_HEADING_RE = re.compile(r"^(?:\d+\.\s*)?(?:TL;DR|TLDR|概要|要約|Summary|変更概要|Overview)$", re.IGNORECASE)
VALIDATION_HEADING_RE = re.compile(
    r"(?:検証|テスト|確認|Testing|Validation|Verification|Checks?)",
    re.IGNORECASE,
)
FORBIDDEN_SIZE_S_HEADING_RE = re.compile(
    r"(?:アーキテクチャ|振る舞い|エッジケース図|差分トリアージ|読むべき(?:ファイル|コード)|設計判断|Safe-to-Skip|Architecture|Diff Triage|Reading Order|Design Decisions?)",
    re.IGNORECASE,
)
SCOPE_BANNER_RE = re.compile(r"^\s*>\s*(?:🎯\s*)?(?:\*\*)?(?:Scope|\[SCOPE\])(?:\*\*)?(?:[:：\s]|$)", re.IGNORECASE)

STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU: Tuple[str, ...] = (
    "TL;DR",
    "概要",
    "検証ステータス",
    "検証結果",
    "アーキテクチャ比較",
    "振る舞い・エッジケース図解",
    "差分トリアージと読むべきコード",
    "設計判断マトリクス",
    "チェックリスト",
    "メタ情報",
)


def _use_utf8(stream: object, **options: str) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", newline="\n", **options)


def _error(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        _use_utf8(stream, errors="backslashreplace")
        print(message, file=stream)
    except (OSError, ValueError):
        pass


def _closed_output_on_windows(error: Exception) -> bool:
    return (
        os.name == "nt"
        and type(error) is OSError
        and error.errno == errno.EINVAL
        and error.filename is None
        and error.filename2 is None
    )


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        stream = sys.stderr
        if stream is None:
            self.exit(2)
        _use_utf8(stream, errors="backslashreplace")
        self.print_usage(stream)
        stream.write(f"{self.prog}: error: {message}\n")
        self.exit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="pr_body_lint.py",
        description="Human-First PR 本文の構造・Mermaid GFM・Size規約・日本語禁止表現を検証する。",
        allow_abbrev=False,
    )
    parser.add_argument("file", nargs="?", help="PR本文ファイル（省略または '-' で標準入力）")
    parser.add_argument("--size", choices=("S", "M", "L", "XL"), help="PR規模（--triage-json 未指定時は必須）")
    parser.add_argument("--triage-json", help="pr_diff_triage.py の出力 JSON パス")
    parser.add_argument("--template", help=".github/pull_request_template.md 等のパス")
    parser.add_argument(
        "--template-mode",
        choices=("merge", "strict"),
        default="merge",
        help="テンプレート検証モード（既定: merge）",
    )
    parser.add_argument(
        "--badge-mode",
        choices=("auto", "emoji", "text"),
        default="auto",
        help="バッジ表記モード（既定: auto）",
    )
    parser.add_argument("--yomiyasu-lint", action="store_true", help="yomiyasu_lint.py が見つかれば統合実行する")
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    parser.add_argument(
        "--fail-on",
        choices=("fail", "warn", "never"),
        default="fail",
        help="終了コード 1 を返す条件（既定: fail）",
    )
    parser.add_argument("--version", action="store_true", help="バージョンを表示して終了する")
    return parser


def _decode_utf8_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _read_text_input(path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if path is not None and path != "-":
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            return None, f"入力ファイルを開けません ({path}): {exc.strerror or exc}"
        try:
            return _decode_utf8_bytes(raw), None
        except UnicodeDecodeError:
            return None, f"入力ファイルを UTF-8 として読めません ({path})"

    stream = sys.stdin
    if stream is None:
        return None, "標準入力が利用できません"
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            return _decode_utf8_bytes(buf.read()), None
        txt = stream.read()
        return txt.replace("\r\n", "\n").replace("\r", "\n"), None
    except UnicodeDecodeError:
        return None, "標準入力を UTF-8 として読めません"
    except OSError as exc:
        return None, f"標準入力の読み込みに失敗しました: {exc.strerror or exc}"


def _read_optional_file(path: str, label: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, f"{label}を開けません ({path}): {exc.strerror or exc}"
    try:
        return _decode_utf8_bytes(raw), None
    except UnicodeDecodeError:
        return None, f"{label}を UTF-8 として読めません ({path})"


def _mask_inline_code(line: str) -> str:
    """インラインコード (`...` / ``...``) の中身をプレースホルダへ置換し、行構造を保つ。"""
    out: List[str] = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == "`":
            tick_len = 1
            while i + tick_len < n and line[i + tick_len] == "`":
                tick_len += 1
            closer = "`" * tick_len
            end = line.find(closer, i + tick_len)
            if end != -1:
                out.append(" _CODE_ ")
                i = end + tick_len
                continue
        out.append(line[i])
        i += 1
    return "".join(out)


def _preprocess_markdown(text: str) -> Dict[str, Any]:
    """Markdown を行分割し、通常コードブロックと mermaid ブロックを分離・マスクする。"""
    raw_lines = text.split("\n")
    prose_lines: List[str] = []
    non_code_raw_lines: List[str] = []
    mermaid_blocks: List[Dict[str, Any]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    fence_lang = ""
    current_mermaid_lines: List[Tuple[int, str]] = []
    mermaid_start = 0

    for idx, line in enumerate(raw_lines, start=1):
        stripped = line.lstrip()
        if not in_fence:
            m = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                fence_lang = m.group(2).strip().split()[0].lower() if m.group(2).strip() else ""
                prose_lines.append("")
                non_code_raw_lines.append("")
                if fence_lang == "mermaid":
                    current_mermaid_lines = []
                    mermaid_start = idx
                continue
            no_comment = re.sub(r"<!--.*?-->", "", line)
            non_code_raw_lines.append(no_comment)
            prose_lines.append(_mask_inline_code(no_comment))
        else:
            close_m = re.match(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped)
            if close_m:
                if fence_lang == "mermaid":
                    mermaid_blocks.append({
                        "start_line": mermaid_start,
                        "end_line": idx,
                        "lines": current_mermaid_lines,
                    })
                in_fence = False
                fence_char = ""
                fence_len = 0
                fence_lang = ""
            elif fence_lang == "mermaid":
                current_mermaid_lines.append((idx, line))
            prose_lines.append("")
            non_code_raw_lines.append("")

    if in_fence and fence_lang == "mermaid":
        mermaid_blocks.append({
            "start_line": mermaid_start,
            "end_line": len(raw_lines),
            "lines": current_mermaid_lines,
        })

    return {
        "raw_lines": raw_lines,
        "non_code_raw_lines": non_code_raw_lines,
        "prose_lines": prose_lines,
        "mermaid_blocks": mermaid_blocks,
    }


def _extract_headings(non_code_raw_lines: Sequence[str]) -> List[Dict[str, Any]]:
    headings: List[Dict[str, Any]] = []
    for line_no, line in enumerate(non_code_raw_lines, start=1):
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*$", line)
        if m:
            headings.append({
                "line": line_no,
                "level": len(m.group(1)),
                "text": m.group(2).strip(),
                "raw": line,
            })
    return headings


def _extract_tldr_section(
    non_code_raw_lines: Sequence[str],
    headings: Sequence[Dict[str, Any]],
    strict_summary_heading: Optional[str] = None,
) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    """TL;DR セクション開始行と (1-based行番号, 本文行) のリストを返す。"""
    tldr_heading: Optional[Dict[str, Any]] = None
    for h in headings:
        clean_title = re.sub(r"^\d+\.\s*", "", h["text"]).strip()
        if TLDR_HEADING_RE.match(clean_title) or (
            strict_summary_heading and clean_title.lower() == strict_summary_heading.lower()
        ):
            tldr_heading = h
            break

    lines_out: List[Tuple[int, str]] = []
    if tldr_heading is not None:
        start_idx = tldr_heading["line"]  # 1-based, next line is index start_idx
        for idx in range(start_idx, len(non_code_raw_lines)):
            line_no = idx + 1
            raw = non_code_raw_lines[idx]
            if re.match(r"^\s{0,3}#{1,6}\s+", raw):
                break
            if SCOPE_BANNER_RE.match(raw) or raw.strip() == "---":
                break
            stripped = raw.strip()
            if not stripped:
                continue
            if re.match(r"^(?:依存追加|Dependencies|Closes\s+#|Refs\s+#|Fixes\s+#)\s*[:：]?", stripped, re.IGNORECASE):
                continue
            lines_out.append((line_no, stripped))
        return tldr_heading["line"], lines_out

    # 見出しがない場合: 最初の見出し/Scopeバナー前の非空段落を冒頭要約として確認
    first_heading_line = headings[0]["line"] if headings else len(non_code_raw_lines) + 1
    first_para_line: Optional[int] = None
    for idx in range(0, first_heading_line - 1):
        line_no = idx + 1
        raw = non_code_raw_lines[idx]
        if SCOPE_BANNER_RE.match(raw) or raw.strip() == "---":
            break
        stripped = raw.strip()
        if not stripped:
            if lines_out:
                break
            continue
        if re.match(r"^(?:Closes\s+#|Refs\s+#|Fixes\s+#)\s*", stripped, re.IGNORECASE):
            continue
        if first_para_line is None:
            first_para_line = line_no
        lines_out.append((line_no, stripped))
    return first_para_line, lines_out


def _normalize_tldr_chars(tldr_lines: Sequence[Tuple[int, str]]) -> Tuple[str, float]:
    joined = " ".join(text for _, text in tldr_lines)
    # [label](url) -> label, bare URL 除外, バッククォートと太字記号を除去
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", joined)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = cleaned.replace("`", "").replace("**", "").replace("__", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    non_ws = re.sub(r"\s+", "", cleaned)
    if not non_ws:
        return cleaned, 0.0
    cjk_count = len(CJK_CHAR_RE.findall(non_ws))
    return cleaned, cjk_count / len(non_ws)


def _add_finding(
    findings: List[Dict[str, Any]],
    *,
    rule_id: str,
    severity: str,
    line: int,
    snippet: str,
    message: str,
    suggestion: str = "",
) -> None:
    findings.append({
        "rule_id": rule_id,
        "severity": severity,
        "line": max(1, line),
        "snippet": snippet.strip()[:160],
        "message": message,
        "suggestion": suggestion,
    })


def _extract_details_line_ranges(non_code_raw_lines: Sequence[str]) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for idx, line in enumerate(non_code_raw_lines, start=1):
        low = line.lower()
        if "<details" in low and start is None:
            start = idx
        if "</details" in low and start is not None:
            ranges.append((start, idx))
            start = None
    if start is not None:
        ranges.append((start, len(non_code_raw_lines)))
    return ranges


def _line_in_ranges(line_no: int, ranges: Sequence[Tuple[int, int]]) -> bool:
    return any(lo <= line_no <= hi for lo, hi in ranges)


def _detect_mermaid_type(lines: Sequence[Tuple[int, str]]) -> str:
    for _, raw in lines:
        s = raw.strip()
        if not s or s.startswith("%%"):
            continue
        low = s.lower()
        if low.startswith("flowchart"):
            return "flowchart"
        if low.startswith("graph"):
            return "graph"
        if low.startswith("sequencediagram"):
            return "sequenceDiagram"
        if low.startswith("statediagram-v2"):
            return "stateDiagram-v2"
        if low.startswith("statediagram"):
            return "stateDiagram"
        if low.startswith("erdiagram"):
            return "erDiagram"
        if low.startswith("pie"):
            return "pie"
        if low.startswith("xychart-beta"):
            return "xychart-beta"
        return s.split()[0]
    return "unknown"


# 形状括弧は長いものから順に照合する（[(...)] 誤爆回避）
SHAPE_DELIMS: Tuple[Tuple[str, str], ...] = (
    ("(((", ")))"),
    ("([", "])"),
    ("[(", ")]"),
    ("[[", "]]"),
    ("{{", "}}"),
    ("((", "))"),
    ("[/", "/]"),
    ("[\\", "\\]"),
    ("[/", "\\]"),
    ("[\\", "/]"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
)


def _scan_flowchart_line(line: str) -> Tuple[List[str], List[Tuple[str, str]], List[Tuple[str, str]]]:
    """
    flowchart の1行を走査し、
    (出現ノードID一覧, [(種別, クォート状況付きラベル文字列)], [(ノードID, rawテキスト)]) を返す。
    """
    node_ids: List[str] = []
    labels_to_check: List[Tuple[str, str]] = []
    n = len(line)
    i = 0

    def skip_ws(pos: int) -> int:
        while pos < n and line[pos].isspace():
            pos += 1
        return pos

    while i < n:
        ch = line[i]
        if ch.isspace() or ch == "&":
            i += 1
            continue
        if line[i : i + 2] == "%%":
            break
        # 矢印演算子 + 矢印ラベル
        if ch in "-=.o<x":
            edge_m = re.match(
                r"(?:<--+>|<--|-->|---|-\.->|-\.-|==+>|===|--x|--o|x--x|o--o|<==+>|-\.+->|--)",
                line[i:],
            )
            if edge_m:
                i += edge_m.end()
                i = skip_ws(i)
                if i < n and line[i] == "|":
                    end_bar = line.find("|", i + 1)
                    if end_bar != -1:
                        labels_to_check.append(("arrow", line[i + 1 : end_bar]))
                        i = end_bar + 1
                continue
        if ch == "|":
            end_bar = line.find("|", i + 1)
            if end_bar != -1:
                labels_to_check.append(("arrow", line[i + 1 : end_bar]))
                i = end_bar + 1
                continue

        if ch == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 1
            i = j + 1
            continue

        # ノードID読み取り
        id_m = re.match(r"[A-Za-z_][A-Za-z0-9_-]*", line[i:])
        if id_m:
            nid = id_m.group(0)
            i += id_m.end()
            # :::class ショートハンドが付いている場合はスキップ
            if line[i : i + 3] == ":::":
                cls_m = re.match(r":::[A-Za-z0-9_-]+", line[i:])
                if cls_m:
                    i += cls_m.end()
            # 形状ラベルがあるか
            matched_shape = False
            for op, cl in SHAPE_DELIMS:
                if line.startswith(op, i):
                    pos = i + len(op)
                    if pos < n and line[pos] == '"':
                        qend = line.find('"', pos + 1)
                        if qend != -1:
                            after_q = skip_ws(qend + 1)
                            if line.startswith(cl, after_q):
                                labels_to_check.append(("node", line[pos : qend + 1]))
                                i = after_q + len(cl)
                                matched_shape = True
                                break
                    cl_idx = line.find(cl, pos)
                    if cl_idx != -1:
                        labels_to_check.append(("node", line[pos:cl_idx]))
                        i = cl_idx + len(cl)
                        matched_shape = True
                        break
            if not matched_shape and line[i : i + 3] == ":::":
                cls_m = re.match(r":::[A-Za-z0-9_-]+", line[i:])
                if cls_m:
                    i += cls_m.end()
            node_ids.append(nid)
            continue

        i += 1

    return node_ids, labels_to_check, []


def _is_quoted_label(raw: str) -> bool:
    s = raw.strip()
    return len(s) >= 2 and s[0] == '"' and s[-1] == '"'


def _strip_br_tags(text: str) -> str:
    return re.sub(r"<br\s*/?>", "", text, flags=re.IGNORECASE)


def _check_mermaid_blocks(
    mermaid_blocks: Sequence[Dict[str, Any]],
    size: str,
    badge_mode: str,
    findings: List[Dict[str, Any]],
) -> int:
    max_nodes = 0
    if size not in ("M", "L", "XL"):
        return 0

    if len(mermaid_blocks) > 3:
        _add_finding(
            findings,
            rule_id="PR017_MERMAID_GFM_SYNTAX",
            severity="fail",
            line=mermaid_blocks[3]["start_line"],
            snippet=f"mermaid blocks={len(mermaid_blocks)}",
            message=f"Mermaid ブロックが {len(mermaid_blocks)} 個あります（1 PR あたり上限 3 ブロック: 主図解 1〜2 + pie 1）。",
            suggestion="主図解 1〜2 個＋定量内訳 pie 1 個以内の計 3 ブロックまでに整理してください。",
        )

    for block in mermaid_blocks:
        lines: List[Tuple[int, str]] = block["lines"]
        diagram_type = _detect_mermaid_type(lines)
        unique_nodes: Set[str] = set()
        open_blocks: List[Tuple[int, str]] = []

        if badge_mode == "text":
            for line_no, raw in lines:
                s = raw.strip()
                if s and not s.startswith("%%") and EMOJI_RE.search(raw):
                    _add_finding(
                        findings,
                        rule_id="PR014_EMOJI_HYGIENE",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="--badge-mode text では Mermaid 図内の絵文字も禁止されています。",
                    )

        # 削減アピール (PR006) in pie / xychart-beta
        if diagram_type in ("pie", "xychart-beta"):
            for line_no, raw in lines:
                if REDUCTION_BOAST_MERMAID_RE.search(raw):
                    _add_finding(
                        findings,
                        rule_id="PR006_REDUCTION_BOAST_DETECTED",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="Mermaid 図内に Gate 1 のコード削減アピール表現が含まれています。",
                        suggestion="pie showData は Must-Read / Skim / Safe-to-Skip の変更内訳のみに使用してください。",
                    )

        if diagram_type in ("flowchart", "graph"):
            for idx_in_block, (line_no, raw) in enumerate(lines):
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if idx_in_block == 0 or low.startswith(("flowchart ", "graph ")):
                    continue
                if low.startswith("direction "):
                    continue
                if ":::" in _strip_quoted_segments(stripped):
                    _add_finding(
                        findings,
                        rule_id="PR017_MERMAID_GFM_SYNTAX",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="Mermaid の `:::` 短縮クラス記法は GFM で崩れるため禁止されています。",
                        suggestion="`class A,B added` のように別行で指定してください。",
                    )
                if low.startswith("classdef "):
                    _check_classdef_flowchart(line_no, stripped, findings)
                    continue
                if low.startswith("class "):
                    parts = stripped.split()
                    if len(parts) >= 3:
                        for nid in parts[1].split(","):
                            nid_c = nid.strip()
                            if nid_c:
                                if nid_c.lower() in MERMAID_RESERVED_IDS:
                                    _add_finding(
                                        findings,
                                        rule_id="PR017_MERMAID_GFM_SYNTAX",
                                        severity="fail",
                                        line=line_no,
                                        snippet=raw,
                                        message=f"予約語 `{nid_c}` をノード ID として使用しています。",
                                        suggestion=f"`n_{nid_c}` 等のプレフィックス付き ID に変更してください。",
                                    )
                                unique_nodes.add(nid_c)
                    continue
                if low.startswith(("style ", "linkstyle ", "click ", "acctitle", "accdescr")):
                    continue
                if low.startswith("subgraph "):
                    open_blocks.append((line_no, "subgraph"))
                    sub_rest = stripped[len("subgraph ") :].strip()
                    # subgraph id[title] or subgraph "title" or subgraph title
                    m_sub = re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*\[(.+)\]\s*$", sub_rest)
                    if m_sub:
                        title_raw = m_sub.group(1)
                        if not _is_quoted_label(title_raw) and SPECIAL_MERMAID_CHARS_RE.search(title_raw):
                            _add_finding(
                                findings,
                                rule_id="PR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message="subgraph タイトルに特殊文字が含まれますがダブルクォートで囲まれていません。",
                                suggestion='`subgraph id["タイトル"]` の形式でダブルクォート囲みにしてください。',
                            )
                    elif not _is_quoted_label(sub_rest) and (" " in sub_rest or SPECIAL_MERMAID_CHARS_RE.search(sub_rest)):
                        _add_finding(
                            findings,
                            rule_id="PR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="ID なしの subgraph タイトルがダブルクォートで囲まれていません。",
                            suggestion='`subgraph sg_id["タイトル"]` の形式で記述してください。',
                        )
                    continue
                if low == "end":
                    if open_blocks:
                        open_blocks.pop()
                    continue

                nids, labels, _ = _scan_flowchart_line(stripped)
                for nid in nids:
                    if nid.lower() in MERMAID_RESERVED_IDS:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"予約語 `{nid}` を裸のノード ID として使用しています。",
                            suggestion=f"`n_{nid}` 等のプレフィックス付き ID に変更してください。",
                        )
                    unique_nodes.add(nid)
                for kind, lbl in labels:
                    if not _is_quoted_label(lbl) and SPECIAL_MERMAID_CHARS_RE.search(lbl):
                        target_name = "矢印ラベル" if kind == "arrow" else "ノードラベル"
                        _add_finding(
                            findings,
                            rule_id="PR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"{target_name} `{lbl}` に特殊文字が含まれますがダブルクォートで囲まれていません。",
                            suggestion='`"... "` で囲んでください。',
                        )
                    no_br = _strip_br_tags(lbl)
                    if "<" in no_br or ">" in no_br:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="Mermaid ラベル内に `<br/>` 以外の生 `<` `>` が含まれています。",
                            suggestion="HTML タグ・不等号を避け、改行のみ `<br/>` を使用してください。",
                        )

        elif diagram_type == "sequenceDiagram":
            for line_no, raw in lines:
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if low == "sequencediagram" or low in ("autonumber",) or low.startswith(("title", "acctitle", "accdescr")):
                    continue
                if low.startswith("classdef "):
                    _add_finding(
                        findings,
                        rule_id="PR009_MERMAID_CLASSDEF_COLOR",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="sequenceDiagram 内での `classDef` 使用は GFM で構文エラーになります。",
                        suggestion="`classDef` を削除してください（`flowchart` 専用です）。",
                    )
                    continue
                if low.startswith(("alt ", "opt ", "loop ", "par ", "critical ", "box ", "rect ")):
                    open_blocks.append((line_no, low.split()[0]))
                    continue
                if low.startswith(("else", "and ", "option ")):
                    continue
                if low == "end":
                    if open_blocks:
                        open_blocks.pop()
                    continue
                if low.startswith("note "):
                    continue
                m_part = re.match(r"^(?:participant|actor)\s+(.+)$", stripped, re.IGNORECASE)
                if m_part:
                    rest = m_part.group(1).strip()
                    m_as = re.match(r"^([^\s]+)\s+as\s+(.+)$", rest, re.IGNORECASE)
                    if m_as:
                        pid, alias = m_as.group(1).strip(), m_as.group(2).strip()
                        unique_nodes.add(pid)
                        if not _is_quoted_label(alias) and SPECIAL_MERMAID_CHARS_RE.search(alias):
                            _add_finding(
                                findings,
                                rule_id="PR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message=f"participant エイリアス `{alias}` に特殊文字が含まれますがダブルクォートで囲まれていません。",
                                suggestion=f'`participant {pid} as "{alias}"` にしてください。',
                            )
                    else:
                        if not _is_quoted_label(rest) and SPECIAL_MERMAID_CHARS_RE.search(rest):
                            _add_finding(
                                findings,
                                rule_id="PR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message=f"participant 名 `{rest}` に特殊文字が含まれますがダブルクォートで囲まれていません。",
                                suggestion='`participant ID as "表示名"` にしてください。',
                            )
                        unique_nodes.add(rest.strip('"'))
                    continue
                m_act = re.match(r"^(?:activate|deactivate|create|destroy)\s+([^\s]+)", stripped, re.IGNORECASE)
                if m_act:
                    unique_nodes.add(m_act.group(1))
                    continue
                m_msg = re.match(r"^([A-Za-z0-9_.\"-]+)\s*(?:->>|-->>|->|-->|-x|--x|-\)|--\)|<<->>|<<-->>)\s*[+-]?([A-Za-z0-9_.\"-]+)\s*:", stripped)
                if m_msg:
                    unique_nodes.add(m_msg.group(1).strip('"'))
                    unique_nodes.add(m_msg.group(2).strip('"'))

        elif diagram_type in ("stateDiagram", "stateDiagram-v2"):
            for line_no, raw in lines:
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if low.startswith(("statediagram", "direction ", "note ", "acctitle", "accdescr")):
                    continue
                if low.startswith("classdef "):
                    _add_finding(
                        findings,
                        rule_id="PR009_MERMAID_CLASSDEF_COLOR",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="stateDiagram 内での `classDef` 使用は GFM で構文エラーになります。",
                        suggestion="`classDef` を削除してください（`flowchart` 専用です）。",
                    )
                    continue
                if ":::" in _strip_quoted_segments(stripped):
                    _add_finding(
                        findings,
                        rule_id="PR017_MERMAID_GFM_SYNTAX",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="Mermaid の `:::` 短縮記法は GFM で禁止されています。",
                    )
                if stripped.endswith("{"):
                    open_blocks.append((line_no, "state"))
                elif stripped == "}":
                    if open_blocks:
                        open_blocks.pop()
                    continue
                m_st_as = re.match(r'^state\s+"[^"]*"\s+as\s+([^\s{]+)', stripped)
                if m_st_as:
                    sid = m_st_as.group(1)
                    if "-" in sid:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"stateDiagram の状態 ID `{sid}` にハイフン `-` は使用できません。",
                            suggestion=f"`{sid.replace('-', '_')}` のようにスネークケースにしてください。",
                        )
                    unique_nodes.add(sid)
                    continue
                m_trans = re.match(r"^([^\s:]+)\s*-->\s*([^\s:]+)", stripped)
                if m_trans:
                    for sid in (m_trans.group(1), m_trans.group(2)):
                        if sid == "[*]":
                            continue
                        if "-" in sid:
                            _add_finding(
                                findings,
                                rule_id="PR017_MERMAID_GFM_SYNTAX",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message=f"stateDiagram の状態 ID `{sid}` にハイフン `-` は使用できません。",
                                suggestion=f"`{sid.replace('-', '_')}` のようにスネークケースにしてください。",
                            )
                        unique_nodes.add(sid)

        elif diagram_type == "erDiagram":
            in_entity = False
            for line_no, raw in lines:
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if low == "erdiagram" or low.startswith(("acctitle", "accdescr", "direction ")):
                    continue
                if low.startswith("classdef "):
                    _add_finding(
                        findings,
                        rule_id="PR009_MERMAID_CLASSDEF_COLOR",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="erDiagram 内での `classDef` 使用は GFM で構文エラーになります。",
                    )
                    continue
                if in_entity:
                    if stripped == "}":
                        in_entity = False
                        continue
                    if "(" in stripped or ")" in stripped:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="erDiagram の属性型内に `()`（例: `varchar(255)`）を含めると GFM で構文エラーになります。",
                            suggestion="`varchar` のように括弧を省いて記述してください。",
                        )
                    if "<" in stripped or ">" in stripped:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="erDiagram の属性型内に `<` `>`（例: `List<String>`）を含めないでください。",
                            suggestion="`string_array` 等へ置換してください。",
                        )
                    continue
                m_ent = re.match(r"^([A-Za-z0-9_-]+)\s*\{$", stripped)
                if m_ent:
                    ename = m_ent.group(1)
                    if "-" in ename:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"erDiagram のエンティティ名 `{ename}` にハイフン `-` は使用できません。",
                            suggestion=f"`{ename.replace('-', '_')}` にしてください。",
                        )
                    unique_nodes.add(ename)
                    in_entity = True
                    continue
                m_rel = re.match(r"^([A-Za-z0-9_-]+)\s+[|}{o.]{2}--[|}{o.]{2}\s+([A-Za-z0-9_-]+)\s*:", stripped)
                if m_rel:
                    for ename in (m_rel.group(1), m_rel.group(2)):
                        if "-" in ename:
                            _add_finding(
                                findings,
                                rule_id="PR017_MERMAID_GFM_SYNTAX",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message=f"erDiagram のエンティティ名 `{ename}` にハイフン `-` は使用できません。",
                                suggestion=f"`{ename.replace('-', '_')}` にしてください。",
                            )
                        unique_nodes.add(ename)

        elif diagram_type == "pie":
            for line_no, raw in lines:
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if low.startswith("pie") or low.startswith(("acctitle", "accdescr")):
                    continue
                if low.startswith("classdef "):
                    _add_finding(
                        findings,
                        rule_id="PR009_MERMAID_CLASSDEF_COLOR",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="pie チャート内での `classDef` 使用は GFM で構文エラーになります。",
                    )
                    continue
                if low.startswith("title "):
                    title_body = stripped[6:].strip()
                    if ":" in title_body:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie チャートの `title` 内にコロン `:` を含めると GFM で構文エラーになります。",
                            suggestion="コロンを削除してください。",
                        )
                    continue
                # スライス行解析: "label" : number
                if stripped.startswith('"'):
                    qend = stripped.find('"', 1)
                    if qend == -1:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie スライスラベルの閉じダブルクォートが不足しています。",
                        )
                        continue
                    lbl = stripped[1:qend]
                    if ":" in lbl:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie スライスラベル内にコロン `:` は使えません。",
                        )
                    rest = stripped[qend + 1 :].strip()
                    if not rest.startswith(":"):
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message='pie スライスは `"ラベル" : 数値` の形式で記述してください。',
                        )
                        continue
                    val_str = rest[1:].strip()
                    if not re.match(r"^\d+(?:\.\d+)?$", val_str):
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"pie スライス値 `{val_str}` に単位文字（行/%等）や不正文字を含めないでください。",
                            suggestion="純粋な正の数値のみを記述してください。",
                        )
                    elif float(val_str) <= 0:
                        _add_finding(
                            findings,
                            rule_id="PR017_MERMAID_GFM_SYNTAX",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie チャートに値 0 以下のスライスは書けません。",
                            suggestion="値が 0 の区分は省略してください。",
                        )
                    unique_nodes.add(lbl)
                else:
                    _add_finding(
                        findings,
                        rule_id="PR017_MERMAID_GFM_SYNTAX",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message='pie スライスラベルは `"..."` で囲む必要があります。',
                    )

        if open_blocks:
            blk_line, blk_kind = open_blocks[-1]
            _add_finding(
                findings,
                rule_id="PR017_MERMAID_GFM_SYNTAX",
                severity="fail",
                line=blk_line,
                snippet=blk_kind,
                message=f"Mermaid の `{blk_kind}` ブロックに対応する `end` / `}}` が閉じていません。",
            )

        node_count = len(unique_nodes)
        max_nodes = max(max_nodes, node_count)
        if node_count > 10:
            _add_finding(
                findings,
                rule_id="PR007_MERMAID_NODE_LIMIT",
                severity="fail",
                line=block["start_line"],
                snippet=f"{diagram_type} ({node_count} nodes)",
                message=f"1つの Mermaid 図のユニークノード/アクター数が {node_count} 個あり、上限 10 個を超えています。",
                suggestion="ファイル単位ではなく責務・モジュール単位で 8〜10 個以内に集約してください。",
            )
        elif node_count >= 9:
            _add_finding(
                findings,
                rule_id="PR007_MERMAID_NODE_LIMIT",
                severity="warn",
                line=block["start_line"],
                snippet=f"{diagram_type} ({node_count} nodes)",
                message=f"1つの Mermaid 図のユニークノード/アクター数が {node_count} 個あります（推奨最大 8〜10 個）。",
            )

    return max_nodes


def _strip_quoted_segments(line: str) -> str:
    return re.sub(r'"[^"]*"', '""', line)


def _check_classdef_flowchart(line_no: int, stripped: str, findings: List[Dict[str, Any]]) -> None:
    m = re.match(r"^classDef\s+([A-Za-z0-9_-]+)\s+(.+)$", stripped, re.IGNORECASE)
    if not m:
        return
    cls_name = m.group(1)
    styles = m.group(2).strip()
    if not re.search(r"\bcolor\s*:\s*#000(?:000)?\b", styles, re.IGNORECASE):
        _add_finding(
            findings,
            rule_id="PR009_MERMAID_CLASSDEF_COLOR",
            severity="fail",
            line=line_no,
            snippet=stripped,
            message=f"`classDef {cls_name}` にダークモード視認性用の `color:#000` が欠落しています。",
            suggestion="末尾に `,color:#000` を追加してください。",
        )
    if cls_name not in STANDARD_CLASSDEFS:
        _add_finding(
            findings,
            rule_id="PR009_MERMAID_CLASSDEF_COLOR",
            severity="warn",
            line=line_no,
            snippet=stripped,
            message=f"`classDef {cls_name}` は標準4クラス (added / modified / risk / base) 外です。",
        )
    else:
        spec = STANDARD_CLASSDEFS[cls_name]
        fill_m = re.search(r"\bfill\s*:\s*(#[0-9a-fA-F]+)", styles, re.IGNORECASE)
        stroke_m = re.search(r"\bstroke\s*:\s*(#[0-9a-fA-F]+)", styles, re.IGNORECASE)
        fill_val = fill_m.group(1).lower() if fill_m else ""
        stroke_val = stroke_m.group(1).lower() if stroke_m else ""
        if fill_val != spec["fill"] or stroke_val != spec["stroke"]:
            _add_finding(
                findings,
                rule_id="PR009_MERMAID_CLASSDEF_COLOR",
                severity="warn",
                line=line_no,
                snippet=stripped,
                message=f"`classDef {cls_name}` の色が標準パレット (fill:{spec['fill']},stroke:{spec['stroke']}) と異なります。",
            )


def _extract_table_rows_outside_details(
    non_code_raw_lines: Sequence[str],
    details_ranges: Sequence[Tuple[int, int]],
) -> List[Tuple[int, List[str]]]:
    """<details> 外の Markdown テーブル行 (line_no, cells) を抽出する。"""
    rows: List[Tuple[int, List[str]]] = []
    i = 0
    n = len(non_code_raw_lines)
    while i < n:
        line_no = i + 1
        if _line_in_ranges(line_no, details_ranges):
            i += 1
            continue
        line = non_code_raw_lines[i].strip()
        if "|" in line and i + 1 < n:
            sep = non_code_raw_lines[i + 1].strip()
            if re.match(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$", sep):
                # ヘッダ行・セパレータ行を飛ばしてデータ行を読む
                j = i + 2
                while j < n:
                    rline_no = j + 1
                    if _line_in_ranges(rline_no, details_ranges):
                        break
                    rline = non_code_raw_lines[j].strip()
                    if not rline or "|" not in rline:
                        break
                    cells = [c.strip() for c in rline.strip("|").split("|")]
                    rows.append((rline_no, cells))
                    j += 1
                i = j
                continue
        i += 1
    return rows


def _extract_path_candidates_from_cell(cell: str) -> List[str]:
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell)
    clean = clean.replace("`", " ")
    return [tok.strip(" ,;()[]") for tok in clean.split() if "/" in tok or "." in tok]


def _check_emoji_hygiene(
    non_code_raw_lines: Sequence[str],
    badge_mode: str,
    findings: List[Dict[str, Any]],
) -> None:
    saw_allowed_emoji_badge = False
    saw_text_badge = False

    for line_no, raw in enumerate(non_code_raw_lines, start=1):
        if not raw:
            continue
        # インラインコード内の絵文字は無視しつつ行をチェック
        masked = _mask_inline_code(raw)
        stripped = masked.strip()

        if TEXT_BADGE_RE.search(masked):
            saw_text_badge = True
            if badge_mode == "emoji":
                _add_finding(
                    findings,
                    rule_id="PR014_EMOJI_HYGIENE",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="--badge-mode emoji でテキストバッジ ([MUST]/[SKIM]/[SKIP]/[SCOPE]) が使われています。",
                )

        emojis = [(m.start(), m.group(0)) for m in EMOJI_RE.finditer(masked)]
        if not emojis:
            continue

        if badge_mode == "text":
            _add_finding(
                findings,
                rule_id="PR014_EMOJI_HYGIENE",
                severity="fail",
                line=line_no,
                snippet=raw,
                message=f"--badge-mode text では絵文字 ({emojis[0][1]}) の使用が禁止されています。",
                suggestion="テキスト代替 ([SCOPE] / [MUST] / [SKIM] / [SKIP]) に置き換えてください。",
            )
            continue

        for pos, em in emojis:
            if em not in ALLOWED_BADGE_EMOJIS:
                _add_finding(
                    findings,
                    rule_id="PR014_EMOJI_HYGIENE",
                    severity="warn",
                    line=line_no,
                    snippet=raw,
                    message=f"許可バッジ4種以外の装飾絵文字 ({em}) が検出されました。",
                    suggestion="地の文・見出しの装飾絵文字は削除してください。",
                )
                continue

            saw_allowed_emoji_badge = True
            if em == "\U0001f3af":  # 🎯
                is_scope_start = bool(re.match(r"^\s*>\s*🎯\s*(?:\*\*)?Scope\b", stripped, re.IGNORECASE))
                is_target_label = bool(
                    re.match(r"^(?:#{1,6}\s*|[*_]*\s*)🎯\s*(?:人間が)?読むべきファイル", stripped)
                )
                if not (is_scope_start or is_target_label):
                    _add_finding(
                        findings,
                        rule_id="PR014_EMOJI_HYGIENE",
                        severity="warn",
                        line=line_no,
                        snippet=raw,
                        message="`🎯` は Scope バナー行頭と `🎯 読むべきファイル` ラベル以外では禁止されています。",
                    )

    if badge_mode == "auto" and saw_allowed_emoji_badge and saw_text_badge:
        _add_finding(
            findings,
            rule_id="PR014_EMOJI_HYGIENE",
            severity="fail",
            line=1,
            snippet="emoji + text badge mix",
            message="同一 PR 本文内で絵文字バッジとテキストバッジ ([MUST]/[SKIM]/[SKIP]/[SCOPE]) が混在しています。",
            suggestion="どちらかのモードに統一してください。",
        )


def _looks_like_prose_sentence(val: str) -> bool:
    """太字コロンラベルの値が短い識別子・数値・パスではなく説明文であるかを判定する。"""
    stripped = val.strip()
    if "。" in stripped:
        return True
    cleaned = re.sub(r"`[^`]+`|https?://\S+", "", stripped).strip()
    if len(cleaned) >= 18 and re.search(r"[ぁ-んァ-ヶ一-龥]", cleaned):
        return True
    if len(cleaned.split()) >= 5:
        return True
    return False


def _extract_template_info(template_text: str) -> Dict[str, Any]:
    prep = _preprocess_markdown(template_text)
    headings = _extract_headings(prep["non_code_raw_lines"])
    h2_h3 = [h for h in headings if h["level"] in (2, 3)]

    summary_heading: Optional[str] = None
    for h in headings:
        if re.search(
            r"^(?:\d+\.\s*)?(?:Summary|Overview|TL;DR|概要|変更概要|要約|Description|Summary\s*/\s*概要)$",
            h["text"],
            re.IGNORECASE,
        ):
            summary_heading = h["text"]
            break

    template_non_val_checkboxes: Set[str] = set()
    in_validation_sec = False
    for line in prep["non_code_raw_lines"]:
        hm = re.match(r"^\s{0,3}#{1,6}\s+(.+)$", line)
        if hm:
            in_validation_sec = bool(VALIDATION_HEADING_RE.search(hm.group(1)))
            continue
        cb = re.match(r"^\s*(?:[-*+]|\d+\.)\s+\[[ xX]\]\s+(.+)$", line)
        if cb and not in_validation_sec:
            template_non_val_checkboxes.add(_normalize_checkbox_text(cb.group(1)))

    requires_issue = bool(re.search(r"(?:Closes|Refs|Fixes)\s*#|関連\s*Issue|Related\s+Issue", template_text, re.IGNORECASE))
    requires_breaking = bool(re.search(r"破壊的変更|Breaking\s+Change", template_text, re.IGNORECASE))
    requires_migration = bool(re.search(r"マイグレーション|Migration", template_text, re.IGNORECASE))

    return {
        "h2_h3": h2_h3,
        "summary_heading": summary_heading,
        "non_val_checkboxes": template_non_val_checkboxes,
        "requires_issue": requires_issue,
        "requires_breaking": requires_breaking,
        "requires_migration": requires_migration,
    }


def _normalize_checkbox_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _find_yomiyasu_lint() -> Optional[Path]:
    script_path = Path(__file__).resolve()
    candidates = [
        script_path.parents[2] / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        Path.home() / ".agents" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        Path.home() / ".gemini" / "config" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
        Path.home() / ".claude" / "skills" / "yomiyasu" / "scripts" / "yomiyasu_lint.py",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _is_allowed_badge_only_emoji_line(raw_line: str) -> bool:
    masked = _mask_inline_code(raw_line)
    stripped = masked.strip()
    emojis = [m.group(0) for m in EMOJI_RE.finditer(masked)]
    if not emojis:
        return False
    for em in emojis:
        if em not in ALLOWED_BADGE_EMOJIS:
            return False
        if em == "\U0001f3af":
            is_scope = bool(re.match(r"^\s*>\s*🎯\s*(?:\*\*)?Scope\b", stripped, re.IGNORECASE))
            is_target = bool(re.match(r"^(?:#{1,6}\s*|[*_]*\s*)🎯\s*(?:人間が)?読むべきファイル", stripped))
            if not (is_scope or is_target):
                return False
    return True


def _is_standard_or_template_heading(
    raw_line: str,
    template_headings: Optional[Set[str]],
) -> bool:
    m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*$", raw_line)
    if not m:
        return False
    title = re.sub(r"^\d+\.\s*", "", m.group(1)).strip()
    if title in STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU:
        return True
    if any(title.startswith(std) for std in STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU):
        return True
    if template_headings and title.lower() in template_headings:
        return True
    return False


def _run_yomiyasu_integration(
    *,
    text: str,
    raw_lines: Sequence[str],
    badge_mode: str,
    template_headings: Optional[Set[str]],
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    yomi_path = _find_yomiyasu_lint()
    if yomi_path is None:
        return {"available": False, "executed": False}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(yomi_path),
                "--doc-type",
                "pr",
                "--reader",
                "expert",
                "--json",
                "-",
            ],
            input=text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "executed": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"available": True, "executed": False, "returncode": proc.returncode}

    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {"available": True, "executed": False, "error": "invalid json"}

    filtered_out = 0
    integrated = 0
    for item in data.get("findings", []):
        rule = str(item.get("rule", ""))
        line_no = int(item.get("line", 1) or 1)
        raw_line = raw_lines[line_no - 1] if 1 <= line_no <= len(raw_lines) else ""
        if rule == "emoji_prohibited" and badge_mode != "text" and _is_allowed_badge_only_emoji_line(raw_line):
            filtered_out += 1
            continue
        if rule == "metaphor_label" and _is_standard_or_template_heading(raw_line, template_headings):
            filtered_out += 1
            continue
        sev_raw = str(item.get("severity", "info")).lower()
        sev = "fail" if sev_raw == "error" else ("warn" if sev_raw == "warn" else "info")
        _add_finding(
            findings,
            rule_id=f"YOMIYASU_{rule.upper()}",
            severity=sev,
            line=line_no,
            snippet=str(item.get("snippet", raw_line)),
            message=str(item.get("message", "")),
        )
        integrated += 1

    return {
        "available": True,
        "executed": True,
        "path": str(yomi_path),
        "integrated_count": integrated,
        "filtered_count": filtered_out,
    }


def lint_pr_body(
    *,
    text: str,
    size: str,
    triage_data: Optional[Dict[str, Any]] = None,
    template_text: Optional[str] = None,
    template_mode: str = "merge",
    badge_mode: str = "auto",
    use_yomiyasu: bool = False,
) -> Dict[str, Any]:
    """PR 本文 Markdown を全ルールで検証し、結果辞書を返す。"""
    prep = _preprocess_markdown(text)
    raw_lines: List[str] = prep["raw_lines"]
    non_code_raw_lines: List[str] = prep["non_code_raw_lines"]
    prose_lines: List[str] = prep["prose_lines"]
    mermaid_blocks: List[Dict[str, Any]] = prep["mermaid_blocks"]

    headings = _extract_headings(non_code_raw_lines)
    details_ranges = _extract_details_line_ranges(non_code_raw_lines)
    findings: List[Dict[str, Any]] = []

    template_info = _extract_template_info(template_text) if template_text is not None else None
    strict_summary_h = template_info["summary_heading"] if (template_info and template_mode == "strict") else None

    # PR001 & PR002: TL;DR
    tldr_start_line, tldr_lines = _extract_tldr_section(
        non_code_raw_lines, headings, strict_summary_heading=strict_summary_h
    )
    tldr_chars = 0
    tldr_cjk_ratio = 0.0
    if not tldr_lines:
        _add_finding(
            findings,
            rule_id="PR001_TLDR_MISSING",
            severity="fail",
            line=tldr_start_line or 1,
            snippet=non_code_raw_lines[(tldr_start_line or 1) - 1] if non_code_raw_lines else "",
            message="`## TL;DR`（または `## 概要` / 冒頭要約）の本文が見つかりません。",
            suggestion="変更の目的 (Why) と振る舞いの変化を 1〜3 行で記述してください。",
        )
    else:
        cleaned_tldr, tldr_cjk_ratio = _normalize_tldr_chars(tldr_lines)
        tldr_chars = len(cleaned_tldr)
        if len(tldr_lines) > 3:
            _add_finding(
                findings,
                rule_id="PR002_TLDR_TOO_LONG",
                severity="fail",
                line=tldr_lines[3][0],
                snippet=tldr_lines[3][1],
                message=f"TL;DR が {len(tldr_lines)} 行あります（最大 3 行）。",
                suggestion="要点を 3 行以内に圧縮してください。",
            )
        if tldr_cjk_ratio >= 0.30:
            if tldr_chars > 250:
                _add_finding(
                    findings,
                    rule_id="PR002_TLDR_TOO_LONG",
                    severity="fail",
                    line=tldr_lines[0][0],
                    snippet=tldr_lines[0][1],
                    message=f"日本語 TL;DR が {tldr_chars} 字あり、上限 250 字を超えています（目安 150 字）。",
                )
            elif tldr_chars > 180:
                _add_finding(
                    findings,
                    rule_id="PR002_TLDR_TOO_LONG",
                    severity="warn",
                    line=tldr_lines[0][0],
                    snippet=tldr_lines[0][1],
                    message=f"日本語 TL;DR が {tldr_chars} 字あります（目安 150 字 / 推奨 180 字以内）。",
                )
        else:
            if tldr_chars > 500:
                _add_finding(
                    findings,
                    rule_id="PR002_TLDR_TOO_LONG",
                    severity="fail",
                    line=tldr_lines[0][0],
                    snippet=tldr_lines[0][1],
                    message=f"英語 TL;DR が {tldr_chars} 文字あり、上限 500 文字を超えています。",
                )
            elif tldr_chars > 350:
                _add_finding(
                    findings,
                    rule_id="PR002_TLDR_TOO_LONG",
                    severity="warn",
                    line=tldr_lines[0][0],
                    snippet=tldr_lines[0][1],
                    message=f"英語 TL;DR が {tldr_chars} 文字あります（推奨 350 文字以内）。",
                )

        # PR016: What 実況 (TL;DR 内)
        for line_no, raw_t in tldr_lines:
            masked_t = _mask_inline_code(raw_t)
            for pat in WHAT_COMMENTARY_PATTERNS:
                if pat.search(masked_t):
                    _add_finding(
                        findings,
                        rule_id="PR016_WHAT_COMMENTARY_HEURISTIC",
                        severity="warn",
                        line=line_no,
                        snippet=raw_t,
                        message="TL;DR に行番号や関数追加などの Diff What 実況が含まれている可能性があります。",
                        suggestion="「なぜ変えたか (Why)」と「外部から観測できる振る舞い変化」に書き換えてください。",
                    )
                    break

    # PR003: Size S 禁止要素
    if size == "S":
        for mb in mermaid_blocks:
            _add_finding(
                findings,
                rule_id="PR003_SIZE_S_FORBIDDEN_BLOCK",
                severity="fail",
                line=mb["start_line"],
                snippet="```mermaid",
                message="Size S では Mermaid 図の出力が禁止されています。",
            )
        for i in range(len(non_code_raw_lines) - 1):
            if "|" in non_code_raw_lines[i] and re.match(
                r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$", non_code_raw_lines[i + 1].strip()
            ):
                _add_finding(
                    findings,
                    rule_id="PR003_SIZE_S_FORBIDDEN_BLOCK",
                    severity="fail",
                    line=i + 1,
                    snippet=non_code_raw_lines[i],
                    message="Size S では Markdown 表の出力が禁止されています。",
                )
        for d_start, _ in details_ranges:
            _add_finding(
                findings,
                rule_id="PR003_SIZE_S_FORBIDDEN_BLOCK",
                severity="fail",
                line=d_start,
                snippet=non_code_raw_lines[d_start - 1],
                message="Size S では `<details>` 折りたたみの出力が禁止されています。",
            )
        for h in headings:
            if FORBIDDEN_SIZE_S_HEADING_RE.search(h["text"]):
                _add_finding(
                    findings,
                    rule_id="PR003_SIZE_S_FORBIDDEN_BLOCK",
                    severity="fail",
                    line=h["line"],
                    snippet=h["raw"],
                    message=f"Size S ではトリアージ/図解/設計判断見出し (`{h['text']}`) は禁止されています。",
                )
        for line_no, raw in enumerate(non_code_raw_lines, start=1):
            if SCOPE_BANNER_RE.match(raw):
                _add_finding(
                    findings,
                    rule_id="PR003_SIZE_S_FORBIDDEN_BLOCK",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="Size S では Scope バナーの出力が禁止されています。",
                )

    # PR004: Scope バナー (M/L/XL)
    if size in ("M", "L", "XL"):
        has_scope = any(
            SCOPE_BANNER_RE.match(ln) and re.sub(r"^\s*>\s*(?:🎯\s*)?(?:\*\*)?(?:Scope|\[SCOPE\])(?:\*\*)?[:：\s]*", "", ln, flags=re.IGNORECASE).strip()
            for ln in non_code_raw_lines
        )
        if not has_scope:
            _add_finding(
                findings,
                rule_id="PR004_MISSING_SCOPE_BANNER",
                severity="fail",
                line=1,
                snippet="> 🎯 Scope: ...",
                message=f"Size {size} では TL;DR 直下の Scope バナー（総変更 vs 実質コード・推定時間）が必須です。",
                suggestion="`> 🎯 Scope: 総変更 +X / -Y行 → 実質コード Z行 (Nファイル / 推定レビュー時間 A-B分)` を追加してください。",
            )

    # PR005 / PR006 / PR018 / PR019: 本文 prose スキャン
    for line_no, p_line in enumerate(prose_lines, start=1):
        if not p_line.strip():
            continue
        raw_line = raw_lines[line_no - 1]
        for pat, hint in AI_CLICHE_PATTERNS:
            m = pat.search(p_line)
            if m:
                _add_finding(
                    findings,
                    rule_id="PR005_AI_CLICHE_DETECTED",
                    severity="fail",
                    line=line_no,
                    snippet=raw_line,
                    message=f"禁止 AI クリシェ `{m.group(0)}` が検出されました。",
                    suggestion=hint,
                )
                break

        if not SCOPE_BANNER_RE.match(raw_line):
            for rpat in REDUCTION_BOAST_PROSE_PATTERNS:
                if rpat.search(p_line):
                    _add_finding(
                        findings,
                        rule_id="PR006_REDUCTION_BOAST_DETECTED",
                        severity="fail",
                        line=line_no,
                        snippet=raw_line,
                        message="Gate 1 のコード削減量アピール表現が検出されました（PR 本文への記載は禁止）。",
                        suggestion="削除した行数やスロップ整理の話題を本文から取り除いてください。",
                    )
                    break

        # PR018: 分類ラベル太字箇条書き
        bm = BOLD_LABEL_COLON_RE.match(raw_line) or BOLD_LABEL_COLON_INSIDE_RE.match(raw_line)
        if bm:
            lbl = bm.group(1).strip()
            val = bm.group(2).strip()
            lbl_low = lbl.lower()
            is_val_label = lbl_low in VALUE_LABEL_WORDS and len(val) <= 80 and "。" not in val
            is_class_label = lbl_low in CLASSIFICATION_LABEL_WORDS
            if is_class_label or (not is_val_label and _looks_like_prose_sentence(val)):
                _add_finding(
                    findings,
                    rule_id="PR018_CLASSIFICATION_BOLD_LABEL",
                    severity="fail",
                    line=line_no,
                    snippet=raw_line,
                    message=f"分類ラベル箇条書き `- **{lbl}**: ...` が検出されました。",
                    suggestion="太字ラベルとコロンを外し、1文の具体的な変更内容として書いてください。",
                )

        # PR019: 補助動詞・形式名詞漢字
        for kpat, kmsg in AUXILIARY_KANJI_PATTERNS:
            if kpat.search(p_line):
                _add_finding(
                    findings,
                    rule_id="PR019_AUXILIARY_KANJI",
                    severity="warn",
                    line=line_no,
                    snippet=raw_line,
                    message=kmsg,
                )
                break

    # PR007 / PR008 / PR009 / PR017: Mermaid 検査
    max_mermaid_nodes = _check_mermaid_blocks(mermaid_blocks, size, badge_mode, findings)

    # PR010: Safe-to-Skip <details>
    if size in ("M", "L", "XL"):
        has_details = any("<details" in ln.lower() for ln in non_code_raw_lines) and any(
            "<summary" in ln.lower() for ln in non_code_raw_lines
        )
        if triage_data is not None:
            skip_count = int(triage_data.get("summary", {}).get("safe_to_skip_files", 0))
            if skip_count > 0 and not has_details:
                _add_finding(
                    findings,
                    rule_id="PR010_DETAILS_MISSING_FOR_SKIP",
                    severity="fail",
                    line=1,
                    snippet=f"safe_to_skip_files={skip_count}",
                    message=f"Safe-to-Skip 対象が {skip_count} 件ありますが `<details><summary>...</summary></details>` がありません。",
                    suggestion="Safe-to-Skip ファイル一覧を `<details>` 内に折りたたんでください。",
                )
        elif not has_details:
            _add_finding(
                findings,
                rule_id="PR010_DETAILS_MISSING_FOR_SKIP",
                severity="warn",
                line=1,
                snippet="<details> missing",
                message="`<details><summary>...</summary></details>` が見つかりません（Safe-to-Skip ファイルが無ければ無視可）。",
            )

    # PR011 & PR012: triage-json クロスチェック
    if triage_data is not None:
        if size in ("M", "L", "XL"):
            must_read_paths = [str(f["path"]) for f in triage_data.get("files", {}).get("must_read", []) if "path" in f]
            if must_read_paths:
                table_rows = _extract_table_rows_outside_details(non_code_raw_lines, details_ranges)
                row_texts = [" | ".join(cells) for _, cells in table_rows]
                missing_paths: List[str] = []
                for mp in must_read_paths:
                    found = any(mp in rtext for rtext in row_texts)
                    if not found and size == "XL":
                        # XL 時はディレクトリ集約 (e.g. src/api/* や src/api/) を許容
                        for _, cells in table_rows:
                            for cell in cells:
                                for cand in _extract_path_candidates_from_cell(cell):
                                    prefix = cand.rstrip("*").rstrip("/")
                                    if prefix and (mp == prefix or mp.startswith(prefix + "/")):
                                        found = True
                                        break
                                if found:
                                    break
                            if found:
                                break
                    if not found:
                        missing_paths.append(mp)
                if missing_paths:
                    _add_finding(
                        findings,
                        rule_id="PR011_READING_ORDER_COVERAGE",
                        severity="fail",
                        line=1,
                        snippet=", ".join(missing_paths[:5]),
                        message=f"triage-json の must_read ファイルが本文の読むべきファイル表から欠落しています: {', '.join(missing_paths)}",
                        suggestion="すべての must_read ファイルを理解順の表へ追加してください。",
                    )

                # 表の並び順（3件以上で単純アルファベット昇順になっていないか）
                ordered_paths: List[str] = []
                all_eff_paths = {
                    str(f["path"])
                    for bucket in ("must_read", "skim")
                    for f in triage_data.get("files", {}).get(bucket, [])
                    if "path" in f
                }
                for _, cells in table_rows:
                    row_joined = " ".join(cells)
                    for ep in sorted(all_eff_paths, key=len, reverse=True):
                        if ep in row_joined and ep not in ordered_paths:
                            ordered_paths.append(ep)
                            break
                if len(ordered_paths) >= 3 and ordered_paths == sorted(ordered_paths, key=str.lower):
                    _add_finding(
                        findings,
                        rule_id="PR011_READING_ORDER_COVERAGE",
                        severity="warn",
                        line=table_rows[0][0] if table_rows else 1,
                        snippet=" -> ".join(ordered_paths[:4]),
                        message="読むべきファイル表の並びがアルファベット昇順と一致しています。型/スキーマ→コアロジック→呼び出し元→テストの理解順か確認してください。",
                    )

        # PR012: 追加/更新パッケージ名言及
        body_non_fence = "\n".join(non_code_raw_lines)
        missing_pkgs: List[str] = []
        for dep_entry in triage_data.get("dependencies", []):
            for item in list(dep_entry.get("added", [])) + list(dep_entry.get("updated", [])):
                pkg_name = str(item.get("name", "")).strip()
                if pkg_name and pkg_name.lower() not in body_non_fence.lower():
                    missing_pkgs.append(pkg_name)
        if missing_pkgs:
            _add_finding(
                findings,
                rule_id="PR012_DEPENDENCY_RATIONALE",
                severity="fail",
                line=1,
                snippet=", ".join(missing_pkgs),
                message=f"追加・更新された依存パッケージ名が本文に未記載です: {', '.join(missing_pkgs)}",
                suggestion="パッケージ名と導入理由 1 行を本文へ記載してください。",
            )

    # PR013: 検証ステータス & チェックボックス衛生
    has_validation_heading = any(VALIDATION_HEADING_RE.search(h["text"]) for h in headings)
    if not has_validation_heading:
        _add_finding(
            findings,
            rule_id="PR013_VALIDATION_CHECKBOX_HYGIENE",
            severity="fail",
            line=1,
            snippet="validation section missing",
            message="検証ステータス（テスト/確認コマンド結果）の見出しが見つかりません。",
            suggestion="`## 5. 検証ステータス`（Size S は `## 検証ステータス`）を追加してください。",
        )

    checked_items = 0
    unchecked_items = 0
    in_validation_sec = False
    for line_no, raw in enumerate(non_code_raw_lines, start=1):
        if re.match(r"^\s{0,3}#{1,6}\s+", raw):
            in_validation_sec = bool(VALIDATION_HEADING_RE.search(raw))
            continue
        cb_m = re.match(r"^\s*(?:[-*+]|\d+\.)\s+\[([ xX])\]\s+(.+)$", raw)
        if not cb_m:
            continue
        state = cb_m.group(1)
        body = cb_m.group(2).strip()
        if state in ("x", "X"):
            checked_items += 1
            if UNVERIFIED_CLAIM_RE.search(body):
                _add_finding(
                    findings,
                    rule_id="PR013_VALIDATION_CHECKBOX_HYGIENE",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="完了済み `[x]` 項目に曖昧・未実行表現（「問題ないはず」「目視」「通る見込み」等）が含まれています。",
                    suggestion="未実行・手動確認事項は `- [ ]` へ分離してください。",
                )
            is_template_meta_cb = (
                template_info is not None
                and not in_validation_sec
                and _normalize_checkbox_text(body) in template_info["non_val_checkboxes"]
            )
            if not is_template_meta_cb and not EXEC_EVIDENCE_RE.search(body):
                _add_finding(
                    findings,
                    rule_id="PR013_VALIDATION_CHECKBOX_HYGIENE",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="完了済み `[x]` 行に実行コマンド・ツール表記（`` `...` `` 等）がありません。",
                    suggestion="実際に実行したコマンドと結果を明記するか、未実行なら `- [ ]` にしてください。",
                )
        else:
            unchecked_items += 1

    if has_validation_heading and checked_items == 0 and unchecked_items == 0:
        _add_finding(
            findings,
            rule_id="PR013_VALIDATION_CHECKBOX_HYGIENE",
            severity="warn",
            line=1,
            snippet="empty validation checklist",
            message="検証セクションに `- [x]` または `- [ ]` のチェック項目がありません。",
        )

    # PR014: 絵文字ハイジーン
    _check_emoji_hygiene(non_code_raw_lines, badge_mode, findings)

    # PR015: テンプレート整合性
    template_heading_set: Optional[Set[str]] = None
    if template_info is not None:
        tpl_h2_h3 = template_info["h2_h3"]
        template_heading_set = {h["text"].lower() for h in tpl_h2_h3}
        if template_mode == "strict":
            body_h_titles = [(h["text"].strip().lower(), h["line"]) for h in headings if h["level"] in (2, 3)]
            search_from = 0
            for th in tpl_h2_h3:
                t_low = th["text"].strip().lower()
                found_idx = None
                for idx in range(search_from, len(body_h_titles)):
                    if body_h_titles[idx][0] == t_low:
                        found_idx = idx
                        break
                if found_idx is None:
                    _add_finding(
                        findings,
                        rule_id="PR015_TEMPLATE_HEADING_INTEGRITY",
                        severity="fail",
                        line=1,
                        snippet=th["raw"],
                        message=f"`--template-mode strict` で元テンプレートの見出し `{th['text']}` が欠落または順序変更されています。",
                    )
                else:
                    search_from = found_idx + 1
        else:
            raw_joined = "\n".join(non_code_raw_lines)
            if template_info["requires_issue"] and not re.search(
                r"(?:Closes|Refs|Fixes)\s+#\d+|#\d+", raw_joined, re.IGNORECASE
            ):
                _add_finding(
                    findings,
                    rule_id="PR015_TEMPLATE_HEADING_INTEGRITY",
                    severity="warn",
                    line=1,
                    snippet="Issue reference missing",
                    message="テンプレートに Issue 参照欄がありますが、`Closes #123` 等の Issue 番号が見つかりません。",
                )
            if template_info["requires_breaking"] and not re.search(r"破壊的変更|Breaking\s+Change", raw_joined, re.IGNORECASE):
                _add_finding(
                    findings,
                    rule_id="PR015_TEMPLATE_HEADING_INTEGRITY",
                    severity="warn",
                    line=1,
                    snippet="Breaking change check missing",
                    message="テンプレートの「破壊的変更」項目が引き継がれていません。",
                )
            if template_info["requires_migration"] and not re.search(r"マイグレーション|Migration", raw_joined, re.IGNORECASE):
                _add_finding(
                    findings,
                    rule_id="PR015_TEMPLATE_HEADING_INTEGRITY",
                    severity="warn",
                    line=1,
                    snippet="Migration check missing",
                    message="テンプレートの「マイグレーション」項目が引き継がれていません。",
                )

    yomi_meta: Optional[Dict[str, Any]] = None
    if use_yomiyasu:
        yomi_meta = _run_yomiyasu_integration(
            text=text,
            raw_lines=raw_lines,
            badge_mode=badge_mode,
            template_headings=template_heading_set,
            findings=findings,
        )

    findings.sort(key=lambda item: (item["line"], item["rule_id"]))
    counts = {"fail": 0, "warn": 0, "info": 0}
    for f in findings:
        if f["severity"] in counts:
            counts[f["severity"]] += 1

    status = "FAIL" if counts["fail"] > 0 else ("WARN" if counts["warn"] > 0 else "PASS")
    result: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "size": size,
        "badge_mode": badge_mode,
        "counts": counts,
        "metrics": {
            "tldr_lines": len(tldr_lines),
            "tldr_chars": tldr_chars,
            "tldr_cjk_ratio": round(tldr_cjk_ratio, 3),
            "mermaid_blocks": len(mermaid_blocks),
            "max_mermaid_nodes": max_mermaid_nodes,
            "details_count": len(details_ranges),
            "checked_items": checked_items,
            "unchecked_items": unchecked_items,
        },
        "findings": findings,
    }
    if yomi_meta is not None:
        result["yomiyasu_lint"] = yomi_meta
    return result


def render_text_report(result: Dict[str, Any]) -> str:
    c = result["counts"]
    m = result["metrics"]
    lines = [
        f"PR Body Lint: {result['status']} (size={result['size']}, badge_mode={result['badge_mode']})",
        f"Counts: fail={c['fail']}, warn={c['warn']}, info={c['info']}",
        (
            f"Metrics: tldr={m['tldr_lines']} lines/{m['tldr_chars']} chars, "
            f"mermaid={m['mermaid_blocks']} blocks (max_nodes={m['max_mermaid_nodes']}), "
            f"details={m['details_count']}, checks=[x]={m['checked_items']}/[ ]={m['unchecked_items']}"
        ),
    ]
    for f in result["findings"]:
        lines.append(f"L{f['line']} [{f['severity'].upper()}] {f['rule_id']}: {f['message']}")
        if f.get("snippet"):
            lines.append(f"  > {f['snippet']}")
        if f.get("suggestion"):
            lines.append(f"  -> {f['suggestion']}")
    return "\n".join(lines)


def _run(argv: Optional[List[str]]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"pr_body_lint.py {__version__} (schema {SCHEMA_VERSION})")
        return 0

    triage_data: Optional[Dict[str, Any]] = None
    if args.triage_json is not None:
        triage_txt, err = _read_optional_file(args.triage_json, "triage JSON ")
        if err:
            _error(f"pr_body_lint.py: {err}")
            return 2
        assert triage_txt is not None
        try:
            triage_data = json.loads(triage_txt)
        except ValueError as exc:
            _error(f"pr_body_lint.py: triage JSON のパースに失敗しました: {exc}")
            return 2
        if not isinstance(triage_data, dict):
            _error("pr_body_lint.py: triage JSON のルートはオブジェクトである必要があります。")
            return 2

    size = args.size
    if size is None:
        if triage_data is None:
            _error("pr_body_lint.py: --size または --triage-json のどちらかを指定してください。")
            return 2
        prov = triage_data.get("summary", {}).get("provisional_size")
        if prov not in ("S", "M", "L", "XL"):
            _error("pr_body_lint.py: triage JSON に有効な summary.provisional_size が含まれていません。")
            return 2
        size = prov

    template_text: Optional[str] = None
    if args.template is not None:
        template_text, err = _read_optional_file(args.template, "テンプレートファイル")
        if err:
            _error(f"pr_body_lint.py: {err}")
            return 2

    body_text, err = _read_text_input(args.file)
    if err:
        _error(f"pr_body_lint.py: {err}")
        return 2
    assert body_text is not None

    result = lint_pr_body(
        text=body_text,
        size=size,
        triage_data=triage_data,
        template_text=template_text,
        template_mode=args.template_mode,
        badge_mode=args.badge_mode,
        use_yomiyasu=args.yomiyasu_lint,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text_report(result))

    if sys.stdout is not None:
        sys.stdout.flush()

    c = result["counts"]
    if args.fail_on == "fail" and c["fail"] > 0:
        return 1
    if args.fail_on == "warn" and (c["fail"] > 0 or c["warn"] > 0):
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        _use_utf8(sys.stdout)
        return _run(argv)
    except BrokenPipeError:
        _silence_stdout()
        _error("pr_body_lint.py: 出力先が閉じられたため、結果を最後まで書けませんでした。")
        return 3
    except Exception as error:
        if _closed_output_on_windows(error):
            _silence_stdout()
            _error("pr_body_lint.py: 出力先が閉じられたため、結果を最後まで書けませんでした。")
            return 3
        _error(f"pr_body_lint.py: 内部エラー: {type(error).__name__}: {error}")
        _error(traceback.format_exc().rstrip("\n"))
        return 3


if __name__ == "__main__":
    sys.exit(main())
