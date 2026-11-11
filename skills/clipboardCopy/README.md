# Clipboard Copy Skill

Mac環境でAIエージェントがテキストやコードスニペットを安全にクリップボード（`pbcopy`）へコピーできるようにするAgent Skillです。

---

## インストール方法

### 1. npx (`npx skill`) によるインストール

`npx skills` (または `npx skill`) コマンドを使ってインストールします。

```bash
npx skills add yharuto0917/clipboard-copy-skill
```

*※ 環境によっては `npx skill add yharuto0917/clipboard-copy-skill` でも実行可能です。*

---

### 2. gh skill (`GitHub CLI`) によるインストール

GitHub CLIの標準スキル管理コマンド `gh skill` を使ってインストールします。

```bash
gh skill install yharuto0917/clipboard-copy-skill
```

---

## 概要・特長

- **安全なクリップボード操作**: macOSの `pbcopy` コマンドを使用し、安全にクリップボードへテキストをコピーします。
- **柔軟なコピー範囲指示**: 全文コピーだけでなく、特定関数の抽出コピーや整形後のテキストコピーにも対応。
- **多言語ヘルパースクリプトを同梱**: Python, Bash, Node.js のスクリプトを用意しており、シェル実行時のエスケープ問題を回避します。

---

## 使い方

AIエージェントに対して以下のように指示してください。

- 「直前の出力をクリップボードにコピーして」
- 「作成した `handleSubmit` 関数の部分だけコピーして」
- 「箇条書きに整形してからコピーして」

エージェントはユーザーの意図を汲み取り、適切なテキストを抽出・整形した上でヘルパースクリプトを実行し、クリップボードへ保存します。

---

## 動作要件

- **OS**: macOS (`pbcopy` コマンドが利用可能であること)
- **ランタイム**: Python 3 / Bash / Node.js のいずれか

---

## ライセンス

[MIT License](LICENSE)
