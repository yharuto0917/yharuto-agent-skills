#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pr_briefing_lint.py - Chat 上の PR 解説出力（Briefing）品質・Mermaid GFM・日本語規約検査 CLI。

完全 Read-Only の PR 解説パイプラインで、Chat 出力前の自己検査として使用する。
ルール BR001〜BR016 を検査し、任意で yomiyasu_lint.py (--doc-type pr --reader expert) と統合実行する。
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
import posixpath
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
TEXT_BADGE_RE = re.compile(r"\[(?:SCOPE|TARGET|MUST|SKIM|SKIP)\](?!\()")

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
    (re.compile(r"という挙動を(?:排除|防止|阻止)|静かに壊れ|黙って失敗|安全側に倒[すし]"), "比喩/抽象回避を禁止。中断条件と送出例外・記録先テーブルを書いてください。"),
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

TEMPLATE_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->")
TEMPLATE_PLACEHOLDER_HINT_RE = re.compile(
    r"(?:describe|your changes|summary|testing|checklist|how .* tested|motivation|context|"
    r"type of change|placeholder|example|todo|fixme|記載|記入|入力|説明|選択|削除|ここ|"
    r"変更|確認|概要|理由|してください|ください|\?\s*$)",
    re.IGNORECASE,
)
EMPTY_ISSUE_REF_RE = re.compile(r"^\s*(?:Closes|Fixes|Refs)\s+#\s*$", re.IGNORECASE)

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

UNVERIFIED_CLAIM_RE = re.compile(
    r"(?:問題ないはず|問題ないと思|通る見込み|通るはず|動くはず|動く見込み|"
    r"目視(?:のみ|で確認|確認)|おそらく(?:通過|成功|問題なし)|たぶん(?:大丈夫|通る)|"
    r"推測|未実行|未検証|確認予定|あとで確認|CI待ち|CI任せ|"
    r"\b(?:should pass|probably works|by inspection|not run|untested|pending CI)\b)",
    re.IGNORECASE,
)

PROSE_AUTHOR_UNVERIFIED_RE = re.compile(
    r"(?:問題ないはず|通る見込み|通るはず|動くはず|動く見込み|おそらく(?:通過|成功|問題なし)|たぶん(?:大丈夫|通る)|"
    r"全テスト(?:を)?実行し?(?:て|し、)?(?:パス|通過)を確認済み)",
    re.IGNORECASE,
)

PR_AUTHOR_QUOTE_CONTEXT_RE = re.compile(
    r"(?:PR\s*(?:本文|説明|記述)|作成者|記載されて|書かれて|主張|「[^」]*(?:問題ないはず|通るはず|動くはず|確認済み)[^」]*」)"
)

TEST_ASSERTION_CB_RE = re.compile(
    r"(?:テスト|試験|test|lint|typecheck|build|check|確認|パス|通過|成功|実行|検証|完了)",
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
    "依存削除",
    "コマンド",
    "実行コマンド",
    "環境",
    "参照",
    "参考",
    "パス",
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
    (
        re.compile(r"[ぁ-ん](?:事(?!の(?:経緯|次第|本質|重大さ|発端|顛末))|為)(?=[はがをにで、。のともやへかだな\s）)]|$)"),
        "形式名詞「事」「為」はひらがな（「こと」「ため」）にしてください。",
    ),
    (re.compile(r"[ぁ-ん](?:下さ|頂)(?:い|き|く|け|いた)"), "補助動詞「下さい」「頂く」はひらがな（「ください」「いただく」）にしてください。"),
    (re.compile(r"(?:て|で|[ぁ-ん][がはもを])出来(?:ます|ません|る|ない|た|ず)|出来(?:ます|ません)"), "補助動詞・可能表現「出来る」はひらがな（「できる」）にしてください。"),
)

CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

OVERVIEW_HEADING_RE = re.compile(
    r"^(?:\d+[.)]\s*)?(?:全体像|PR\s*の概要|概要|TL\s*;?\s*DR|要約|変更概要|Summary|Overview|PR\s*Overview|PR\s*Summary)"
    r"(?:\s*(?:[（(][^）)]*[）)]|[:：].*))?$",
    re.IGNORECASE,
)

FORBIDDEN_SIZE_S_HEADING_RE = re.compile(
    r"(?:アーキテクチャ|振る舞い|エッジケース図|仕組み[・･]?処理フロー|差分トリアージ|読むべき(?:ファイル|コード)|読む順番(?:ガイド|マップ)|設計判断|Safe-to-Skip|Architecture|Diff Triage|Reading Order|Reading Guide|Design Decisions?)",
    re.IGNORECASE,
)
READING_GUIDE_HEADING_RE = re.compile(
    r"(?:読む順番|読むべき(?:ファイル|コード)|差分トリアージ|Reading\s*(?:Order|Guide)|Files\s+to\s+Read)",
    re.IGNORECASE,
)
SCOPE_BANNER_RE = re.compile(r"^\s*>\s*(?:🎯\s*)?(?:\*\*)?(?:Scope|\[SCOPE\])(?:\*\*)?(?:[:：\s]|$)", re.IGNORECASE)

STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU: Tuple[str, ...] = (
    "全体像",
    "PRの概要",
    "概要",
    "TL;DR",
    "仕組み・処理フロー",
    "処理フロー",
    "アーキテクチャ比較",
    "振る舞い・エッジケース図解",
    "主な変更ポイントと設計判断",
    "主な変更ポイント",
    "設計判断マトリクス",
    "変更ポイント",
    "読む順番ガイド",
    "読む順番マップ",
    "読むべきファイル",
    "差分トリアージと読むべきコード",
    "注意・確認ポイント",
    "検証・確認ポイント",
    "確認ポイント",
    "検証ステータス",
    "検証結果",
    "チェックリスト",
    "メタ情報",
    "PR 分割読解ガイド",
    "分割読解ガイド",
)

