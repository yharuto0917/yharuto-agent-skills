---
name: human-first-pr
description: >-
  AIが書いたPRの冗長コード（AIスロップ）の無言整理・差分トリアージ・Mermaid図解を組み合わせ、「3秒で全体像と読むべき箇所がわかるPR概要（Pull Request本文）」を作成・再構成するスキル。
  「PRを作って」「プルリクエストを作成」「PRの説明文を書いて」「PR本文を書き直して」「変更を図解してPRにまとめて」「PR提出前に整理して」「PRを分割すべきか見て概要を作って」「gh pr create」「draft a PR description」「rewrite this PR body」「make this PR easier to review」など、PR本文の新規作成・改善・可視化、またはPR提出準備が目的の場面で必ず使用する。
  一方、コミットメッセージ作成単体（commit-message）、他者PRやdiffのバグ指摘・設計監査（fable-code-review）、PR作成を伴わないコード掃除単体（deslop）、PRと無関係な一般日本語推敲（yomiyasu）には使用しない。
---

# Human-First PR Skill

本スキルの中核アンカーは **3-Gate PR Pipeline（減量 → 仕分け → 視覚化）** である。PR 提出前に冗長防衛コードや無関係変更を無言で整理し、lockfile や自動生成物のノイズを隔離した上で、レビュアーが 3 秒で全体像と読む順番を判断できる Pull Request 本文を作成する。

```text
Step 0 (Mode / Base / テンプレート / 絵文字規約の確定)
  ├─ Mode A (編集可: 既定) ─▶ Gate 1 (差分内スロップ整理 + 検証 + コミット前承認) ─▶ Gate 2
  └─ Mode B (Read-Only) ────▶ [Gate 1 スキップ] ────────────────────────────────▶ Gate 2
                                                                                     │
  Gate 3 (Size S 最小4行 / Size M・L 図解構成 / Size XL 分割案+WARNING) ◀──────────┘
    └─ pr_body_lint.py (--yomiyasu-lint) exit 0 確認 ─▶ 出力 / 投稿前確認
```

## 起動意図・実行モード・書き込み規約

- **起動形態**: Auto-trigger（PR 作成・PR 本文リライト・PR 提出前整理の依頼時）および Manual-run（`/human-first-pr`）。
- **実行モードと適用境界**:

| 状況 / 依頼内容 | 選択モード | Gate 1 | 最終出力・振る舞い |
|---|---|---|---|
| ブランチ実装完了後の PR 作成・提出前整理（既定） | **Mode A** | 実施 | 整理＋検証後にコミット承認を確認し、PR 本文を生成 |
| 「PR 本文だけ書いて」「コードは触らないで」「PR #N 書き直し」 | **Mode B** | 禁止 | コード未変更で Gate 2〜3 のみ実行（スロップは本文外の末尾メモ 1〜3 行のみ） |
| 読み取り専用環境・マージ済み PR・Gate 1 拒否時 | **Mode B** | 禁止 | 同上 |
| コミットメッセージ単体 / 他者 PR 監査 / PR なし掃除 / 一般文書推敲 | **適用外** | — | 本スキルを中断し `commit-message` / `fable-code-review` / `deslop` / `yomiyasu` へ委譲 |

- **冪等書き込みと安全境界**:
  - Gate 1 のコード編集はベースブランチとの差分ハンクのみを対象とし、2 回目実行で追加差分が出ない冪等な最小編集にとどめる。
  - Gate 3 の PR 本文出力・更新は **上書き置換** とし、再実行時に既存セクションを重複追記しない。
  - **ユーザー確認なしの `git commit`・`git push`・`gh pr create`・`gh pr edit` 実行は禁止**。
  - **既存テストの無断削除・skip・コメントアウト、および無関係な `README.md`・ドキュメントの変更は禁止**。

## 厳格禁止事項（4 原則）

以下の 4 点はサイズ・モード・テンプレート種別を問わず常に守る。

