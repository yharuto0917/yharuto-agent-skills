# Gate 2: Diff Sizing & Signal/Noise Triage 専門辞書

Gate 2 は `git diff` の変更を「人間が熟読すべき実質コード」と「自動生成・機械置換などのノイズ」に仕分けし、PR 規模（Size S / M / L / XL）とテンプレート統合方針を確定する工程である。

## 目次

- [1. ベースブランチ自動特定手順](#1-ベースブランチ自動特定手順)
- [2. `pr_diff_triage.py` の実行モード・JSON 読解・AI オーバーライド基準](#2-pr_diff_triagepy-の実行モードjson-読解ai-オーバーライド基準)
- [3. 3 階層トリアージ完全辞書と強制昇格ルール](#3-3-階層トリアージ完全辞書と強制昇格ルール)
- [4. Size S / M / L / XL 確定閾値表と特殊 PR 判定規則](#4-size-s--m--l--xl-確定閾値表と特殊-pr-判定規則)
- [5. 既存 PR テンプレート探索順と統合モード](#5-既存-pr-テンプレート探索順と統合モード)

---

## 1. ベースブランチ自動特定手順

`main` へのハードコードおよび `@{u}` の参照を禁止する。すでに `origin/<feature>` へ push 済みのブランチでは `@{u}` が自分自身を指して `merge-base` 差分が 0 になるためである。

以下の優先順でベースブランチを特定する。

1. **ユーザー明示指定**: `--base <ref>` や「`develop` 向け PR」等の指定を最優先する。
2. **既存 PR メタデータ（SKILL 側での事前取得）**: `gh` CLI が利用でき現在ブランチに紐づく PR がある場合は、`gh pr view --json baseRefName -q .baseRefName` で取得した値を `--base` へ渡す（`pr_diff_triage.py` 本体はオフライン/ローカル Git 完結であり `gh` を呼ばない）。
3. **リモートデフォルトブランチ参照**: `git symbolic-ref refs/remotes/origin/HEAD` から取得する。
4. **候補ブランチの最近接 `merge-base` 探索**:
   - 候補群 `["origin/main", "main", "origin/master", "master", "origin/develop", "develop", "origin/trunk", "trunk"]` のうち存在するものを列挙する。
   - **現在ブランチ（`git branch --show-current`）と同名のローカル候補は探索対象から除外する**（`main` 上での直作業時に距離 0 で自己比較になる罠を防ぐため）。
   - `git rev-list --count $(git merge-base <cand> <head>)..<head>` が最小となる候補を選ぶ（未コミット作業のみで距離 0 が同点の場合は上記列挙の固定順で決定する）。
5. **フォールバック**: デフォルトブランチ上の未コミット作業のみで他に比較対象がない場合は `HEAD` をベースとし、作業ブランチ作成が必要な旨を通知する。

---

## 2. `pr_diff_triage.py` の実行モード・JSON 読解・AI オーバーライド基準

### `--mode` オプションの使い分け

| `--mode` | 比較範囲 | 使う場面 |
|---|---|---|
| `committed`（既定） | `<base>...HEAD`（`merge-base` 基準の 3-dot 相当） | 変更がすべてブランチへコミット済みの通常 PR（Mode B 含む） |
| `working` | `HEAD` と作業ツリー（staged / unstaged / untracked） | コミット前の手元差分だけを確認したいとき |
| `all` | `merge-base(<base>, HEAD)` と作業ツリー＋未追跡ファイル | Gate 1 整理後、コミット承認待ちで未コミットのまま Gate 2〜3 へ進むとき |

### JSON 出力の主要項目

| キー | 内容と確認ポイント |
|---|---|
| `schema_version` / `meta` | スキーマ版、解決済み `base_ref`・`head_ref`・`merge_base_sha`・`mode` |
| `summary` | `total_*`（見かけ総差分）、`effective_*`（`must_read` + `skim` 合算）、各区分件数/行数、`noise_ratio_pct`、`provisional_size`（仮サイズ）、`size_reasons`、`special_case`、`layers_touched`、`estimated_review_minutes`（`min`/`max`） |
| `warnings` | `SUSPECTED_MANUAL_GEN_EDIT`、`NON_DEPENDENCY_MANIFEST_CHANGE`、`LOCKFILE_WITHOUT_MANIFEST`、`TEST_SKIP_OR_ASSERTION_REMOVAL` 等 |
| `dependencies` | マニフェスト別の `added` / `updated` / `removed` パッケージ一覧、`non_dependency_changes` |
| `files` | `must_read` / `skim` / `safe_to_skip` のファイル配列（`path`, `status`, `added`, `deleted`, `layer`, `reason`, `flags`） |
| `ai_override_guidance` | AI が Diff 本文を読んで最終判定すべき項目リスト（`must_verify`） |

### AI オーバーライド基準（スクリプトとの協調判断）

`pr_diff_triage.py` はパスと統計から仮分類・仮サイズを出す。最終確定時は実際の Diff を読んで以下の 5 点を判定・上書きする。

1. **微小な 1 行定数のリスク変更**:
   - 例: `src/config/timeout.ts` の `3000` → `30` のような 1 行変更は統計上小さくても障害リスクが高いため、`must_read` へ引き上げ、Size S に丸めない。
2. **1 ファイル内の複数責務混在と実質単一変更**:
   - 1〜2 ファイルかつ 30 行未満でも、1 ファイル内に認証・DB・UI 境界が混在していれば複数レイヤー扱い（Size M）へ引き上げる。
   - 逆に、型定義と実装の 2 ファイル構成で実質単一バグ修正なら Size S を維持してよい。
   - `.github/workflows/` は安全のため初期分類が `must_read` になるが、権限・秘密情報・デプロイ・CI ゲートを変えない 1〜2 ファイル 30 行未満の軽微修正なら AI 判断で Size S 化できる。
3. **テストの `.skip` やアサーション削除検知**:
   - テストファイルで `.skip` / `@unittest.skip` / `pytest.mark.skip` の追加やアサーション削除のみ（`TEST_SKIP_OR_ASSERTION_REMOVAL`）がある場合、`skim` から `must_read` へ強制昇格し確認事項として扱う。
4. **外部スキーマ由来再生成の除外理由**:
   - リポジトリ内にスキーマ元がなく `SUSPECTED_MANUAL_GEN_EDIT` 警告が出た場合、Diff 本文から外部 API スキーマ等に基づく正当な機械再生成と確認できたときのみ警告を解除し、`<details>` 等に再生成元を 1 行明記する。実際に手動改変がある場合は `must_read` を維持し `> [!CAUTION]` で警告する。
5. **依存パッケージの導入理由抽出**:
   - `dependencies` に抽出された `added` / `updated` パッケージについて、コード上の利用箇所から導入・更新理由を 1 行に要約して PR 本文へ必ず記載する。

---

## 3. 3 階層トリアージ完全辞書と強制昇格ルール

| 階層 | 対象ファイル・変更内容 | 強制昇格・抽出ルール |
|---|---|---|
| `🧠 Must-Read` | コアビジネスロジック、認証・認可・暗号・秘密情報（`auth`, `oauth`, `jwt`, `session`, `permission`, `rbac`, `crypto`, `secret`）、課金・決済・Webhook、状態変更、手書き DB スキーマ・SQL マイグレーション（`*.sql`, `schema.prisma`, `migrations/`, `prisma/`）、公開 API 契約（`openapi`, `routes/`, `controllers/`）、`.github/workflows/**`・`Dockerfile`・`*.tf` 等の本番デプロイ設定 | **強制昇格対象**: ①自動生成ファイルへの手動改変疑い、②依存マニフェストの `scripts` / `exports` / `bin` 改変、③テストファイルでの `.skip` 追加またはアサーション削除のみ |
| `👀 Skim` | テストコード（健全な追加・更新: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `tests/`, `__tests__/`, `e2e/`）、ルーティング・DI の単純結線、ドキュメント（`*.md`, `*.mdx`, `docs/`）、リンター/IDE 設定、純粋 CSS/スタイル、純粋リネーム（`R` かつ `0/0`）、非リスクコードの全削除（`D`）、マニフェストの `scripts`/`exports`/`bin` 以外の非依存キー変更 | `.skip` 追加やアサーション削除のみを検知したら `🧠 Must-Read` へ昇格 |
| `⏭️ Safe-to-Skip` | **Lockfile 各種**: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `bun.lock`, `Cargo.lock`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Gemfile.lock`, `composer.lock`, `go.sum`, `Podfile.lock`, `flake.lock`, `mix.lock`<br>**Snapshot**: `*.snap`, `*.snap.new`, `__snapshots__/**`<br>**自動生成コード**: `*.gen.*`, `*.generated.*`, `*.g.dart`, `*.freezed.dart`, `*.pb.go`, `*_pb2.py`, `*_pb2_grpc.py`, `**/generated/**`, `**/__generated__/**`<br>**Drizzle / Prisma 自動生成メタデータ**: `drizzle/meta/**`（`_journal.json`, `*_snapshot.json`）, `prisma/migrations/migration_lock.toml`, `**/generated/prisma/**`（※`*.sql` と `schema.prisma` は除外し `Must-Read` 固定）<br>**ビルド成果物・ベンダー**: `**/dist/**`, `**/build/**`, `**/out/**`, `**/.next/**`, `**/.nuxt/**`, `**/target/**`, `**/*.tsbuildinfo`, `*.min.js`, `*.min.css`, `*.bundle.js`, `*.map`, `**/vendor/**`<br>**機械的 import パス一括置換のみ**: 全変更行が `import` / `export ... from` / `from ... import` / `require(` / Rust `use` のパス変更と空行のみで構成（ロジック変更 0 行）<br>**依存マニフェスト**: `package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `Gemfile`, `composer.json`, `pom.xml`, `build.gradle*` | **安全ルール**:<br>1. 依存マニフェストの追加・更新パッケージ名と導入理由は 1 行で必ず抽出明記する<br>2. 同一リポジトリ全体で対応マニフェスト未変更のまま lockfile のみ変わっている場合は `LOCKFILE_WITHOUT_MANIFEST` 警告を出す<br>3. `import` パス置換に 1 行でもロジック変更が混在する場合は `Must-Read` へ戻す<br>4. 生成ヘッダ（`DO NOT EDIT`, `@generated`, `auto-generated`, `Generated by`, `Code generated by`）が先頭 30 行またはパスに一致し、スキーマ元（`*.proto`, `*.graphql`, `*.gql`, `openapi.*`, `swagger.*`, `schema.prisma`, `codegen.*`, `drizzle.config.*`）未変更・ヘッダ削除・`TODO`/`FIXME`/`HACK`/`manual`/`console.log`/`debugger` 混入・局所ハンクのいずれかがあれば **`Must-Read` へ強制昇格**し `SUSPECTED_MANUAL_GEN_EDIT` 警告を出す |

---

## 4. Size S / M / L / XL 確定閾値表と特殊 PR 判定規則

規模判定は **`effective_lines = added + deleted`（`🧠 Must-Read` + `👀 Skim` のみ合算、`⏭️ Safe-to-Skip` は除外）** で行う。なお `summary.layers_touched` には把握用に `"tests"` / `"docs"` も含めて出力するが、Size S（単一レイヤー `<= 1`）と Size XL（`>= 4`）のレイヤー数判定では `"tests"` と `"docs"` を除いた機能レイヤー数を用いる。Gate 3 の具体的なテンプレート書式と Mermaid ルールは [mermaid-and-templates.md](mermaid-and-templates.md) を正本とする。

| Size | 判定条件（すべて実質コード基準） | 出力ルール |
|---|---|---|
| **Size S** | `effective_files <= 2` かつ `effective_lines < 30` かつ 単一レイヤー（認証・DB・ワークフロー等の `risk_path` や `SUSPECTED_MANUAL_GEN_EDIT` を含まない） | `## TL;DR`（最大 3 行）と `## 検証ステータス` のみ。**Mermaid 図・Markdown 表・Scope バナー・`<details>`・設計判断表はすべて出力禁止**。依存追加や lockfile を伴う場合のみ TL;DR 直下に `依存追加: <pkg>（理由1行）` を 1 行置ける。 |
| **Size M** | Size S を超え、`effective_files <= 10` かつ `effective_lines <= 300`（または 30 行未満でも複数レイヤー横断・危険領域を含む） | Visual-First 標準構成（必須要素＋該当する任意節のみ出力） |
| **Size L** | Size S/M を超え、`effective_files <= 20` かつ `effective_lines <= 600`（単一主題） | Visual-First フル構成（アーキテクチャ比較＋振る舞い図＋読む順表＋`<details>`＋設計判断表） |
| **Size XL** | `effective_lines > 600` または `effective_files > 20` または `estimated_review_minutes.max > 45` または（`layers_touched >= 4` かつ `effective_lines > 400`）※純削除 PR は除外 | **PR 分割提案（Split Proposal）を先に出力し、続けて `> [!WARNING]` 付きの 1 本版 PR 本文ドラフトも同時出力する**。Mermaid は責務単位で最大 10 ノードに収める。 |

### 特殊 PR（実質コード 0 行・削除中心）の例外規則

| `special_case` / パターン | 判定条件 | 出力ルール |
|---|---|---|
| **Docs のみ PR** | `*.md`, `*.mdx`, `docs/**` 等のみ変更 | `1〜2 ファイル かつ < 50 行` なら **Size S**。`>= 3 ファイル または >= 50 行` なら **Size M**（必要時のみ概念図＋読む順表）。 |
| **CI / Build / Infra 設定のみ PR** | `.github/workflows/**`, `Dockerfile`, `tsconfig.json` 等のみ変更 | `Safe-to-Skip` には絶対入れない。権限・秘密情報・ゲート変更がない単一ジョブ小修正（`1〜2 ファイル・< 30 行`）なら AI 判断で **Size S** 可、それ以外は **Size M**（`flowchart LR` で依存/分岐を図解）。 |
| **自動生成 / Lockfile 更新のみ PR** (`skip_only`) | 実質コード 0 行で `safe_to_skip` のみ変更 | 手動改変疑いがなければ **Size S 相当**（更新パッケージ名・再生成元・理由 1 行＋検証結果のみ。図表・`<details>` 禁止。ファイル一覧を折りたたみたい場合のみ Size M 扱い）。手動改変疑いがあれば **Size M**＋`> [!CAUTION]` 警告。 |
| **大量削除のみ PR** (`deletion_heavy`) | `deleted / effective_lines >= 0.8` かつ `added <= 30` | 削除は読解コストが低いため Size XL 分割を強制しない。`1〜2 自己完結ファイル・< 30 行`（リスク領域なし）は **Size S**、それ以上で `<= 10 ファイル かつ <= 300 行` は **Size M**、300 行超または 11 ファイル以上は **Size L** と判定し、出力本文は Size M/L 共通テンプレートで Scope バナーの `+0行 / -N行`（事実表示）と削除理由・参照 `grep` 結果を記載する（公開 API / DB 削除は `Must-Read`）。 |

---

## 5. 既存 PR テンプレート探索順と統合モード

リポジトリに既存の PR テンプレートがある場合は、以下の優先順で探索し、同時に `.github/workflows/*.yml` 等に PR 本文の見出し・チェックリストを強制する CI（Danger / pr-lint 等）があるか確認する。

1. `.github/pull_request_template.md`（大文字小文字 `PULL_REQUEST_TEMPLATE.md` 含む）
2. `.github/PULL_REQUEST_TEMPLATE/*.md`（複数ある場合は変更種別に合うものを選択、または確認）
3. `docs/pull_request_template.md`

### テンプレート項目の 3 分類マッピング

- **必須メタ・Bot フック（必ず保持して埋める）**: Issue 連携（`Closes #...`）、チケット ID、破壊的変更チェック、DB マイグレーション/リリースチェックボックス、スクリーンショット枠。
- **意味的重複セクション（Visual-First 構成へ昇格統合）**: `Summary / 概要` → `TL;DR`＋Scope バナー、`Changes / 変更内容` → アーキテクチャ比較＋差分トリアージ、`Testing / テスト` → 検証ステータス。
- **入力ガイド用 HTML コメント**（`<!-- Describe your changes -->` 等）: 原則削除する（Bot が解釈する専用マーカーコメントのみ残す）。

### 統合モード（`merge` vs `strict`）

| モード | 適用条件 | 統合方法と `pr_body_lint.py` 連携 |
|---|---|---|
| **`merge`（既定）** | 見出し固定を強制する CI がない場合 | Human-First PR 標準構成を骨格とし、元テンプレート固有の必須メタ情報や未確認チェックリスト（未検証項目は必ず `[ ]` のまま）を TL;DR 直下または末尾へ欠落なく引き継ぐ（`--template <path> --template-mode merge`）。 |
| **`strict`（強制）** | PR 本文の見出し一致を検査する CI がある場合、またはユーザー指定時 | 元テンプレートの **見出し名・数・順序を変更せず**、各見出しの配下に TL;DR・Scope バナー・Mermaid・読む順表・`<details>`・`[x]`/`[ ]` を差し込む（`--template <path> --template-mode strict`）。 |