TEST_DIR_NAMES: Set[str] = {"tests", "__tests__", "e2e"}


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
        prog="pr_briefing_lint.py",
        description="Chat 上の PR 解説出力をルール BR001〜BR016 で検査し、任意で yomiyasu_lint.py と連携する。",
        allow_abbrev=False,
    )
    parser.add_argument("file", nargs="?", help="PR解説 Markdown ファイル（省略または '-' で標準入力）")
    parser.add_argument("--size", choices=("S", "M", "L", "XL"), help="PR規模（--triage-json 未指定時は必須）")
    parser.add_argument("--triage-json", help="pr_diff_triage.py の出力 JSON パス")
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
    non_fence_raw_lines: List[str] = []
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
                non_fence_raw_lines.append("")
                if fence_lang == "mermaid":
                    current_mermaid_lines = []
                    mermaid_start = idx
                continue
            non_fence_raw_lines.append(line)
            no_comment = TEMPLATE_HTML_COMMENT_RE.sub("", line)
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
            non_fence_raw_lines.append("")

    if in_fence and fence_lang == "mermaid":
        mermaid_blocks.append({
            "start_line": mermaid_start,
            "end_line": len(raw_lines),
            "lines": current_mermaid_lines,
        })

    return {
        "raw_lines": raw_lines,
        "non_fence_raw_lines": non_fence_raw_lines,
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


def _extract_overview_section(
    non_code_raw_lines: Sequence[str],
    headings: Sequence[Dict[str, Any]],
) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    """全体像/概要セクションの開始行と (1-based行番号, 本文行) のリストを返す。"""
    overview_heading: Optional[Dict[str, Any]] = None
    for h in headings:
        if OVERVIEW_HEADING_RE.match(h["text"].strip()):
            overview_heading = h
            break

    lines_out: List[Tuple[int, str]] = []
    if overview_heading is not None:
        start_idx = overview_heading["line"]
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
            if re.match(r"^(?:Closes\s+#|Fixes\s+#|Refs\s+#)\s*", stripped, re.IGNORECASE):
                continue
            lines_out.append((line_no, stripped))
        return overview_heading["line"], lines_out

    # 見出しがない場合は最初の見出し・Scopeバナー・水平線より前の導入段落を対象とする
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
        if re.match(r"^(?:Closes\s+#|Fixes\s+#|Refs\s+#)\s*", stripped, re.IGNORECASE):
            continue
        if first_para_line is None:
            first_para_line = line_no
        lines_out.append((line_no, stripped))
    return first_para_line, lines_out


def _normalize_overview_chars(overview_lines: Sequence[Tuple[int, str]]) -> Tuple[str, float, str]:
    joined = " ".join(text for _, text in overview_lines)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", joined)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = cleaned.replace("`", "").replace("**", "").replace("__", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    non_ws = re.sub(r"\s+", "", cleaned)
    if not non_ws:
        return cleaned, 0.0, ""
    cjk_count = len(CJK_CHAR_RE.findall(non_ws))
    substance = re.sub(r"[\W_]+", "", cleaned)
    return cleaned, cjk_count / len(non_ws), substance


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


def _scan_flowchart_line(line: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """flowchart の1行を走査し、(出現ノードID一覧, [(種別, ラベル文字列)]) を返す。"""
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

        id_m = re.match(r"[A-Za-z_][A-Za-z0-9_-]*", line[i:])
        if id_m:
            nid = id_m.group(0)
            i += id_m.end()
            if line[i : i + 3] == ":::":
                cls_m = re.match(r":::[A-Za-z0-9_-]+", line[i:])
                if cls_m:
                    i += cls_m.end()
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

    return node_ids, labels_to_check


def _is_quoted_label(raw: str) -> bool:
    s = raw.strip()
    return len(s) >= 2 and s[0] == '"' and s[-1] == '"'


def _has_inner_double_quote(raw: str) -> bool:
    s = raw.strip()
    if _is_quoted_label(s):
        return '"' in s[1:-1]
    return False


def _strip_br_tags(text: str) -> str:
    return re.sub(r"<br\s*/?>", "", text, flags=re.IGNORECASE)


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
            rule_id="BR009_MERMAID_CLASSDEF_COLOR",
            severity="fail",
            line=line_no,
            snippet=stripped,
            message=f"`classDef {cls_name}` にダークモード視認性用の `color:#000` が欠落しています。",
            suggestion="末尾に `,color:#000` を追加してください。",
        )
    fill_m = re.search(r"\bfill\s*:\s*(#[0-9a-fA-F]+)", styles, re.IGNORECASE)
    stroke_m = re.search(r"\bstroke\s*:\s*(#[0-9a-fA-F]+)", styles, re.IGNORECASE)
    fill_val = fill_m.group(1).lower() if fill_m else ""
    stroke_val = stroke_m.group(1).lower() if stroke_m else ""
    standard_fills = {spec["fill"] for spec in STANDARD_CLASSDEFS.values()}

    if cls_name in STANDARD_CLASSDEFS:
        spec = STANDARD_CLASSDEFS[cls_name]
        if fill_val != spec["fill"] or stroke_val != spec["stroke"]:
            _add_finding(
                findings,
                rule_id="BR009_MERMAID_CLASSDEF_COLOR",
                severity="warn",
                line=line_no,
                snippet=stripped,
                message=f"`classDef {cls_name}` の色が標準パレット (fill:{spec['fill']},stroke:{spec['stroke']}) と異なります。",
            )
    elif fill_val not in standard_fills:
        _add_finding(
            findings,
            rule_id="BR009_MERMAID_CLASSDEF_COLOR",
            severity="warn",
            line=line_no,
            snippet=stripped,
            message=f"`classDef {cls_name}` の配色 ({fill_val or '未指定'}) が標準4色（#dcfce7 / #fef9c3 / #fee2e2 / #f3f4f6）外です。",
        )


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
            rule_id="BR007_MERMAID_NODE_LIMIT",
            severity="fail",
            line=mermaid_blocks[3]["start_line"],
            snippet=f"mermaid blocks={len(mermaid_blocks)}",
            message=f"Mermaid ブロックが {len(mermaid_blocks)} 個あります（上限 3 ブロック: 主図解 1〜2 + pie 1）。",
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
                        rule_id="BR014_EMOJI_HYGIENE",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="--badge-mode text では Mermaid 図内の非コメント行の絵文字も禁止されています。",
                    )

        if diagram_type in ("pie", "xychart-beta"):
            for line_no, raw in lines:
                if REDUCTION_BOAST_MERMAID_RE.search(raw):
                    _add_finding(
                        findings,
                        rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message="Mermaid 図内にコード削減量アピール表現が含まれています。",
                        suggestion="pie showData は Must-Read / Skim / Safe-to-Skip の変更内訳のみに使用してください。",
                    )

        if diagram_type in ("flowchart", "graph"):
            subgraph_has_direction = False
            subgraph_nodes: Set[str] = set()
            outside_nodes: Set[str] = set()
            cross_edges: List[Tuple[int, str, str]] = []

            for idx_in_block, (line_no, raw) in enumerate(lines):
                stripped = raw.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                low = stripped.lower()
                if idx_in_block == 0 or low.startswith(("flowchart ", "graph ")):
                    continue
                if low.startswith("direction "):
                    if open_blocks:
                        subgraph_has_direction = True
                    continue
                if ":::" in _strip_quoted_segments(stripped):
                    _add_finding(
                        findings,
                        rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                                        rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                    m_sub = re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*\[(.+)\]\s*$", sub_rest)
                    if m_sub:
                        title_raw = m_sub.group(1)
                        if not _is_quoted_label(title_raw) and SPECIAL_MERMAID_CHARS_RE.search(title_raw):
                            _add_finding(
                                findings,
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message="subgraph タイトルに特殊文字が含まれますがダブルクォートで囲まれていません。",
                                suggestion='`subgraph id["タイトル"]` の形式でダブルクォート囲みにしてください。',
                            )
                    elif not _is_quoted_label(sub_rest) and (" " in sub_rest or SPECIAL_MERMAID_CHARS_RE.search(sub_rest)):
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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

                nids, labels = _scan_flowchart_line(stripped)
                for nid in nids:
                    if nid.lower() in MERMAID_RESERVED_IDS:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"予約語 `{nid}` を裸のノード ID として使用しています。",
                            suggestion=f"`n_{nid}` 等のプレフィックス付き ID に変更してください。",
                        )
                    unique_nodes.add(nid)
                    if open_blocks:
                        subgraph_nodes.add(nid)
                    else:
                        outside_nodes.add(nid)
                if len(nids) >= 2 and any(op in stripped for op in ("-->", "---", "-.->", "==>", "<--")):
                    cross_edges.append((line_no, nids[0], nids[-1]))

                for kind, lbl in labels:
                    if _has_inner_double_quote(lbl):
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="Mermaid ラベル内にエスケープ不能な二重引用符 `\"` が含まれています。",
                        )
                    elif not _is_quoted_label(lbl) and SPECIAL_MERMAID_CHARS_RE.search(lbl):
                        target_name = "矢印ラベル" if kind == "arrow" else "ノードラベル"
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"{target_name} `{lbl}` に特殊文字が含まれますがダブルクォートで囲まれていません。",
                            suggestion='`"..."` で囲んでください。',
                        )
                    no_br = _strip_br_tags(lbl)
                    if "<" in no_br or ">" in no_br:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="Mermaid ラベル内に `<br/>` 以外の生 `<` `>` が含まれています。",
                            suggestion="HTML タグ・不等号を避け、改行のみ `<br/>` を使用してください。",
                        )

            if subgraph_has_direction and subgraph_nodes and outside_nodes:
                for edge_line, left_id, right_id in cross_edges:
                    if (left_id in subgraph_nodes and right_id in outside_nodes) or (
                        right_id in subgraph_nodes and left_id in outside_nodes
                    ):
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=edge_line,
                            snippet=f"{left_id} -> {right_id}",
                            message="subgraph 内の `direction` 指定とサブグラフ内外のクロス接続は GFM で崩れるため禁止されています。",
                        )
                        break

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
                        rule_id="BR009_MERMAID_CLASSDEF_COLOR",
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
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message=f"participant エイリアス `{alias}` に特殊文字が含まれますがダブルクォートで囲まれていません。",
                                suggestion=f'`participant {pid} as "{alias}"` にしてください。',
                            )
                        no_br = _strip_br_tags(alias)
                        if "<" in no_br or ">" in no_br:
                            _add_finding(
                                findings,
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                                severity="fail",
                                line=line_no,
                                snippet=raw,
                                message="participant エイリアス内に `<br/>` 以外の生 `<` `>` が含まれています。",
                            )
                    else:
                        if not _is_quoted_label(rest) and SPECIAL_MERMAID_CHARS_RE.search(rest):
                            _add_finding(
                                findings,
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                m_msg = re.match(
                    r"^([A-Za-z0-9_.\"-]+)\s*(?:->>|-->>|->|-->|-x|--x|-\)|--\)|<<->>|<<-->>)\s*[+-]?([A-Za-z0-9_.\"-]+)\s*:(.*)$",
                    stripped,
                )
                if m_msg:
                    unique_nodes.add(m_msg.group(1).strip('"'))
                    unique_nodes.add(m_msg.group(2).strip('"'))
                    msg_body = m_msg.group(3).strip()
                    no_br = _strip_br_tags(msg_body)
                    if "<" in no_br or ">" in no_br:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="sequenceDiagram メッセージ内に `<br/>` 以外の生 `<` `>` が含まれています。",
                        )

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
                        rule_id="BR009_MERMAID_CLASSDEF_COLOR",
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
                        rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                        rule_id="BR009_MERMAID_CLASSDEF_COLOR",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="erDiagram の属性型内に `()`（例: `varchar(255)`）を含めると GFM で構文エラーになります。",
                            suggestion="`varchar` のように括弧を省いて記述してください。",
                        )
                    if "<" in stripped or ">" in stripped:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                if low.startswith("pie"):
                    m_inline_title = re.search(r"\btitle\s+(.+)$", stripped, re.IGNORECASE)
                    if m_inline_title and ":" in m_inline_title.group(1):
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie チャートの `title` 内にコロン `:` を含めると GFM で構文エラーになります。",
                            suggestion="コロンを削除してください。",
                        )
                    continue
                if low.startswith(("acctitle", "accdescr")):
                    continue
                if low.startswith("classdef "):
                    _add_finding(
                        findings,
                        rule_id="BR009_MERMAID_CLASSDEF_COLOR",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie チャートの `title` 内にコロン `:` を含めると GFM で構文エラーになります。",
                            suggestion="コロンを削除してください。",
                        )
                    continue
                if stripped.startswith('"'):
                    qend = stripped.find('"', 1)
                    if qend == -1:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message="pie スライスラベル内にコロン `:` は使えません。",
                        )
                    rest = stripped[qend + 1 :].strip()
                    if not rest.startswith(":"):
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                            severity="fail",
                            line=line_no,
                            snippet=raw,
                            message=f"pie スライス値 `{val_str}` に単位文字（行/%等）や不正文字を含めないでください。",
                            suggestion="純粋な正の数値のみを記述してください。",
                        )
                    elif float(val_str) <= 0:
                        _add_finding(
                            findings,
                            rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                        rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
                        severity="fail",
                        line=line_no,
                        snippet=raw,
                        message='pie スライスラベルは `"..."` で囲む必要があります。',
                    )

        if open_blocks:
            blk_line, blk_kind = open_blocks[-1]
            _add_finding(
                findings,
                rule_id="BR008_MERMAID_UNQUOTED_SPECIAL",
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
                rule_id="BR007_MERMAID_NODE_LIMIT",
                severity="fail",
                line=block["start_line"],
                snippet=f"{diagram_type} ({node_count} nodes)",
                message=f"1つの Mermaid 図のユニークノード/アクター数が {node_count} 個あり、上限 10 個を超えています。",
                suggestion="ファイル単位ではなく責務・モジュール単位で 8〜10 個以内に集約してください。",
            )
        elif node_count >= 9:
            _add_finding(
                findings,
                rule_id="BR007_MERMAID_NODE_LIMIT",
                severity="warn",
                line=block["start_line"],
                snippet=f"{diagram_type} ({node_count} nodes)",
                message=f"1つの Mermaid 図のユニークノード/アクター数が {node_count} 個あります（推奨最大 8〜10 個）。",
            )

    return max_nodes