1. **Diff の What 実況禁止**: 行番号や関数名の変更カタログを並べず、「なぜ（Why）変えたか」と「外から観測できる振る舞い変化」のみを書く（TL;DR は非空 3 行以内・日本語 150 字目安）。
2. **AI クリシェ禁止**: 「堅牢性を向上」「シームレスに統合」「包括的なエラーハンドリング」「クリーンで保守性の高い設計」などの曖昧語を禁止し、条件・数値・型・例外の具体挙動で書く。
3. **Gate 1 削減量アピール・削減グラフ禁止**: 整理で削った行数やコードダイエットの成果・比較グラフを PR 本文へ一切書かない（大量削除 PR の客観 Scope 表記は除く）。
4. **未実行テスト・確認事項の捏造禁止**: 実際に実行し成功を確認したコマンドと根拠のみ `- [x]` とし、未実行・環境不足による失敗・手動確認待ちは `- [ ]` へ分離して理由を明記する。

## JIT リファレンス読込とスクリプト解決

コンテキスト肥大と誤出力を防ぐため、専門辞書は該当フェーズに入った時点で初めて読み込む。各仕様の詳細はリファレンスを単一情報源（SSOT）とし、本ファイルへ重複記載しない。

| 実行フェーズ / 条件 | 読み込むファイル | 読まない条件（制約意図） |
|---|---|---|
| Gate 1 開始時（Mode A のみ） | `references/gate1-slop-diet.md` | **Mode B 時は読込禁止** |
| Gate 2 開始時（全モード） | `references/triage-rules.md` | なし（分類確認・サイズ確定に使用） |
| Gate 3 執筆時（全サイズ） | `references/japanese-pr-style.md` | なし（日本語規約・クリシェ回避に使用） |
| Gate 3 執筆時（Size M / L / XL のみ） | `references/mermaid-and-templates.md` | **Size S 確定時は読込禁止**（図表誤出力を構造的に遮断） |

- **`<skill-dir>` の解決ルール**: `<skill-dir>` は本 `SKILL.md` が配置されたディレクトリを指す。対象リポジトリのルートを作業ディレクトリとしてコマンドを実行する場合は、`<skill-dir>` を絶対パス（例: `SKILL_DIR`）または作業ディレクトリからの相対パスに解決して呼び出す。スキルディレクトリ側から別リポジトリを対象にする場合は `--repo <target-repo-path>` を指定する。
  ```bash
  # 例: 対象リポジトリをカレントディレクトリにして実行する場合
  python3 "<skill-dir>/scripts/pr_diff_triage.py" --mode committed --format json
  # 例: スキルディレクトリ等をカレントディレクトリにしたまま対象リポジトリを指定する場合
  python3 "<skill-dir>/scripts/pr_diff_triage.py" --repo "<target-repo>" --mode committed --format json
  ```

---

## Step 0: 実行モード・ベースブランチ・リポジトリ規約の確定

着手時に以下 4 点を確定し、誤った比較ベースや規約違反によるやり直しを防ぐ。

1. **実行モード確定**: 上記境界表で **Mode A** か **Mode B** かを決め、一時作業ディレクトリを作成する。既存 PR や下書きファイルを書き直す場合（Mode B）は、Issue 参照（`Closes #...`）と未確認チェックリストを控える一方、元文の「全テストパス」等の主張は信用せず実コードと実測結果だけで裏取りする。
   ```bash
   TMP_DIR=$(mktemp -d)
   CUR_BRANCH=$(git branch --show-current)
   ```
2. **ベースブランチ特定**: `main` の決め打ちと `@{u}` 参照（push 済み feature ブランチで自分自身を指し差分 0 になる罠）を禁止する。
   - ユーザー明示指定（`develop` 向け等）があればそれを `BASE_REF` とする。
   - 未指定かつ `gh` CLI 利用可能時は既存 PR から取得を試みる（未作成・オフライン時は空のまま進む）:
     ```bash
     BASE_REF=$(gh pr view --json baseRefName -q .baseRefName 2>/dev/null || true)
     ```
   - `BASE_REF` が空なら `--base` を省略し、`pr_diff_triage.py` の純ローカル Git 解決（`origin/HEAD` → 候補ブランチのうち現在ブランチ同名ローカルを除いた最小 `rev-list` 距離）へ委ねる。
