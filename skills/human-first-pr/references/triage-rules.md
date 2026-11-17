# Step 1–2: PR 取得・3 階層トリアージ・ボイラープレート濾過辞書

本ドキュメントは、`3-Step PR Comprehension Pipeline（取得 → 仕分け → Chat図解解説）` の Step 1（PR 情報・差分の取得）および Step 2（ノイズ隔離・ボイラープレート濾過・規模判定）の判定基準を定義する正本である。本スキルは 100% Read-Only であり、リポジトリのコード変更や PR 自体の作成・更新は一切行わない。

## 目次

- [1. PR・Diff 取得の 3 経路とベースブランチ特定手順](#1-prdiff-取得の-3-経路とベースブランチ特定手順)
- [2. `pr_diff_triage.py` の JSON 出力読解と AI オーバーライド基準](#2-pr_diff_triagepy-の-json-出力読解と-ai-オーバーライド基準)
- [3. 3 階層仕分け辞書と強制昇格ルール](#3-3-階層仕分け辞書と強制昇格ルール)
- [4. Read-Only ボイラープレート濾過ガイド](#4-read-only-ボイラープレート濾過ガイド)
- [5. PR 作成者説明文とのギャップ検知ルール](#5-pr-作成者説明文とのギャップ検知ルール)
- [6. 規模判定（Size S / M / L / XL）と特殊 PR・巨大 PR 読解チャンク](#6-規模判定size-s--m--l--xlと特殊-pr巨大-pr-読解チャンク)

---

## 1. PR・Diff 取得の 3 経路とベースブランチ特定手順

### 1.1 入力 3 経路の使い分け（完全 Read-Only）

`scripts/pr_diff_triage.py` は対象の状態に応じて以下の 3 経路（相互排他）で差分を取得・解析する。いずれの経路でも作業ツリーや Git 履歴は一切変更しない。

| 経路 | CLI オプション | 内部動作と使う場面 |
|---|---|---|
| **① GitHub PR 直接取得** | `--pr <number\|url>` | 読み取り専用の `gh pr view <pr> --json number,title,url,baseRefName,headRefName,additions,deletions` と `gh pr diff <pr>` を実行し、ローカルへチェックアウトせずに unified diff を直接解析する。すでに出されている GitHub PR を解説するときの標準経路。 |
| **② ローカル Git 差分** | `[--base <ref>] --mode {committed,working,all}` | 手元の Git リポジトリから差分を取得して解析する。ローカルにチェックアウト済みの PR ブランチや作業中の差分を解説するときに使う。 |
| **③ Unified Diff パッチ入力** | `--patch-file <path\|->` | `.patch` / `.diff` ファイル（`-` の場合は標準入力）を直接パースする。対象リポジトリが手元にない環境や、外部から渡された差分テキストを解説するときに使う。 |

ローカル Git 経路（経路 ②）における `--mode` の比較範囲は次のとおりである。

| `--mode` | 比較範囲 | 使う場面 |
|---|---|---|
| `committed`（既定） | `<base>...HEAD`（`merge-base` 基準の 3-dot 相当） | ブランチ上に積まれた変更全体を解説するとき |
| `working` | `HEAD` と作業ツリー（staged / unstaged / untracked） | 未記録の手元変更だけをピンポイントで解説するとき |
| `all` | `merge-base(<base>, HEAD)` と作業ツリー＋未追跡ファイル | ブランチ上の変更と手元の未記録変更をまとめて解説するとき |

### 1.2 ローカル Git 経路でのベースブランチ自動特定手順

`main` への決め打ちハードコードおよび **`@{u}`（upstream）の参照は禁止**する。リモートへ公開済みの作業ブランチでは `@{u}` が `origin/<feature>` 自身を指すため、`merge-base` の差分が 0 行になる罠があるためである。

以下の優先順でベースブランチを特定する。

1. **ユーザーの明示指定**: `--base <ref>` や「`develop` 向けの PR」といった会話上の指定を最優先する。
2. **既存 PR メタデータの事前参照**: `gh` CLI が利用でき現在ブランチに紐づく PR が存在する場合は、`gh pr view --json baseRefName -q .baseRefName`（読み取り専用）で取得した値を `--base` へ渡す。
3. **リモートデフォルトブランチ参照**: `git symbolic-ref refs/remotes/origin/HEAD` から取得する。
4. **候補ブランチの最近接 `merge-base` 探索**:
   - 候補群 `["origin/main", "main", "origin/master", "master", "origin/develop", "develop", "origin/trunk", "trunk"]` のうち実在するものを列挙する。
   - **現在ブランチ（`git branch --show-current`）と同名のローカル候補は探索対象から必ず除外する**（デフォルトブランチ上での直作業時に自己比較で距離 0 になる問題を防ぐため）。
   - `git rev-list --count $(git merge-base <cand> <head>)..<head>` が最小となる候補を選ぶ（距離が同点の場合は上記リストの定義順で決定する）。
5. **フォールバック**: デフォルトブランチ上の未記録変更のみで他に比較対象がない場合は `HEAD` を基準とする。

---

## 2. `pr_diff_triage.py` の JSON 出力読解と AI オーバーライド基準

### 2.1 JSON 出力の主要項目

| キー | 内容と確認ポイント |
|---|---|
| `schema_version` / `meta` | スキーマ版、`source_type`（`"github_pr"` / `"git"` / `"patch_file"`）、`pr`（`--pr` 時は `{number, title, url, base_ref, head_ref}`、それ以外は `null`）、解決済み `base_ref`・`head_ref`・`merge_base_sha`・`mode` |
| `summary` | `total_*`（見かけ総差分）、`effective_*`（`must_read` + `skim` 合算）、各区分の件数と行数、`noise_ratio_pct`、`provisional_size`（仮サイズ）、`size_reasons`、`special_case`、`layers_touched`、`estimated_review_minutes`（`min`/`max`） |
| `warnings` | `SUSPECTED_MANUAL_GEN_EDIT`、`NON_DEPENDENCY_MANIFEST_CHANGE`、`LOCKFILE_WITHOUT_MANIFEST`、`TEST_SKIP_OR_ASSERTION_REMOVAL` 等 |
| `dependencies` | マニフェスト別の `added` / `updated` / `removed` パッケージ一覧、`non_dependency_changes` |
| `files` | `must_read` / `skim` / `safe_to_skip` のファイル配列（`path`, `status`, `added`, `deleted`, `layer`, `reason`, `flags` — `boilerplate_heavy` 等を含む） |
| `ai_override_guidance` | AI が実際の Diff 本文を読んで最終判定・補足すべき項目リスト（`must_verify` 等） |

### 2.2 AI オーバーライド基準（スクリプトとの協調判断）

`pr_diff_triage.py` はファイルパスと統計量から仮分類・仮サイズを算出する。最終的な解説構成を決める際は、必ず実際の Diff を読んで以下の 5 点を判定・上書きする。

1. **微小な 1 行定数・閾値のリスク変更**:
   - 例: `src/config/timeout.ts` の `3000` → `30` のような 1 行変更は統計上は極小でも本番影響が大きいため、`must_read` へ引き上げ、安易に Size S へ丸めず影響範囲を解説する。
2. **1 ファイル内の複数責務混在と実質単一変更**:
   - 1〜2 ファイルかつ 30 行未満でも、1 ファイル内に認証・DB・UI 境界の変更が混在していれば複数レイヤー扱い（Size M）へ引き上げて図解する。
   - 逆に、型定義と実装の 2 ファイル構成で実質的に単一の小修正であれば Size S を維持してよい。
   - `.github/workflows/` は安全のため初期分類が `must_read` になるが、権限・秘密情報・デプロイ条件・CI ゲートを変えない 1〜2 ファイル 30 行未満の軽微修正なら AI 判断で Size S として解説できる。
3. **テストの `.skip` 追加やアサーション削除の検知**:
   - テストファイルで `.skip` / `@unittest.skip` / `pytest.mark.skip` の追加やアサーション削除のみ（`TEST_SKIP_OR_ASSERTION_REMOVAL`）がある場合、`skim` から `must_read` へ強制昇格し、`## 注意・確認ポイント` で明示する。
4. **外部スキーマ由来再生成の確認**:
   - リポジトリ内にスキーマ元ファイルがなく `SUSPECTED_MANUAL_GEN_EDIT` 警告が出た場合でも、Diff 本文から外部 API スキーマ等に基づく正当な機械再生成と確認できたときに限り `Safe-to-Skip` 扱いへ戻し、`<details>` 内に再生成元を 1 行明記する。実際に手書き改変が混ざっている場合は `must_read` を維持し警告する。
5. **依存パッケージの導入・更新理由の抽出**:
   - `dependencies` に抽出された `added` / `updated` パッケージについて、実コード上の import 先・利用箇所から「なぜ導入・更新されたか」を 1 行で特定し、Chat 解説へ必ず含める（`BR013` 対応）。

---

## 3. 3 階層仕分け辞書と強制昇格ルール

| 階層 | 対象ファイル・変更内容 | 強制昇格・安全検証ルール |
|---|---|---|
| `🧠 Must-Read` | コアビジネスロジック、認証・認可・暗号・秘密情報（`auth`, `oauth`, `jwt`, `session`, `permission`, `rbac`, `crypto`, `secret`）、課金・決済・Webhook、状態遷移、手書き DB スキーマ・SQL マイグレーション（`*.sql`, `schema.prisma`, `migrations/`, `prisma/`）、公開 API 契約（`openapi`, `routes/`, `controllers/`）、`.github/workflows/**`・`Dockerfile`・`*.tf` 等の本番デプロイ・CI 設定 | **強制昇格対象**:<br>① 自動生成ファイルへの手動改変疑い（`SUSPECTED_MANUAL_GEN_EDIT`）<br>② 依存マニフェストの `scripts` / `exports` / `bin` 改変（`NON_DEPENDENCY_MANIFEST_CHANGE`）<br>③ テストでの `.skip` 追加またはアサーション削除のみ（`TEST_SKIP_OR_ASSERTION_REMOVAL`） |
| `👀 Skim` | テストコードの健全な追加・更新（`test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `tests/`, `__tests__/`, `e2e/`）、ルーティング・DI の単純結線、ドキュメント（`*.md`, `*.mdx`, `docs/`）、リンター/IDE 設定、純粋 CSS/スタイル、純粋リネーム（`R` かつ `0/0`）、非リスク領域の全削除（`D`）、マニフェストの `scripts`/`exports`/`bin` 以外の非依存キー変更 | `.skip` 追加やアサーション削除のみを検知した場合は `🧠 Must-Read` へ昇格させる |
| `⏭️ Safe-to-Skip` | **Lockfile 各種**: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `bun.lock`, `Cargo.lock`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Gemfile.lock`, `composer.lock`, `go.sum`, `Podfile.lock`, `flake.lock`, `mix.lock`<br>**Snapshot**: `*.snap`, `*.snap.new`, `__snapshots__/**`<br>**自動生成コード**: `*.gen.*`, `*.generated.*`, `*.g.dart`, `*.freezed.dart`, `*.pb.go`, `*_pb2.py`, `*_pb2_grpc.py`, `**/generated/**`, `**/__generated__/**`<br>**Drizzle / Prisma 自動生成メタデータ**: `drizzle/meta/**`（`_journal.json`, `*_snapshot.json`）, `prisma/migrations/migration_lock.toml`, `**/generated/prisma/**`（※`*.sql` と `schema.prisma` は除外し `Must-Read` 固定）<br>**ビルド成果物・ベンダー**: `**/dist/**`, `**/build/**`, `**/out/**`, `**/.next/**`, `**/.nuxt/**`, `**/target/**`, `**/*.tsbuildinfo`, `*.min.js`, `*.min.css`, `*.bundle.js`, `*.map`, `**/vendor/**`<br>**機械的 import パス一括置換のみ**（`import_path_only`）: 全変更行が `import` / `export ... from` / `from ... import` / `require(` / Rust `use` のパス変更と空行のみ（ロジック変更 0 行）<br>**依存マニフェスト**: `package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `Gemfile`, `composer.json`, `pom.xml`, `build.gradle*` | **安全検証ルール**:<br>1. 依存マニフェストで追加・更新されたパッケージ名と理由は解説文に必ず 1 行で明記する<br>2. 対応マニフェストが未変更のまま lockfile だけが変わっている場合は `LOCKFILE_WITHOUT_MANIFEST` 警告を出し、確認ポイントへ記載する<br>3. `import` パス置換に 1 行でもロジック変更が混在する場合は `Must-Read` へ戻す<br>4. 生成ヘッダ（`DO NOT EDIT`, `@generated`, `auto-generated`, `Generated by`, `Code generated by`）または生成パスに一致し、スキーマ元（`*.proto`, `*.graphql`, `*.gql`, `openapi.*`, `swagger.*`, `schema.prisma`, `codegen.*`, `drizzle.config.*`）未変更・ヘッダ削除・`TODO`/`FIXME`/`HACK`/`manual`/`console.log`/`debugger` 混入・局所ハンク改変のいずれかがあれば **`Must-Read` へ強制昇格**し `SUSPECTED_MANUAL_GEN_EDIT` を警告する |

---

## 4. Read-Only ボイラープレート濾過ガイド

AI が作成した PR や大規模 PR では、本質的なロジック変更の周囲に冗長な防御コードや定型ラッパーが大量に含まれることがある。本スキルはコードを直接編集・削除しない代わりに、**Chat 解説側でボイラープレート箇所を識別して「読むべき行レンジ」と「流し読みしてよい範囲」を具体的に案内する**ことで、人間の読解負荷を下げる。

### 4.1 識別対象のボイラープレートパターン（`boilerplate_heavy` フラグ連携）

`pr_diff_triage.py` は各コードファイルの追加ハンク（`+` 行）を検査し、コメント行比率が過大（追加行の 40% 超など）または冗長な防御チェック・空 catch 比率が高いファイルに `flags: ["boilerplate_heavy"]` を付与する。このフラグと実 Diff の目視を組み合わせて、以下のパターンを見分ける。

1. **重複した防御的 null / 型チェック**: 型定義や直前のバリデータですでに保証済みの引数に対して繰り返される `if (x === null || x === undefined)` や過剰な `isinstance` 分岐。
2. **右から左へ受け流すだけの薄いラッパー・詰め替え処理**: ドメインロジックを持たず、同名のフィールドを別オブジェクトへコピーするだけの DTO 変換や委譲メソッド。
3. **定型エラーハンドリング・再送出ラッパー**: ログを 1 行出して同じ例外をそのまま再送出するだけの定型 `try / catch`（`try / except`）ブロック。
4. **自明な処理の逐語コメント**: コードを読めば一目で分かる代入や関数呼び出しをそのまま日本語・英語で言い換えただけのコメント群。

### 4.2 解説時のナビゲーション書式

`boilerplate_heavy` が付いたファイルであっても、内部に重要な変更が含まれる限りファイルごと `Safe-to-Skip` に隠してはならない。`🧠 Must-Read` または `👀 Skim` の表に残した上で、「注目箇所・読み方メモ」欄で行レンジを切り分けて案内する。

- **良い案内例**:
  - `L15〜L48 の署名検証と有効期限の境界判定だけ読めば十分です。L49〜L92 は定型的なエラー型ラッパーとログ整形のため流し読みで問題ありません`
  - `L30〜L55 のトランザクション分離処理が中核です。冒頭 L8〜L29 の引数 null チェック群はスキーマ検証と重複する定型コードです`
- **禁止事項**:
  - 「AI スロップが大量に含まれています」「無駄なコードが N 行あります」といった攻撃的な評価や削減量アピールは書かず（`BR006` 対応）、どこに注目すべきかの客観的な道案内に徹する。

---

## 5. PR 作成者説明文とのギャップ検知ルール

`gh pr view` 等で取得できる既存の PR タイトル・説明文・チェックリストは **「PR 作成者による未検証の参考情報」** として扱う。解説の一次根拠は必ず実コードの Diff に置き、以下の 4 観点でギャップを点検する。

| 点検観点 | 具体的な食い違いの例 | Chat 解説での扱い |
|---|---|---|
| **1. 説明文の過剰主張・実装漏れ** | PR 本文には「タイムアウトと再試行上限を追加」とあるが、実 Diff には再試行回数しかなくタイムアウト設定がない | `## 注意・確認ポイント` で「PR 説明文ではタイムアウト追加と記載されていますが、実際の差分には含まれていません」と事実を対比して示す |
| **2. 説明文にないサイレント変更** | PR 本文では「軽微な整理」とされているが、実 Diff で認可条件・デフォルト値・DB カラム制約・依存パッケージが変わっている | 見落とされないよう `## 全体像` と `## 主な変更ポイントと設計判断` の表舞台へ引き上げて解説する |
| **3. 未検証テスト主張の鵜呑み** | PR 本文で `- [x] 全テスト通過` と書かれているが、エージェント自身は未実行、またはテストに `.skip` が追加されている | 作成者の `[x]` を自分の確認結果として転記しない（`BR006`）。「PR 説明文ではテスト通過と記載されていますが、手元での実行結果は未確認です」と切り分ける |
| **4. 抽象クリシェによる曖昧化** | PR 本文が「堅牢性を向上」「包括的なエラー処理」などの抽象語で書かれている | そのまま引用せず、実 Diff から読み取った具体的な例外型・条件・返却ステータスへ言い換えて解説する（`BR005`） |

---

## 6. 規模判定（Size S / M / L / XL）と特殊 PR・巨大 PR 読解チャンク

規模判定は **`effective_lines = added + deleted`（`🧠 Must-Read` + `👀 Skim` のみ合算、`⏭️ Safe-to-Skip` は除外）** で行う。`summary.layers_touched` には把握用に `"tests"` と `"docs"` も含まれるが、Size S（単一機能レイヤー `<= 1`）および Size XL（多層横断 `>= 4`）のレイヤー数判定では `"tests"` と `"docs"` を除いた機能レイヤー数を用いる。Step 3 の具体的なテンプレート書式と Mermaid 安全規約は [mermaid-and-templates.md](mermaid-and-templates.md) を正本とする。

| Size | 判定条件（実質コード基準） | Chat 解説の出力方針 |
|---|---|---|
| **Size S** | `effective_files <= 2` かつ `effective_lines < 30` かつ 単一機能レイヤー（`risk_path` や `SUSPECTED_MANUAL_GEN_EDIT` を含まない） | **言葉中心の簡潔解説**（テンプレート正本は `SKILL.md`）。Mermaid 図・Scope バナー・Markdown 表・`<details>` は一切出力せず（`BR003`）、背景・変更内容・挙動変化・確認ポイントを文章と箇条書きで丁寧に説明する。依存追加がある場合は理由 1 行を添える。 |
| **Size M** | Size S を超え、`effective_files <= 10` かつ `effective_lines <= 300`（または 30 行未満でも複数レイヤー横断・リスク領域を含む） | **標準 Chat 図解解説**。全体像＋Scope バナー、仕組み・処理フロー図解（1〜2 図＋前後の言語解説）、主な変更ポイント、理解順の読む順番ガイド、必要時の注意・確認ポイントを出力する。 |
| **Size L** | Size S/M を超え、`effective_files <= 20` かつ `effective_lines <= 600` | **フル Chat 図解解説**。構造変化と処理フローの図解＋丁寧な言語解説、設計判断の比較、行レンジ・ボイラープレート読み飛ばしメモ付きの読む順番ガイド、`<details>` 折りたたみ、注意・確認ポイントを出力する。 |
| **Size XL** | `effective_lines > 600` または `effective_files > 20` または `estimated_review_minutes.max > 45` または（機能レイヤー数 `>= 4` かつ `effective_lines > 400`）※純削除 PR は除外 | **分割読解ガイド（Reading Chunks）付き Chat 図解解説**。すでに出されている巨大 PR を作り直させる提案は行わず、人間が段階的に読み進められるようテーマ別・チャンク別の読解順序を編成して案内する（Mermaid はモジュール単位に集約し 10 ノード以下厳守）。 |

### 6.1 Size XL 時の「分割読解ガイド（Reading Chunks）」編成ルール

すでに出されている PR が Size XL の場合、1 つの巨大な表を上から順に読ませると認知負荷が飽和する。以下の基準で既存 PR の変更ファイル群を **2〜4 個の読解チャンク（Reading Chunks）** に分けて案内する。

1. **1 チャンクの目安**: 3〜7 ファイル・実質 150〜250 行・推定読解時間 10〜15 分以内に収まるよう束ねる。
2. **チャンクの推奨順序**:
   - **Chunk 1（土台・データ契約）**: DB マイグレーション、スキーマ、共通型定義、設定値
   - **Chunk 2（中核ドメインロジック）**: ビジネスルール、状態遷移、外部連携クライアント
   - **Chunk 3（API 境界・UI 結線）**: ルーティング、コントローラ、画面コンポーネント
   - **Chunk 4（検証・エッジケース）**: テストスイート、境界値・異常系の確認
3. **記載内容**: 各チャンクごとに「このチャンクで確認すべき観点」「対象ファイルと注目行レンジ」「目安読解時間」を添え、チャンク単位で区切って休憩しながらレビューできるよう案内する。

### 6.2 特殊 PR（実質コード 0 行・削除中心等）の判定規則

| `special_case` / パターン | 判定条件 | Chat 解説の出力ルール |
|---|---|---|
| **差分 0 件** (`empty_diff`) | 比較対象の差分が 0 行 | 差分が 0 件である旨と、指定した `--base` / `--mode` / PR 番号の確認ポイントのみを簡潔に案内する（図表は出力しない）。 |
| **Docs / 設定のみ PR** (`docs_or_config_only`) | `*.md`, `*.mdx`, `docs/**`, リンター/ビルド設定等のみ変更 | Docs のみで `1〜2 ファイル かつ < 50 行` なら **Size S**、`>= 3 ファイル または >= 50 行` なら **Size M**（概念整理や構成変更がある場合のみ図解＋読む順番ガイド）。`.github/workflows/**`, `Dockerfile` 等の CI / 本番基盤変更は `Safe-to-Skip` には絶対に入れず、権限・秘密情報・ゲート変更がない単一ジョブの小修正（`1〜2 ファイル・< 30 行`）のみ **Size S** 可、それ以外は **Size M** として `flowchart LR` 等でジョブ依存・発火条件を図解解説する。 |
| **自動生成 / Lockfile 更新のみ PR** (`skip_only`) | 実質コード 0 行で `safe_to_skip` のみ変更 | 手動改変疑いがなければ **Size S 相当**（更新パッケージ名や再生成元・背景理由・確認ポイントのみを言葉で解説。対象ファイルが多く一覧を折りたたみたい場合のみ Size M 扱いで `<details>` を使用可）。手動改変疑い（`SUSPECTED_MANUAL_GEN_EDIT`）がある場合は **Size M** とし、`## 注意・確認ポイント` で該当箇所を警告する。 |
| **大量削除のみ PR** (`deletion_heavy`) | `deleted / effective_lines >= 0.8` かつ `added <= 30` | 削除は読解コストが低いため Size XL 扱いにしない。`1〜2 ファイル・< 30 行`（リスク領域なし）は **Size S**、`<= 10 ファイル かつ <= 300 行` は **Size M**、それ以上は **Size L** とし、Scope バナーの `+N行 / -M行`（客観事実）とともに「なぜ削除して安全か（残存参照の有無・公開 API や DB への影響）」を中心に解説する。 |
