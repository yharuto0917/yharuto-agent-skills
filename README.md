# yharuto-agent-skills

Claude Code・Gemini CLI・GitHub Copilot・Antigravity・Cursor などの AI コーディングエージェント向けに作成した [Agent Skills](https://agentskills.io/) コレクションです。

## インストール方法

### 対話的に選択・一括インストール

```bash
# npx skills を使用する場合
npx skills add yharuto0917/yharuto-agent-skills

# GitHub CLI (gh skill) を使用する場合
gh skill install yharuto0917/yharuto-agent-skills
```

### 特定のスキルを指定してインストール

```bash
# npx skills
npx skills add yharuto0917/yharuto-agent-skills --skill <skill-name>

# GitHub CLI (gh skill)
gh skill install yharuto0917/yharuto-agent-skills <skill-name>
```

> 例: `fable-code-review` をインストールする場合
> ```bash
> npx skills add yharuto0917/yharuto-agent-skills --skill fable-code-review
> ```

---

## 収録スキル一覧

| スキル名 | 概要 | 同梱リソース |
| :--- | :--- | :--- |
| [`agent-handoff-elicitation`](./skills/agent-handoff-elicitation) | 別エージェント・別スレッドへの引き継ぎ時に、目的・未完了事項・判断理由・検証状況・次の一手を高密度に抽出するスキル | `evals/` |
| [`ai-sdk`](./skills/ai-sdk) | Vercel AI SDK を用いたチャット UI・ストリーミング・構造化出力・Tool Calling・RAG・MCP 連携の実装ガイド | `references/` |
| [`clipboardCopy`](./skills/clipboardCopy) | macOS の `pbcopy` と同梱スクリプトを使い、エスケープ問題を回避して安全にテキストやコード断片をコピーするスキル | `scripts/` (Py / Sh / JS), `evals/` |
| [`commit-message`](./skills/commit-message) | `git diff` / `git status` から Conventional Commits・Semantic Commits・日本語慣習に沿ったコミットメッセージを生成するスキル | `references/` |
| [`fable-code-review`](./skills/fable-code-review) | 違和感の検知から仮説検証・確定/棄却・重要度付けまで、コードレビューにおける判断の型を提供するスキル | - |
| [`google-genai`](./skills/google-genai) | Python 版 `google-genai` SDK による Gemini API（テキスト生成・マルチモーダル・Function Calling・ストリーミング）実装ツールキット | `references/`, `scripts/` |
| [`useAdviser`](./skills/useAdviser) | 高難度な設計判断やリスクの高い変更時に、読み取り専用の上位モデルを Subagent（アドバイザー）として呼び出し助言を得るスキル | `references/`, `evals/` |
| [`yharuto-presentation-skill-for-slidev`](./skills/yharuto-presentation-skill-for-slidev) | パステルアクセントの独自デザインシステムを Slidev デッキ（Vite / Vue）に適用するプレゼンテーション作成スキル | `assets/` (CSS / Vue) |

---

## ディレクトリ構成

Agent Skills 標準の `skills/<skill-name>/SKILL.md` 構成を採用しています。

```text
yharuto-agent-skills/
├── LICENSE
├── README.md
└── skills/
    ├── agent-handoff-elicitation/
    ├── ai-sdk/
    ├── clipboardCopy/
    ├── commit-message/
    ├── fable-code-review/
    ├── google-genai/
    ├── useAdviser/
    └── yharuto-presentation-skill-for-slidev/
```

---

## 手動配置で利用する場合

CLI を使わずに手動で導入する場合は、本リポジトリをクローンして任意のスキルディレクトリをお使いのエージェントのスキル読み込み先へコピーしてください。

```bash
git clone https://github.com/yharuto0917/yharuto-agent-skills.git

# 例: 共通スキルディレクトリ (~/.agents/skills/) に配置する場合
mkdir -p ~/.agents/skills
cp -R yharuto-agent-skills/skills/<skill-name> ~/.agents/skills/
```

## ライセンス

[MIT License](./LICENSE)