3. **絵文字禁止規約の自動検出**: `CLAUDE.md`、`AGENTS.md`、`.github/pull_request_template.md`、`.yomiyasu-words/` 内に絵文字禁止規定がある場合、またはユーザーが絵文字なしを指定した場合は、Gate 3 で `--badge-mode text`（`[SCOPE]` / `[TARGET]` / `[MUST]` / `[SKIM]` / `[SKIP]`）を自動選択する。
   ```bash
   rg -n "絵文字|emoji|\[MUST\]|\[SCOPE\]" CLAUDE.md AGENTS.md .github/pull_request_template.md 2>/dev/null || true
   ```
4. **既存 PR テンプレートと CI の探索**: 以下のテンプレート有無と、`.github/workflows/*.yml` 内の PR 見出し・チェックリスト必須検証 CI（Danger / pr-lint 等）の有無を確認する。
   ```bash
   ls -1 .github/pull_request_template.md .github/PULL_REQUEST_TEMPLATE.md \
     .github/PULL_REQUEST_TEMPLATE/*.md docs/pull_request_template.md 2>/dev/null || true
   rg -n "pull_request_template|danger|pr-lint" .github/workflows/ 2>/dev/null || true
   ```

---

## Gate 1: Silent Code Diet（Mode A のみ）

Mode B の場合は Gate 1 をスキップして Gate 2 へ進む。具体的なスロップ 6 類型とスタック別チェックコマンドは `references/gate1-slop-diet.md` を参照する。

### 1.1 作業ツリー保護と編集範囲の限定

1. `git status --porcelain` と `git diff --name-only` で作業ツリーの状態を確認する。
2. 未コミット変更（staged / unstaged / untracked）がある場合は、作業ツリーを変更せず非破壊スナップショットを作成する（`git checkout -- .` や `git reset --hard` による巻き戻しは厳禁）:
   ```bash
   STASH_SHA=$(git stash create || true)
   git diff HEAD > "$TMP_DIR/pre_gate1.patch"
   ```
3. 編集範囲は **ベースブランチとの差分（`<merge-base>`〜作業ツリー）に含まれる変更関数・近傍ハンクのみ** とし、未変更の既存コードへの理由なきリファクタは行わない。

### 1.2 4 つの整理観点

`references/gate1-slop-diet.md` に従い、外部仕様を保ったまま次の順で整理する。

1. **車輪の再発明の排除**: 新規関数・型・定数・エラー処理が既存資産（`utils/`, `shared/`, `common/` 等）と重複していれば既存実装へ統合する。薄いラッパーや重複関数を削除・統合する前にテストファイルを含む全参照箇所を `rg` 等で検索し、旧関数を直接呼ぶテストがあれば統合先に合わせて更新または確認する。
2. **AI スロップの削除**: 到達不能な `null` / `undefined` チェック、要件外の無意味フォールバック、握りつぶしまたは再送出のみの `try-catch`、1 箇所呼び出しの薄いラッパー・使い捨て中間変数、What をなぞるだけの自明コメントを削る。
3. **3 段以上ネストの平坦化**: ガード節・早期リターンへ書き換える。
4. **無関係変更の扱い**: 本 PR の主題と無関係な周辺変更（未コミット・コミット済みともに）を **無断で `git restore` や削除しない**。該当ファイルと差分要約を一覧化し、差し戻すか別ブランチへ退避するか保持するかをユーザーへ確認する。

### 1.3 検証・失敗時ロールバック・コミット前承認プロトコル

1. マニフェストや CI 定義（`pyproject.toml` / `package.json` / `Cargo.toml` / `go.mod` / `Makefile` / `.github/workflows/`）を探索し、**型チェック → リンター/フォーマッター → テスト** の順で実実行する。
   - 既存テストが最初から落ちている可能性があるリポジトリ（DB 環境変数不足等）では、編集前にベースラインの失敗有無を把握し、新規悪化と既存失敗を切り分ける。
   - Gate 3 で根拠付き `[x]`（または未実行 `[ ]`）を書けるよう、実行コマンド・通過件数・エラー内容を正確に控えておく。
2. **テスト失敗時のロールバック手順**:
   - Gate 1 の編集が原因で失敗した場合は、当該ファイルのみ直ちに復元（クリーン状態なら `git restore -- <file>`、ダーティ状態なら `git checkout "$STASH_SHA" -- <file>` 等で編集前へ戻し事前状態を保全）して再検証し、通過を確認した上で当該箇所を「意図的保持」として残す。
   - 編集前から失敗していたテストがある場合は、動作変更を伴う構造リファクタ（薄いラッパー統合・大規模ネスト展開）を控え、静的範囲の整理にとどめる。
