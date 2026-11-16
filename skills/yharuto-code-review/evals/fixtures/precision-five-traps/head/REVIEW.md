# Repository Review Guidelines

1. 境界検証: リクエスト入力の型・形式検証は公開ルーター (`src/router.ts`) で行い、検証済みの値のみを受け取る `src/internal/` 配下の内部関数では同じ検証を重複させない。
2. 外部決済APIレート制限: `InvoiceGateway` は1アカウントあたり同時実行数 `1`・リクエスト順序厳守の制限があるため、複数請求書の取得は必ず直列 `for...of await` で実行する（`Promise.all` による並列化は `429 Too Many Requests` を誘発するため禁止）。
3. 公開APIスキーマ互換: 外部パートナー連携で参照されるレスポンスフィールド `customerAccountDataList` のキー名は後方互換性のため変更しない。
