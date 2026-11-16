# Gate 1: Silent Code Diet 専門辞書

Gate 1（Silent Code Diet）は、PR 作成前またはレビュー依頼前にブランチ差分から AI 特有の冗長コード（AI スロップ）・重複実装・深いネストを整理し、不要な周辺変更を切り離す工程である。

> [!IMPORTANT]
> **Mode B（Read-Only 本文生成モード）時は本ドキュメントに基づくコード編集を行わない。**
> Gate 1 はローカル作業ブランチを編集できる Mode A でのみ実施する。また、Gate 1 で削減した行数やリファクタ成果を PR 本文へ記載することは厳禁とする（[japanese-pr-style.md](japanese-pr-style.md) および `pr_body_lint.py` の `PR006` で禁止）。

## 目次

- [1. 作業ツリー状態別の安全保護](#1-作業ツリー状態別の安全保護)
- [2. 車輪の再発明の探索手順](#2-車輪の再発明の探索手順)
- [3. AI スロップ 6 類型（Before / After）](#3-ai-スロップ-6-類型before--after)
- [4. 無関係変更の扱いとテスト保護規則](#4-無関係変更の扱いとテスト保護規則)
- [5. スタック別検証コマンド探索順と失敗時ロールバック](#5-スタック別検証コマンド探索順と失敗時ロールバック)
- [6. コミット前承認プロトコルと削減アピール厳禁](#6-コミット前承認プロトコルと削減アピール厳禁)

---

## 1. 作業ツリー状態別の安全保護

Gate 1 着手前に `git status --porcelain` と `git diff --name-only` を実行し、作業ツリーの状態に応じた保護手順を取る。

| 作業ツリーの状態 | 着手前の保護手順 | 編集範囲の制約 | 問題発生時の巻き戻し方法 |
|---|---|---|---|
| **クリーン**（変更がすべてブランチ上にコミット済み） | 追加退避は不要 | `<merge-base>`〜`HEAD` の変更ファイル内にある変更関数・近傍ハンクのみ | `git restore -- <file>` で Gate 1 編集のみ即復元 |
| **ダーティ**（staged / unstaged / untracked が存在） | `git stash create` で作業ツリーを壊さないスナップショット SHA を記録し、必要に応じて退避パッチを控える | `<merge-base>`〜作業ツリーの変更関数・近傍ハンクのみ | 退避スナップショットから該当ファイルのみ復元し、事前パッチを再適用 |

- `git checkout -- .` や `git reset --hard` はユーザーの未保存作業を消すため使用しない。
- 編集対象ファイル内であっても、今回ブランチの `git diff` に含まれない既存レガシーコードへの理由なきリファクタは行わない。

---

## 2. 車輪の再発明の探索手順

AI が既存モジュールを参照せずに新規ヘルパー・型・定数・エラークラスを複製した場合、以下の 4 手順で既存資産へ統合する。

1. **変更差分の新規シンボルを列挙する**:
   - `<merge-base>` との差分から追加された関数名・型定義・定数・バリデーション・エラークラスを抽出する。
2. **リポジトリ横断検索で既存実装を探す**:
   - `rg` 等で同名の機能キーワード（例: `formatDate`, `normalize`, `ValidationError`, `RETRY_LIMIT`, `parseToken`）や共通ディレクトリ（`utils/`, `shared/`, `common/`, `lib/`, `errors/`）を検索する。
3. **統合先の呼び出し規約とテスト参照を確認する**:
   - 既存ヘルパーが見つかった場合は引数型・戻り値・例外仕様が一致するか確認する。
   - 薄いラッパーや重複ヘルパーを削除・置換する前に、テストファイルを含む全参照箇所を検索し、テストが旧ヘルパーを直接呼んでいる場合は統合先のテストへ更新または確認する。
4. **重複定義を削除し既存ヘルパーへ差し替える**:
   - 既存実装が仕様を満たさない場合は重複を作らず、互換性を壊さない形で既存ヘルパーまたは型を拡張できるか検討する（変更波及が大きい場合はユーザーへ確認する）。

---

## 3. AI スロップ 6 類型（Before / After）

Gate 1 で削除・整理する典型的な AI スロップ 6 類型を示す。いずれも外部仕様を変えない範囲で実施し、削除後に必ず型チェックとテストで安全を確認する。

### 類型 1: 到達不能な `null` / `undefined` チェック

型システムまたは呼び出し元で非 null が保証されている値に対する冗長な防衛分岐を削除する。

```typescript
// Before: 引数 user は必須型であり呼び出し元でも検証済み
function formatDisplayName(user: { id: string; name: string }): string {
  if (!user) {
    return "unknown";
  }
  if (user.name === undefined || user.name === null) {
    return user.id;
  }
  return `${user.name} (${user.id})`;
}

// After: 型契約に沿った単一責務の記述
function formatDisplayName(user: { id: string; name: string }): string {
  return `${user.name} (${user.id})`;
}
```

### 類型 2: 要件外の無意味フォールバック

異常入力や設定欠落時に根拠のないデフォルト値（空文字、`0`、`[]`、`null`）を返して不具合を隠す分岐を削り、本来の失敗経路または既存バリデーションに従わせる。

```python
# Before: token 未設定でも空文字を返して後続で不可解な認証失敗を起こす
def load_signing_secret(env: dict[str, str]) -> str:
    secret = env.get("JWT_SECRET")
    if not secret:
        return ""
    return secret

# After: 設定必須を明示して失敗箇所を即時特定できる形にする
def load_signing_secret(env: dict[str, str]) -> str:
    secret = env.get("JWT_SECRET")
    if not secret:
        raise KeyError("JWT_SECRET is required")
    return secret
```

### 類型 3: 握りつぶし / 再 throw のみの `try-catch`

例外をログにも返却値にも残さず破棄する `catch`、意味のない再送出だけの `try-catch` を削除し、呼び出し側の統一エラー境界へ委ねる。

```python
# Before: 例外を握りつぶす / 何も変換せず再送出するだけ
def save_order(repo, order):
    try:
        return repo.insert(order)
    except Exception:
        return None

def fetch_profile(client, user_id: str):
    try:
        return client.get_user(user_id)
    except ValueError as exc:
        raise exc

# After: 変換や補償処理が不要なら try-except を置かない
def save_order(repo, order):
    return repo.insert(order)

def fetch_profile(client, user_id: str):
    return client.get_user(user_id)
```

### 類型 4: 1 箇所呼び出しの薄いラッパー・使い捨て中間変数

1 箇所からしか呼ばれない単純移譲関数や、1 度しか参照されない意味の薄い中間変数をインライン化する。

```typescript
// Before: 1箇所専用の薄いラッパーと使い捨て変数
function buildAuthHeaders(token: string): Record<string, string> {
  return createBearerHeader(token);
}

function createBearerHeader(token: string): Record<string, string> {
  const headerValue = `Bearer ${token}`;
  return { Authorization: headerValue };
}

// After: 直接表現する（公開APIや複数箇所で共有する関数は残す）
function buildAuthHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}
```

### 類型 5: What をなぞる自明なコメント

コードを読めば即座に分かる動作説明コメントを削除し、設計上の制約やワークアラウンド理由（Why）のみを残す。

```python
# Before: コードの字面を日本語にしただけのコメント
# ユーザーIDが空か確認する
if not user_id:
    raise ValueError("user_id must not be empty")
# レート制限用のキャッシュTTLを60秒に設定する
CACHE_TTL_SECONDS = 60

# After: 自明コメントを消し、非自明な制約理由だけを残す
if not user_id:
    raise ValueError("user_id must not be empty")
# 上流APIのバースト窓（60秒）と一致させる必要があるため60秒固定
CACHE_TTL_SECONDS = 60
```

### 類型 6: 3 段以上の深いネスト（早期リターンによる平坦化）

条件分岐が 3 段以上入れ子になっている処理をガード節と早期リターンへ書き換え、正常系を最浅インデントへ揃える。

```typescript
// Before: 4段ネストで正常系の条件を追いにくい
function canApproveOrder(order: Order | null, actor: User): boolean {
  if (order) {
    if (order.status === "pending") {
      if (actor.role === "admin" || order.ownerId === actor.id) {
        if (order.items.length > 0) {
          return true;
        }
      }
    }
  }
  return false;
}

// After: 早期リターンで条件を上から順に判定する
function canApproveOrder(order: Order | null, actor: User): boolean {
  if (!order || order.status !== "pending" || order.items.length === 0) {
    return false;
  }
  return actor.role === "admin" || order.ownerId === actor.id;
}
```

---

## 4. 無関係変更の扱いとテスト保護規則

1. **無関係な周辺変更（未コミット分・コミット済み別タスク分）を無断で削除しない**:
   - 本題のタスクと無関係な `README.md` の書き換え、別モジュールのフォーマット差分、実験用編集を見つけた場合でも、**無断で `git restore` や `git reset` を行わない**。
   - 対象ファイルと変更概要をチャットで提示し、「この PR から差し戻すか / 別ブランチへ退避するか / このまま含めるか」の判断をユーザーに確認する。
2. **テストコードの無断削除・スキップ・コメントアウト禁止**:
   - テストが通らないことを理由にアサーションを削る、`.skip` / `@unittest.skip` / `pytest.mark.skip` を付ける、またはテストをコメントアウトする行為は禁止する。
   - 重複ラッパー統合によって参照先が変わるテストは、削除ではなく統合先関数を呼ぶ形へ更新し、判断が分かれる場合は事前確認する。

---

## 5. スタック別検証コマンド探索順と失敗時ロールバック

Gate 1 の整理を終えたら、以下の順でリポジトリ内の設定ファイルから検証コマンドを特定し、**型チェック → リンター/フォーマッター → テスト** の順で実行する。

| スタック | 探索する設定ファイル | 確認すべきコマンド例（存在するもののみ実行） |
|---|---|---|
| **JavaScript / TypeScript** | `package.json` (`scripts`), `tsconfig.json` | `pnpm/npm run typecheck` (`tsc --noEmit`) → `lint` (`eslint` / `biome`) → `test` |
| **Python** | `pyproject.toml`, `setup.cfg`, `tox.ini` | `mypy` / `pyright` → `ruff check` / `flake8` → `pytest` / `python3 -m unittest` |
| **Rust** | `Cargo.toml` | `cargo check` → `cargo clippy` → `cargo test` |
| **Go** | `go.mod` | `go vet ./...` → `golangci-lint run` → `go test ./...` |
| **共通 / その他** | `Makefile`, `.github/workflows/*.yml` | CI 定義内の `check` / `lint` / `test` ターゲット |

### 失敗時のロールバックと切り分け規則

| 状況 | 判定と対処 |
|---|---|
| **Gate 1 編集後に失敗し、該当箇所の復元で通過した** | 削除した分岐やラッパーが実行時境界や型契約上必要だったと判断し、**該当ファイルまたはハンクのみ即復元**して再検証する。復元した箇所は「意図的保持」として再編集しない。 |
| **Gate 1 編集前から失敗していた** | 編集前後のエラー内容を比較し、新規の悪化がなければ継続する。ただし Gate 3 の検証欄では実行結果を偽らず `[ ]` または失敗の事実のまま記録する。 |
| **テスト環境や外部依存が未整備で実行不能** | 動作を変える可能性のあるリファクタ（ラッパー統合・ネスト展開）は控え、静的型・未使用コード・自明コメントの整理のみに留める。未実行コマンドを Gate 3 で `[x]` にしない。 |

---

## 6. コミット前承認プロトコルと削減アピール厳禁

1. **無断 `git commit` / `git push` / `gh pr create`・`edit` の禁止**:
   - 検証通過後も自動でコミットや push を実行せず、確認フローと提示すべき 4 項目の書式は [SKILL.md](../SKILL.md)（「1.3 検証・失敗時ロールバック・コミット前承認プロトコル」）に従う（未コミットのまま進む場合は Gate 2 を `--mode all` で実行する）。
2. **PR 本文での削減量アピール厳禁**:
   - Gate 1 で何行削除したか、コードダイエットの成果、削減前後の行数比較グラフ（`pie` や `xychart-beta` 等）は **PR 本文に一切書かない**。
   - Gate 1 は提出前に静かに済ませる前処理であり、PR 本文はレビュアーが現在の差分の意味と読む順番を把握するためだけに使う。