3. **コミット前承認の取得**: 検証通過後、**`git commit` 前に** 以下の 4 項目をチャット上で提示してユーザー承認を求める（確認なしに自動コミット・自動 push しない）。
   ```text
   - Gate 1 変更要約: <git diff --stat（PR本文へは転記しない）>
   - 検証コマンド結果: <実行コマンドと通過/失敗件数>
   - 無関係変更の確認: <該当ファイルと差し戻し/退避/保持の相談、なければ「なし」>
   - 提案コミットメッセージ: <Conventional Commits + 日本語>
   → (A) コミットを承認して Gate 2 (--mode committed) へ進む / (B) 未コミットのまま Gate 2 (--mode all) へ進む
   ```

---

## Gate 2: Diff Sizing & Signal/Noise Triage

`references/triage-rules.md` を読み込み、`pr_diff_triage.py` を実行して定量集計・初期分類・警告・依存変更を取得する。

```bash
python3 <skill-dir>/scripts/pr_diff_triage.py \
  --base <ref> \
  --mode {committed,working,all} \
  --format json > "$TMP_DIR/pr_triage.json"
```

- 事前に `BASE_REF` を取得済みなら `--base "$BASE_REF"` を渡し、未取得なら `--base` 自体を省略する。
- 変更がすべてコミット済み（Mode B または承認コミット後）なら `--mode committed`、Gate 1 の未コミット編集を残したまま進むなら `--mode all`、作業ツリー単体確認時は `--mode working` を選ぶ。別ディレクトリから実行する場合は `--repo <path>` を添える。

### AI による最終確定とルーティング

`pr_triage.json` の `summary`, `warnings`, `dependencies`, `files`, `ai_override_guidance` と実際の `git diff` を突き合わせ、以下を確定する。

1. **自動生成ファイル手動改変（`SUSPECTED_MANUAL_GEN_EDIT`）**:
   - スクリプトが警告したファイルは `Safe-to-Skip` に戻さず **`🧠 Must-Read` へ昇格して警告** を明記する。外部 API 定義等に基づく正当な再生成であると Diff から確認できた場合のみ `Safe-to-Skip` に戻し、理由を 1 行添える。
2. **意味的リスクとテスト健全性の検査**:
   - タイムアウト値等の 1 行定数変更や 1 ファイル内の複数レイヤー混在、テスト内 `.skip` / アサーション削除（`TEST_SKIP_OR_ASSERTION_REMOVAL`）を確認し、必要に応じて `Must-Read` / Size M 以上へ引き上げる。
3. **依存パッケージ理由の 1 行抽出**:
   - `dependencies` に `added` / `updated` がある場合は、パッケージ名と **コード上の利用箇所から確認した導入理由 1 行** を抽出する（`scripts` / `exports` 等非依存キー改変は `Must-Read` / `Skim` へ昇格）。
4. **確定サイズ別の Gate 3 ルーティング**（詳細閾値と特殊 PR 判定は `references/triage-rules.md` を参照）:

| 確定規模 / 特殊状態 | 読み込むリファレンス | Gate 3 の出力アクション |
|---|---|---|
| **Size S** / 健全な `skip_only` | `japanese-pr-style.md` のみ（図表リファレンス読込禁止） | 3.1 節の最小 4 行テンプレートのみ出力 |
| **Size M** / **Size L** / `deletion_heavy` / 大規模 Docs・CI | `mermaid-and-templates.md` + `japanese-pr-style.md` | 3.2 節の Visual-First 構成を出力 |
| **Size XL**（または独立責務混在） | 同上 | **まず PR 分割提案（Split Proposal）を出し、続けて `> [!WARNING]` 付きの 1 本版 PR ドラフトも同時出力** |
| **`empty_diff`** | なし | PR 本文を生成せず、比較ベースや未保存変更をユーザーへ確認 |

5. **テンプレート統合モードの確定**: 既存 PR テンプレートがある場合、必須見出し検証 CI があれば `--template-mode strict`、それ以外は `--template-mode merge`（既定）を選ぶ。

