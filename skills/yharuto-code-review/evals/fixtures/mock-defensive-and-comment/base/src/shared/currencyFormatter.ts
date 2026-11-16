/**
 * 全画面共通（注文履歴・請求書PDF・管理画面・決済画面）の汎用日本円フォーマッタ。
 */
export function formatCurrencyJpy(amountJpy: number): string {
  if (!Number.isFinite(amountJpy)) {
    throw new RangeError("amountJpy must be a finite number");
  }
  const rounded = Math.trunc(amountJpy);
  return `¥${rounded.toLocaleString("en-US")}`;
}
