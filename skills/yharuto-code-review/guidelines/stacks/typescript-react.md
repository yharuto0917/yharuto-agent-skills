# TypeScript / React / フロントエンド監査基準

TypeScript、JavaScript、React、Next.js、Astro 等の画面・クライアント変更時に読み込む基準である。単体テスト環境（JSDOM 等）と実ブラウザの挙動差、React のフック制約、SSR/CSR 境界、型絞り込みの漏れを重点的に確認する。

## 1. 実ブラウザDOM・CSSとJSDOMモックの境界

JSDOM や簡易 DOM モックはレイアウト計算やビューポート切り抜き・重なり順を行わないため、テスト通過を根拠に画面動作を安全とみなさない。必ずコンポーネントの HTML/JSX 構造、CSS、クライアント処理、テストモックを並べて次の4点を確認する。

1. モーダル・ドロワー・ポップオーバーやブラウザ固有のビューポート占有表示APIでは、指定した対象要素とその子孫だけが有効領域になり外側の兄弟要素が描画・操作対象から外れることがある。内側の部分要素だけを対象に指定して外側の兄弟要素にある操作部品やフッターボタンが隠れる不具合（`🔴 Bug`）がないか、必要な操作系を含む外枠要素を対象にしているかを確認する。
2. 親要素の `overflow: hidden` によるドロップダウンやメニューの切り抜き、`transform` / `filter` 指定要素が作る包含ブロックによる `position: fixed` の基準ズレ、`z-index` の重なり順競合、モーダル閉鎖時の背面スクロール固定スタイルの解除漏れがないかを確認する。
3. `fn.toString()` で関数を文字列化して HTML へ埋め込む手法や生スクリプト文字列での処理再実装は、型検査・リンターの対象外になりバンドル時の識別子変換で未定義参照エラーを招くため `🟡 Risk`（実行時エラー発火時は `🔴 Bug`）とし、ビルド対象スクリプトの読込または `data-*` 属性渡しへ統一させる。
4. テストの簡易モックに標準メソッドが未定義なことだけを理由に本番コードへ `typeof el.classList?.toggle === "function"` 等の存在判定を足している場合は `🟡 Risk` とし、本番の不要分岐を削除してテストモック側を補う方針を示す（SSR の `typeof window !== "undefined"` や実在するブラウザ互換差の機能検出は正当として除外する）。

## 2. React Rules of Hooks と useEffect の対称性

1. `useState`、`useEffect`、`useMemo`、`useCallback`、`useRef`、カスタム `use*` フックより前に条件付きの早期 `return` を置いてはならない。全 Hook 呼び出しの直後にガード節が置かれているコードは正しく、「関数先頭へ早期 `return` を移動せよ」という誤った提案は絶対に行わない。
2. `useEffect` 内部で参照する state / props の依存配列入れ忘れによる古い値の参照（Stale Closure）、または毎レンダー新しく生成されるオブジェクト・配列・関数を依存に入れた無限再実行ループがないかを確認する。
3. `addEventListener`、タイマー、`ResizeObserver`、`BroadcastChannel`、外部購読、非同期 `fetch` を行う `useEffect` に、アンマウント時および依存変更時の解除・中断（`AbortController` 等）があり、Strict Mode の再マウントでも二重登録にならないかを確認する。
4. props や既存 state からレンダー中に同期計算できる派生値を `useEffect` 内の `setState` で更新し、余分な再描画や一時的な表示ズレを生んでいないかを確認する。

## 3. SSR / CSR 境界と Server / Client Components

- 初回 SSR 出力とクライアント側ハイドレーションで値が変わる処理（`Date.now()`、`Math.random()`、ロケール依存の日時整形、`window` / `localStorage` の直接分岐）をマウント完了前に JSX へ描画し、ハイドレーション不一致エラーを起こしていないかを確認する。
- Next.js App Router 等で Server Components から Client Components へシリアライズ不能な値（関数、クラスインスタンス等）を渡していないか、また `"use client"` 側からサーバー専用の秘密情報や DB モジュールをインポートしていないかを確認する。

## 4. 厳密な型絞り込みと公開仕様の保護

- 信頼境界（`JSON.parse`、`fetch` 応答、`postMessage` の `event.data`、URL クエリ、`localStorage`）の値を `as SomeType` や `as any` で直接通さず、`unknown` からスキーマ検証または型ガードで絞り込んでいるかを確認する。
- 判別可能なユニオン型の網羅漏れ、必須値を誤魔化す無根拠な `?? ""` / `?? 0`、未待機の `.forEach(async ...)` を確認する。配列の Nullish 除去を提案する際は `0` や `""` まで消える `.filter(Boolean)` を禁止し、必ず `.filter((x) => x != null)` を使う。
- 既存の公開 Props 名、`export` された関数・型名、外部 API JSON のキー名（例として `legacyWebhookDeliveryId`）に対する改名指摘は呼び出し元との互換性を損なうため行わない。