---

## Gate 3: Visual-First PR Generation & Self-Lint

確定したサイズと統合モードに従い、PR 本文ドラフトを作成して自己検証する。

### 3.1 Size S 最小 4 行テンプレート（本節を正本とする）

実質コードが Size S 確定時は `references/mermaid-and-templates.md` を **読み込まず**、`references/japanese-pr-style.md` のみ確認して以下の最小構成で出力する（Scope バナー・Mermaid 図・Markdown 表・`<details>`・設計判断表は出力禁止）。

```markdown
## TL;DR
<Why と外部から観測できる振る舞いの変化を最大3行・150字目安で記述>

## 検証ステータス
- [x] `<実際に実行し成功を確認したコマンド>`（<通過件数・根拠>）
```

- 依存追加が伴う Size S のみ、`## TL;DR` 本文直下に `- 依存追加: <pkg>（導入理由1行）` を 1 行だけ添えてよい。
- 未実行テストや手動確認項目がある場合は `## 検証ステータス` に `- [ ] <項目と未実行理由>` を追記する。

### 3.2 Size M / L / XL の出力方針とテンプレート統合

`references/mermaid-and-templates.md` と `references/japanese-pr-style.md` を読み込み、次の規則で本文を組み立てる。

- **標準骨格と連番詰め**: `## TL;DR` ＋ Scope バナー → `## 1. アーキテクチャ比較（Before / After）` →（必要時）`## 2. 振る舞い・エッジケース図解` → `## 3. 差分トリアージと読むべきコード` →（トレードオフ時）`## 4. 設計判断マトリクス` → `## 5. 検証ステータス`。任意節を省く場合は空見出しを残さず番号を詰める。
- **既存 PR テンプレート統合**: `--template-mode strict` 時は元テンプレートの見出し名・順序を一切変えず各見出し配下へ要素を差し込む。`merge` 時は Human-First PR 骨格を主軸にしつつ、`Closes #...`・破壊的変更チェック・DB マイグレーション欄・チェックリストを欠落なく引き継ぎ、ガイド用 HTML コメント（Bot マーカー除く）は削除する。
- **読むべきファイル表**: `🧠 Must-Read` → `👀 Skim` の順かつ理解しやすい順（型・スキーマ契約 → コアロジック → 呼び出し元・結線 → テスト）に並べる。アルファベット順は禁止。
- **Mermaid GFM 安全制約**: 1 図最大 8〜10 ノード（`Before`+`After` 合算）、主図解 1〜2 個（＋条件満たす場合の定量 `pie showData` 1 個）、特殊文字・矢印ラベルの `"..."` クォート必須、`classDef` は `flowchart` 限定かつ規定 4 色（`color:#000`）、`:::` 短縮記法と予約語裸 ID（`end` 等）禁止。
- **バッジモード統一**: 装飾絵文字（🚀✨⚠️ 等）は全面禁止。既定は許可バッジ 4 種（`🎯` / `🧠` / `👀` / `⏭️`）を所定位置のみに使い、絵文字禁止環境では `[SCOPE]` / `[TARGET]` / `[MUST]` / `[SKIM]` / `[SKIP]` で統一して混在させない。

### 3.3 Mode B（既存 PR 本文・下書き書き直し）の固有手順

既存の PR 本文や下書きファイル（例: `bad_pr_body.md`）を再構成する場合は、次を徹底する。

1. 既存ファイルを読むか `gh pr view` で現行本文を取得し、保持すべき Issue 参照（`Closes #...`）と未確認チェック項目だけを抜き出す。
   ```bash
   gh pr view <PR_NUMBER> --json title,body,baseRefName,headRefName 2>/dev/null || true
   ```
2. 元文の「テスト全パス」「大幅高速化」などの記述は根拠として採用せず、`git diff` と実実行した検証結果だけで書き直す（未実行・環境不足で落ちる項目は `- [ ]` へ降格）。
3. 元の文体（敬体指定テンプレート等）が明示されている場合のみ文体を合わせ、それ以外は常体で簡潔に記述する。
4. 明白なスロップを見つけても PR 本文へは書かず、最終回答の末尾に 1〜3 行の任意クリーンアップ候補メモとして添える。

