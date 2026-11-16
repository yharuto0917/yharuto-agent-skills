export function calculateDiscountedConsumptionTaxJpy(
  subtotalJpy: number,
  discountJpy: number,
  taxRatePercent = 10,
): number {
  if (subtotalJpy < 0 || discountJpy < 0 || taxRatePercent < 0) {
    throw new RangeError("Amounts and tax rate must be non-negative");
  }
  const netAmountJpy = Math.max(0, subtotalJpy - discountJpy);
  return Math.round((netAmountJpy * taxRatePercent) / 100);
}
