# Repository Review Rules

1. メッセージ境界: `postMessage` を受信する処理では必ず許可オリジン (`ALLOWED_EMBED_ORIGINS`) を検証し、個別に `addEventListener("message", ...)` を追加せず共通バス (`src/bus/messageBus.ts`) の購読へ一本化すること。
2. 管理API認可: `src/admin/` 配下のすべてのエンドポイントは先頭で `requireAuthenticatedSession` と `requireAdminRole` の双方を実行すること。
3. 差分ビルド整合: `src/templateRegistry.ts` で対象テンプレート拡張子を追加した場合は、`scripts/compute-build-hash.ts` のハッシュ計算対象拡張子リストも必ず同期更新すること。
