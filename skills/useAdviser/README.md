# useAdviser Skill

下位エージェント（開発用LLMモデル）が、難度の高い設計判断やリスクの高いコード変更を行う際、動的に上位エージェント（アドバイザー）をSubagentとして呼び出し、適切な助言を得るための拡張スキル（Agent Skill）です。

---

## インストール

npx skills
```bash
npx skills add yharuto0917/useAdviser
```

gh command
```bash
gh skill install yharuto0917/useAdviser
```

## 📁 ディレクトリ構成

```text
useAdviser/
├── README.md                  # 本ドキュメント
├── SKILL.md                   # スキルのメインロジック（トリガーと実行フロー）
├── references/
│   ├── escalation-guide.md     # 7つの代表的なユースケースとエスカレーション判定基準
│   ├── prompt-templates.md     # アドバイザーへの質問用構造化プロンプトテンプレート（9パターン）
│   └── integration-patterns.md # 取得した助言をコードに安全に統合・反映するパターン
└── evals/
    └── evals.json              # スキルの動作とトリガー精度を検証するためのテストスイート
```

---

## ⚡ エスカレーショントリガー（起動条件）

以下のいずれかのトリガーを検知した際、自動的にエスカレーションプロセスを開始します。

1. **スキルの明示的呼び出し**: ユーザーが `/useAdviser` を指定してタスクを実行したとき。
2. **明示的な指示**: ユーザープロンプト内に「上位モデルに聞いて」「アドバイザーに相談して」等の文言が含まれるとき。
3. **タスクカテゴリの合致**: `.gemini/settings.json` などで特定のタスク（アーキテクチャ設計、セキュリティ監査等）に自動呼び出し設定がなされているとき。
4. **自己判断（Self-Assessment）**: 不確実性の高い技術選定、破壊的リスクの伴うコード変更、専門的なアルゴリズム実装などにおいて、モデル自身が助言を必要と判断したとき。

---

## 🛠️ 基本ワークフロー

1. **アドバイザー（Subagent）の定義**
   - 変更権限を持たない読み取り専用（`enable_write_tools: false`）の上位モデルを起動します。
2. **コンテキストの要約と質問の組み立て**
   - `references/prompt-templates.md` に沿って、背景・論点・制約条件を整理した質問を作成します。
3. **アドバイスの取得と適用**
   - 原則としてアドバイザーの推奨方針を採用し、コードに適用します。
4. **ユーザーへの結果報告**
   - アドバイザーに何を相談し、どのような推奨を得て、どうコードに反映したかを要約してユーザーに報告します。

---

## 🧪 スキルの評価方法

本スキルのトリガー精度や動作をテストするには、[skill-creator](file:///Users/yharuto/.gemini/config/skills/skill-creator) を使用して評価（Evaluation）を実行できます。

```bash
# 評価スイートの実行例
python3 -m scripts.run_eval \
  --eval-set evals/evals.json \
  --skill-path .
```