def _check_mermaid_without_prose(
    *,
    non_code_raw_lines: Sequence[str],
    mermaid_blocks: Sequence[Dict[str, Any]],
    details_ranges: Sequence[Tuple[int, int]],
    size: str,
    findings: List[Dict[str, Any]],
) -> None:
    """BR010: Mermaid 図を含む ## セクション内に言葉で補足する散文または箇条書き行が 1 行以上あるか検査する。"""
    if size not in ("M", "L", "XL") or not mermaid_blocks:
        return

    # テーブル行を判定
    table_line_nos: Set[int] = set()
    i = 0
    n = len(non_code_raw_lines)
    while i < n:
        line = non_code_raw_lines[i].strip()
        if "|" in line and i + 1 < n:
            sep = non_code_raw_lines[i + 1].strip()
            if re.match(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$", sep):
                table_line_nos.add(i + 1)
                table_line_nos.add(i + 2)
                j = i + 2
                while j < n:
                    rline = non_code_raw_lines[j].strip()
                    if not rline or "|" not in rline:
                        break
                    table_line_nos.add(j + 1)
                    j += 1
                i = j
                continue
        i += 1

    # セクション番号（## ごとに区切る）を各行へ割り当てる
    line_to_section: Dict[int, int] = {}
    section_has_prose: Dict[int, bool] = {}
    sec_id = 0
    for idx, raw in enumerate(non_code_raw_lines, start=1):
        if re.match(r"^\s{0,3}##\s+", raw):
            sec_id += 1
            line_to_section[idx] = sec_id
            section_has_prose.setdefault(sec_id, False)
            continue
        line_to_section[idx] = sec_id
        section_has_prose.setdefault(sec_id, False)

        s = raw.strip()
        if not s:
            continue
        if _line_in_ranges(idx, details_ranges):
            continue
        if idx in table_line_nos:
            continue
        if re.match(r"^\s{0,3}#{1,6}\s+", raw):
            continue
        if SCOPE_BANNER_RE.match(raw):
            continue
        if re.match(r"^[-*_]{3,}$", s):
            continue
        if re.match(r"^</?(?:details|summary)(?:\s[^>]*)?>$", s, re.IGNORECASE):
            continue
        # 散文または箇条書き行（引用注意注記含む）が 1 行以上あれば OK
        section_has_prose[sec_id] = True

    warned_sections: Set[int] = set()
    for block in mermaid_blocks:
        b_sec = line_to_section.get(block["start_line"], 0)
        if not section_has_prose.get(b_sec, False) and b_sec not in warned_sections:
            warned_sections.add(b_sec)
            _add_finding(
                findings,
                rule_id="BR010_MERMAID_WITHOUT_PROSE",
                severity="warn",
                line=block["start_line"],
                snippet="```mermaid (no prose in section)",
                message="Mermaid 図を含むセクション内に、図の背景やポイントを言葉で補足する散文・箇条書きがありません。",
                suggestion="図の前後に 2〜4 行程度の言語解説（従来課題・変更点・分岐条件など）を添えてください。",
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


def _extract_reading_guide_list_rows(
    non_code_raw_lines: Sequence[str],
    details_ranges: Sequence[Tuple[int, int]],
) -> List[Tuple[int, str]]:
    """読む順番ガイド配下の箇条書き行を補助的に収集する。"""
    items: List[Tuple[int, str]] = []
    in_guide = False
    guide_level = 0
    for idx, raw in enumerate(non_code_raw_lines, start=1):
        hm = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*$", raw)
        if hm:
            lvl = len(hm.group(1))
            title = hm.group(2).strip()
            if READING_GUIDE_HEADING_RE.search(title):
                in_guide = True
                guide_level = lvl
            elif in_guide and lvl <= guide_level:
                in_guide = False
            continue
        if not in_guide or _line_in_ranges(idx, details_ranges):
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", raw):
            items.append((idx, raw.strip()))
    return items


def _extract_path_candidates_from_cell(cell: str) -> List[str]:
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell)
    clean = clean.replace("`", " ")
    return [tok.strip(" ,;()[]") for tok in clean.split() if "/" in tok or "." in tok]


def _is_test_path(path: str) -> bool:
    norm = posixpath.normpath(path)
    base = posixpath.basename(norm)
    segs = [s.lower() for s in norm.split("/")[:-1] if s and s != "."]
    if any(s in TEST_DIR_NAMES for s in segs):
        return True
    stem, _ = posixpath.splitext(base)
    if base.startswith("test_") or stem.endswith("_test"):
        return True
    return bool(re.search(r"\.(?:test|spec)\.[^.]+$", base, re.IGNORECASE))


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
        masked = _mask_inline_code(raw)
        stripped = masked.strip()

        if TEXT_BADGE_RE.search(masked):
            saw_text_badge = True
            if badge_mode == "emoji":
                _add_finding(
                    findings,
                    rule_id="BR014_EMOJI_HYGIENE",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="--badge-mode emoji でテキストバッジ ([MUST]/[SKIM]/[SKIP]/[SCOPE]/[TARGET]) が使われています。",
                )

        emojis = [(m.start(), m.group(0)) for m in EMOJI_RE.finditer(masked)]
        if not emojis:
            continue

        if badge_mode == "text":
            _add_finding(
                findings,
                rule_id="BR014_EMOJI_HYGIENE",
                severity="fail",
                line=line_no,
                snippet=raw,
                message=f"--badge-mode text では絵文字 ({emojis[0][1]}) の使用が禁止されています。",
                suggestion="テキスト代替 ([SCOPE] / [TARGET] / [MUST] / [SKIM] / [SKIP]) に置き換えてください。",
            )
            continue

        for _, em in emojis:
            if em not in ALLOWED_BADGE_EMOJIS:
                _add_finding(
                    findings,
                    rule_id="BR014_EMOJI_HYGIENE",
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
                    re.match(
                        r"^(?:#{1,6}\s*|[*_]*\s*)🎯\s*(?:人間が)?読む(?:べき(?:ファイル|コード)|順番)",
                        stripped,
                    )
                )
                if not (is_scope_start or is_target_label):
                    _add_finding(
                        findings,
                        rule_id="BR014_EMOJI_HYGIENE",
                        severity="warn",
                        line=line_no,
                        snippet=raw,
                        message="`🎯` は Scope バナー行頭と `🎯 読むべきファイル` / `🎯 読む順番` ラベル以外では禁止されています。",
                    )

    if badge_mode == "auto" and saw_allowed_emoji_badge and saw_text_badge:
        _add_finding(
            findings,
            rule_id="BR014_EMOJI_HYGIENE",
            severity="fail",
            line=1,
            snippet="emoji + text badge mix",
            message="同一解説内で絵文字バッジとテキストバッジ ([MUST]/[SKIM]/[SKIP]/[SCOPE]/[TARGET]) が混在しています。",
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
            is_target = bool(
                re.match(
                    r"^(?:#{1,6}\s*|[*_]*\s*)🎯\s*(?:人間が)?読む(?:べき(?:ファイル|コード)|順番)",
                    stripped,
                )
            )
            if not (is_scope or is_target):
                return False
    return True


def _is_standard_briefing_heading(raw_line: str) -> bool:
    m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*$", raw_line)
    if not m:
        return False
    title = re.sub(r"^\d+[.)]\s*", "", m.group(1)).strip()
    title = re.sub(r"^(?:🎯\s*|\[TARGET\]\s*)", "", title).strip()
    if title in STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU:
        return True
    if any(title.startswith(std) for std in STANDARD_TEMPLATE_HEADINGS_FOR_YOMIYASU):
        return True
    return False


def _run_yomiyasu_integration(
    *,
    text: str,
    raw_lines: Sequence[str],
    badge_mode: str,
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
        if rule == "metaphor_label" and _is_standard_briefing_heading(raw_line):
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


def lint_pr_briefing(
    *,
    text: str,
    size: str,
    triage_data: Optional[Dict[str, Any]] = None,
    badge_mode: str = "auto",
    use_yomiyasu: bool = False,
) -> Dict[str, Any]:
    """Chat 上の PR 解説 Markdown を BR001〜BR016 で検証し、結果辞書を返す。"""
    prep = _preprocess_markdown(text)
    raw_lines: List[str] = prep["raw_lines"]
    non_fence_raw_lines: List[str] = prep["non_fence_raw_lines"]
    non_code_raw_lines: List[str] = prep["non_code_raw_lines"]
    prose_lines: List[str] = prep["prose_lines"]
    mermaid_blocks: List[Dict[str, Any]] = prep["mermaid_blocks"]

    headings = _extract_headings(non_code_raw_lines)
    details_ranges = _extract_details_line_ranges(non_code_raw_lines)
    findings: List[Dict[str, Any]] = []

    # BR001 & BR002: 冒頭全体像・概要セクションと長さ
    overview_start_line, overview_lines = _extract_overview_section(non_code_raw_lines, headings)
    overview_chars = 0
    overview_substance_chars = 0
    overview_cjk_ratio = 0.0
    if not overview_lines:
        _add_finding(
            findings,
            rule_id="BR001_OVERVIEW_MISSING",
            severity="fail",
            line=overview_start_line or 1,
            snippet=non_code_raw_lines[(overview_start_line or 1) - 1] if non_code_raw_lines else "",
            message="`## 全体像`（または `## PRの概要` / `## 概要` / `## TL;DR` / 導入段落）の本文が見つかりません。",
            suggestion="変更の背景 (Why)・主な変更点・振る舞いの変化を自然な言葉で記述してください。",
        )
    else:
        cleaned_ov, overview_cjk_ratio, substance_ov = _normalize_overview_chars(overview_lines)
        overview_chars = len(cleaned_ov)
        overview_substance_chars = len(substance_ov)
        ov_line_count = len(overview_lines)

        fail_reasons: List[str] = []
        warn_reasons: List[str] = []
        if ov_line_count > 18:
            fail_reasons.append(f"非空行が {ov_line_count} 行あり上限 18 行を超えています")
        elif 13 <= ov_line_count <= 18:
            warn_reasons.append(f"非空行が {ov_line_count} 行あります（推奨 12 行以内）")

        if overview_cjk_ratio >= 0.30:
            if overview_chars > 1000:
                fail_reasons.append(f"日本語要約が {overview_chars} 字あり上限 1000 字を超えています")
            elif 651 <= overview_chars <= 1000:
                warn_reasons.append(f"日本語要約が {overview_chars} 字あります（推奨 650 字以内）")
        else:
            if overview_chars > 2000:
                fail_reasons.append(f"英語要約が {overview_chars} 文字あり上限 2000 文字を超えています")
            elif 1301 <= overview_chars <= 2000:
                warn_reasons.append(f"英語要約が {overview_chars} 文字あります（推奨 1300 文字以内）")

        if overview_substance_chars < 30:
            warn_reasons.append(
                f"冒頭要約の言葉での説明が不足しています（実質 {overview_substance_chars} 字 < 30 字）"
            )

        if fail_reasons:
            _add_finding(
                findings,
                rule_id="BR002_OVERVIEW_LENGTH",
                severity="fail",
                line=overview_lines[0][0],
                snippet=overview_lines[0][1],
                message=" / ".join(fail_reasons),
                suggestion="要点を 3〜10 行（日本語 650 字以内 / 英語 1300 文字以内）に整理し、詳細は後続セクションへ分けてください。",
            )
        elif warn_reasons:
            _add_finding(
                findings,
                rule_id="BR002_OVERVIEW_LENGTH",
                severity="warn",
                line=overview_lines[0][0],
                snippet=overview_lines[0][1],
                message=" / ".join(warn_reasons),
                suggestion="背景・変更点・外部挙動の変化を過不足なく伝わる分量へ調整してください。",
            )

    # BR003: Size S 過剰要素禁止
    if size == "S":
        for mb in mermaid_blocks:
            _add_finding(
                findings,
                rule_id="BR003_SIZE_S_OVERKILL",
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
                    rule_id="BR003_SIZE_S_OVERKILL",
                    severity="fail",
                    line=i + 1,
                    snippet=non_code_raw_lines[i],
                    message="Size S では Markdown 表の出力が禁止されています。",
                )
        for d_start, _ in details_ranges:
            _add_finding(
                findings,
                rule_id="BR003_SIZE_S_OVERKILL",
                severity="fail",
                line=d_start,
                snippet=non_code_raw_lines[d_start - 1],
                message="Size S では `<details>` 折りたたみの出力が禁止されています。",
            )
        for h in headings:
            if FORBIDDEN_SIZE_S_HEADING_RE.search(h["text"]):
                _add_finding(
                    findings,
                    rule_id="BR003_SIZE_S_OVERKILL",
                    severity="fail",
                    line=h["line"],
                    snippet=h["raw"],
                    message=f"Size S ではトリアージ/図解/設計判断見出し (`{h['text']}`) は禁止されています。",
                )
        for line_no, raw in enumerate(non_code_raw_lines, start=1):
            if SCOPE_BANNER_RE.match(raw):
                _add_finding(
                    findings,
                    rule_id="BR003_SIZE_S_OVERKILL",
                    severity="fail",
                    line=line_no,
                    snippet=raw,
                    message="Size S では Scope バナーの出力が禁止されています。",
                )

    # BR004: Scope バナー (M/L/XL)
    if size in ("M", "L", "XL"):
        has_scope = any(
            SCOPE_BANNER_RE.match(ln)
            and re.sub(
                r"^\s*>\s*(?:🎯\s*)?(?:\*\*)?(?:Scope|\[SCOPE\])(?:\*\*)?[:：\s]*",
                "",
                ln,
                flags=re.IGNORECASE,
            ).strip()
            for ln in non_code_raw_lines
        )
        if not has_scope:
            _add_finding(
                findings,
                rule_id="BR004_MISSING_SCOPE_SUMMARY",
                severity="fail",
                line=1,
                snippet="> 🎯 Scope: ...",
                message=f"Size {size} では `> 🎯 Scope:` または `> [SCOPE]` のサマリー行（見かけ行数 vs 実質行数）が必須です。",
                suggestion="`> 🎯 Scope: 総変更 +X / -Y行 → 実質読むべきコード Z行 (Nファイル / 推定読解時間 A〜B分)` を追加してください。",
            )

    # BR005 / BR006 / BR015 / BR016: 本文スキャン
    # BR006 の未記入 HTML テンプレートコメント検査
    for line_no, raw_nf in enumerate(non_fence_raw_lines, start=1):
        if not raw_nf:
            continue
        for hm in TEMPLATE_HTML_COMMENT_RE.finditer(raw_nf):
            inner = hm.group(1).strip()
            if inner == "" or TEMPLATE_PLACEHOLDER_HINT_RE.search(inner):
                _add_finding(
                    findings,
                    rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                    severity="fail",
                    line=line_no,
                    snippet=raw_nf,
                    message="PR テンプレートの未記入 HTML プレースホルダが残っています（Chat PR 解説では不要です）。",
                    suggestion="テンプレートの案内コメントを取り除き、実 Diff に基づく解説のみを残してください。",
                )
                break
        if EMPTY_ISSUE_REF_RE.match(raw_nf):
            _add_finding(
                findings,
                rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                severity="fail",
                line=line_no,
                snippet=raw_nf,
                message="PR テンプレート由来の空の Issue 参照欄 (`Closes #`) が残っています。",
            )

    checked_items = 0
    unchecked_items = 0
    for line_no, p_line in enumerate(prose_lines, start=1):
        if not p_line.strip():
            continue
        raw_line = raw_lines[line_no - 1]

        # BR005: 日英 AI クリシェ
        for pat, hint in AI_CLICHE_PATTERNS:
            m = pat.search(p_line)
            if m:
                _add_finding(
                    findings,
                    rule_id="BR005_AI_CLICHE_DETECTED",
                    severity="fail",
                    line=line_no,
                    snippet=raw_line,
                    message=f"禁止 AI クリシェ `{m.group(0)}` が検出されました。",
                    suggestion=hint,
                )
                break

        # BR006: PR 作成者なりすまし未検証テスト断言・削減量自慢
        cb_m = re.match(r"^\s*(?:[-*+]|\d+\.)\s+\[([ xX])\]\s+(.+)$", raw_line)
        if cb_m:
            state = cb_m.group(1)
            body = cb_m.group(2).strip()
            if state in ("x", "X"):
                checked_items += 1
                if UNVERIFIED_CLAIM_RE.search(body):
                    _add_finding(
                        findings,
                        rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                        severity="fail",
                        line=line_no,
                        snippet=raw_line,
                        message="PR 作成者になりすました未検証の `[x]` テスト完了断言が検出されました。",
                        suggestion="実際に実行したコマンド結果のみ事実として記載し、未確認事項は未確認として分離してください。",
                    )
                elif TEST_ASSERTION_CB_RE.search(body) and not EXEC_EVIDENCE_RE.search(body):
                    _add_finding(
                        findings,
                        rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                        severity="fail",
                        line=line_no,
                        snippet=raw_line,
                        message="実行コマンド根拠のないテスト完了 `[x]` チェックが検出されました（PR 作成者視点の記述と混同しています）。",
                        suggestion="実行コマンドと結果を明記するか、未実行なら `- [ ]` または「未確認」と記載してください。",
                    )
            else:
                unchecked_items += 1
        elif PROSE_AUTHOR_UNVERIFIED_RE.search(p_line) and not PR_AUTHOR_QUOTE_CONTEXT_RE.search(raw_line):
            _add_finding(
                findings,
                rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                severity="fail",
                line=line_no,
                snippet=raw_line,
                message="Diff の確認根拠がないままテスト通過や安全性を断定する表現が検出されました。",
                suggestion="「diff 上では確認できない（要確認）」のように根拠範囲を明確にしてください。",
            )

        if not SCOPE_BANNER_RE.match(raw_line):
            for rpat in REDUCTION_BOAST_PROSE_PATTERNS:
                if rpat.search(p_line):
                    _add_finding(
                        findings,
                        rule_id="BR006_PR_AUTHOR_MODE_CONFUSION",
                        severity="fail",
                        line=line_no,
                        snippet=raw_line,
                        message="コード削減量自慢表現が検出されました（Chat PR 解説では不要なPR作成者視点表現です）。",
                        suggestion="削減行数アピールは削除し、現在の Diff の挙動と読解ポイントを解説してください。",
                    )
                    break

        # BR015: 分類太字ラベル箇条書き
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
                    rule_id="BR015_CLASSIFICATION_BOLD_LABEL",
                    severity="fail",
                    line=line_no,
                    snippet=raw_line,
                    message=f"分類太字ラベル箇条書き `- **{lbl}**: ...` が検出されました。",
                    suggestion="太字ラベルとコロンを外し、1文の具体的な内容として書いてください。",
                )

        # BR016: 補助動詞・形式名詞漢字
        for kpat, kmsg in AUXILIARY_KANJI_PATTERNS:
            if kpat.search(p_line):
                _add_finding(
                    findings,
                    rule_id="BR016_AUXILIARY_KANJI",
                    severity="warn",
                    line=line_no,
                    snippet=raw_line,
                    message=kmsg,
                )
                break

    # BR007 / BR008 / BR009 / BR010: Mermaid 検査
    max_mermaid_nodes = _check_mermaid_blocks(mermaid_blocks, size, badge_mode, findings)
    _check_mermaid_without_prose(
        non_code_raw_lines=non_code_raw_lines,
        mermaid_blocks=mermaid_blocks,
        details_ranges=details_ranges,
        size=size,
        findings=findings,
    )

    # BR011: Safe-to-Skip <details>
    if size in ("M", "L", "XL") and triage_data is not None:
        skip_count = int(triage_data.get("summary", {}).get("safe_to_skip_files", 0))
        has_details = any("<details" in ln.lower() for ln in non_code_raw_lines) and any(
            "<summary" in ln.lower() for ln in non_code_raw_lines
        )
        if skip_count > 0 and not has_details:
            _add_finding(
                findings,
                rule_id="BR011_DETAILS_MISSING_FOR_SKIP",
                severity="fail",
                line=1,
                snippet=f"safe_to_skip_files={skip_count}",
                message=f"Safe-to-Skip 対象が {skip_count} 件ありますが `<details><summary>...</summary></details>` がありません。",
                suggestion="Safe-to-Skip ファイル一覧を `<details>` 内に折りたたんでください。",
            )

    # BR012 & BR013: triage-json クロスチェック
    if triage_data is not None:
        if size in ("M", "L", "XL"):
            must_read_paths = [str(f["path"]) for f in triage_data.get("files", {}).get("must_read", []) if "path" in f]
            if must_read_paths:
                table_rows = _extract_table_rows_outside_details(non_code_raw_lines, details_ranges)
                guide_list_rows = _extract_reading_guide_list_rows(non_code_raw_lines, details_ranges)
                guide_texts: List[str] = [" | ".join(cells) for _, cells in table_rows] + [txt for _, txt in guide_list_rows]
                guide_entries: List[Tuple[int, str]] = [(ln, " | ".join(cells)) for ln, cells in table_rows] + guide_list_rows

                missing_paths: List[str] = []
                for mp in must_read_paths:
                    found = any(mp in gtext for gtext in guide_texts)
                    if not found and size == "XL":
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
                            for _, gtxt in guide_list_rows:
                                for cand in _extract_path_candidates_from_cell(gtxt):
                                    prefix = cand.rstrip("*").rstrip("/")
                                    if prefix and (mp == prefix or mp.startswith(prefix + "/")):
                                        found = True
                                        break
                                if found:
                                    break
                    if not found:
                        missing_paths.append(mp)
                if missing_paths:
                    _add_finding(
                        findings,
                        rule_id="BR012_READING_GUIDE_COVERAGE",
                        severity="fail",
                        line=1,
                        snippet=", ".join(missing_paths[:5]),
                        message=f"triage-json の must_read ファイルが読む順番表・ガイドから欠落しています: {', '.join(missing_paths)}",
                        suggestion="すべての must_read ファイルを理解順の読む順番ガイドへ追加してください。",
                    )

                ordered_paths: List[str] = []
                first_guide_line = guide_entries[0][0] if guide_entries else 1
                all_eff_paths = {
                    str(f["path"])
                    for bucket in ("must_read", "skim")
                    for f in triage_data.get("files", {}).get(bucket, [])
                    if "path" in f
                }
                for _, entry_text in guide_entries:
                    for ep in sorted(all_eff_paths, key=len, reverse=True):
                        if ep in entry_text and ep not in ordered_paths:
                            ordered_paths.append(ep)
                            break

                if len(ordered_paths) >= 4 and ordered_paths == sorted(ordered_paths, key=str.lower):
                    first_test_idx: Optional[int] = None
                    last_core_idx: Optional[int] = None
                    for idx, p in enumerate(ordered_paths):
                        if _is_test_path(p):
                            if first_test_idx is None:
                                first_test_idx = idx
                        else:
                            last_core_idx = idx
                    if first_test_idx is not None and last_core_idx is not None and first_test_idx < last_core_idx:
                        _add_finding(
                            findings,
                            rule_id="BR012_READING_GUIDE_COVERAGE",
                            severity="warn",
                            line=first_guide_line,
                            snippet=" -> ".join(ordered_paths[:4]),
                            message="読む順番ガイドが単純なアルファベット昇順かつテストファイルがコアより先になっています。型/スキーマ→コア→結線→テストの理解順へ並べてください。",
                        )

        # BR013: 依存パッケージ名言及
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
                rule_id="BR013_DEPENDENCY_EXPLANATION",
                severity="fail",
                line=1,
                snippet=", ".join(missing_pkgs),
                message=f"追加・更新された依存パッケージ名が解説本文に未記載です: {', '.join(missing_pkgs)}",
                suggestion="パッケージ名と導入・更新理由 1 行を解説へ記載してください。",
            )

    # BR014: 絵文字ハイジーン
    _check_emoji_hygiene(non_code_raw_lines, badge_mode, findings)

    yomi_meta: Optional[Dict[str, Any]] = None
    if use_yomiyasu:
        yomi_meta = _run_yomiyasu_integration(
            text=text,
            raw_lines=raw_lines,
            badge_mode=badge_mode,
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
            "overview_lines": len(overview_lines),
            "overview_chars": overview_chars,
            "overview_substance_chars": overview_substance_chars,
            "overview_cjk_ratio": round(overview_cjk_ratio, 3),
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
        f"PR Briefing Lint: {result['status']} (size={result['size']}, badge_mode={result['badge_mode']})",
        f"Counts: fail={c['fail']}, warn={c['warn']}, info={c['info']}",
        (
            f"Metrics: overview={m['overview_lines']} lines/{m['overview_chars']} chars (substance={m['overview_substance_chars']}), "
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
        print(f"pr_briefing_lint.py {__version__} (schema {SCHEMA_VERSION})")
        return 0

    triage_data: Optional[Dict[str, Any]] = None
    if args.triage_json is not None:
        triage_txt, err = _read_optional_file(args.triage_json, "triage JSON ")
        if err:
            _error(f"pr_briefing_lint.py: {err}")
            return 2
        assert triage_txt is not None
        try:
            triage_data = json.loads(triage_txt)
        except ValueError as exc:
            _error(f"pr_briefing_lint.py: triage JSON のパースに失敗しました: {exc}")
            return 2
        if not isinstance(triage_data, dict):
            _error("pr_briefing_lint.py: triage JSON のルートはオブジェクトである必要があります。")
            return 2

    size = args.size
    if size is None:
        if triage_data is None:
            _error("pr_briefing_lint.py: --size または --triage-json のどちらかを指定してください。")
            return 2
        prov = triage_data.get("summary", {}).get("provisional_size")
        if prov not in ("S", "M", "L", "XL"):
            _error("pr_briefing_lint.py: triage JSON に有効な summary.provisional_size が含まれていません。")
            return 2
        size = prov

    briefing_text, err = _read_text_input(args.file)
    if err:
        _error(f"pr_briefing_lint.py: {err}")
        return 2
    assert briefing_text is not None

    result = lint_pr_briefing(
        text=briefing_text,
        size=size,
        triage_data=triage_data,
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
        _error("pr_briefing_lint.py: 出力先が閉じられたため、結果を最後まで書けませんでした。")
        return 3
    except Exception as error:
        if _closed_output_on_windows(error):
            _silence_stdout()
            _error("pr_briefing_lint.py: 出力先が閉じられたため、結果を最後まで書けませんでした。")
            return 3
        _error(f"pr_briefing_lint.py: 内部エラー: {type(error).__name__}: {error}")
        _error(traceback.format_exc().rstrip("\n"))
        return 3


if __name__ == "__main__":
    sys.exit(main())
