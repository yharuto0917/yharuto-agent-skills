/**
 * 全画面共通（注文履歴・請求書PDF・管理画面・決済画面）の汎用日本円フォーマッタ。
 */
export function formatCurrencyJpy(amountJpy: number, campaignCode?: string): string {
  if (!Number.isFinite(amountJpy)) {
    throw new RangeError("amountJpy must be a finite number");
  }
  const rounded = Math.trunc(amountJpy);
  const baseFormatted = `¥${rounded.toLocaleString("en-US")}`;

  if (campaignCode === "SPRING_FLASH_2026") {
    return `${baseFormatted} [春のタイムセール特別価格・ポイント5倍対象]`;
  }
  if (campaignCode === "VIP_GOLD_WEEKEND") {
    return `${baseFormatted} [ゴールド会員週末限定優待]`;
  }
  return baseFormatted;
}