### 3.4 `pr_body_lint.py` による自己検証と出力

ドラフトを `"$TMP_DIR/pr_draft.md"` へ保存し、`pr_body_lint.py` で機械検証する（`--yomiyasu-lint` 指定時は導入済み環境の `yomiyasu_lint.py --doc-type pr` も自動実行・ポストフィルタ統合され、未導入環境では無言スキップされる）。

```bash
python3 <skill-dir>/scripts/pr_body_lint.py \
  --size {S,M,L,XL} \
  --triage-json <path> \
  [--template <path> --template-mode {merge,strict}] \
  [--badge-mode {auto,emoji,text}] \
  [--yomiyasu-lint] \
  --json < "$TMP_DIR/pr_draft.md"
```

1. `status == "FAIL"`（終了コード `1`）が出た場合は `findings[]` を確認して下表に従い修正し、**終了コード `0`（`counts.fail == 0`）** になるまで再実行する。

| 検出 Rule ID | 主な原因 | 対処アクション |
|---|---|---|
| `PR001`〜`PR004` | TL;DR 欠落/超過、Size S 禁止要素、Scope 欠落 | TL;DR を 3 行・150 字目安へ圧縮、Size S なら図表削除、Size M 以上は Scope バナー補完 |
| `PR005` / `PR006` / `PR016` / `PR018` / `PR019` | AI クリシェ、削減自慢、What 実況、分類ラベル、漢字「事/為」 | 具体挙動へ置換、削減表現削除、分類太字を外し 1 文統合、ひらがな化 |
| `PR007`〜`PR009` / `PR017` | ノード超過、クォート漏れ、`classDef` 不備、GFM 非互換構文 | 責務単位で 10 ノード以下へ集約、特殊文字ラベル囲み、`color:#000` と `class` 行分離 |
| `PR010`〜`PR015` | `<details>` / `must_read` / 依存理由欠落、`[x]` 根拠不足、絵文字/見出し違反 | Skip 折りたたみ・理解順表・1 行理由を補完、未確認は `[ ]` へ移しバッジモード統一 |

2. `/yomiyasu` 連携時でも `yomiyasu-check:` 等の内部レポート見出しは出力本文へ含めない。
3. 指定出力先（例: `outputs/pr_body.md`）へ上書き保存またはチャット上に出力し、`rm -rf "$TMP_DIR"` で一時ディレクトリを削除する。
4. GitHub 上への新規作成・上書き更新（`gh pr create` / `gh pr edit`）が必要な場合は、タイトル・ベースブランチ・ヘッドブランチ・出力本文を提示し、ユーザーの明示承認を得てから実行する。

---

## 完了前セルフチェックと例外フォールバック

### 完了前セルフチェック（出力直前）

- [ ] `pr_body_lint.py` が終了コード `0` で通過した（`PR001`〜`PR019` の FAIL 0 件）
- [ ] 無断の `git commit`・`git push`・`gh pr create`・`gh pr edit` を行っていない
- [ ] 未実行・環境不足で失敗したテストを `[x]` にせず、`[ ]` と理由で分離した
- [ ] Gate 1 削減量アピール・AI クリシェ・What 実況・分類ラベル箇条書きが含まれない
- [ ] 一時ディレクトリ（`"$TMP_DIR"`）を削除し、対象リポジトリを作業ゴミで汚していない

### 例外・フォールバック対処

| 発生状況 | 対処方針 |
|---|---|
| テスト環境未整備・DB/API キー不足でローカル失敗 | 動作リスクのある Gate 1 リファクタを控え、検証ステータスで `[ ]` と失敗理由を事実どおり記載する |
| `gh` CLI 未導入・未認証 | Base ブランチ特定は `pr_diff_triage.py` の純ローカル Git 解決に任せ、PR 本文はファイルまたはチャット出力にとどめる |
| 対象リポジトリの差分が空（`empty_diff`） | PR 本文を生成せず、比較ベースブランチや未保存変更の有無をユーザーへ確認する |
| `Size XL` かつ分割不要とユーザーが指示 | 冒頭の `> [!WARNING]` を残したまま 1 本版ドラフトを採用し、Mermaid は責務単位の抽象化（10 ノード以下）を維持する |
