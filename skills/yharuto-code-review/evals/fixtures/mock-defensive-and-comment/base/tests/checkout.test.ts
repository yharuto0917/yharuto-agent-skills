import test from "node:test";
import assert from "node:assert/strict";
import { renderCheckoutSummaryText } from "../src/checkoutBadge.ts";
import { formatCurrencyJpy } from "../src/shared/currencyFormatter.ts";
import { calculateDiscountedConsumptionTaxJpy } from "../src/taxCalculator.ts";

test("renderCheckoutSummaryText formats positive and zero-clamped totals", () => {
  assert.equal(
    renderCheckoutSummaryText({ orderId: "o1", subtotalJpy: 3000, discountJpy: 500, settled: true }),
    "¥2,500",
  );
  assert.equal(
    renderCheckoutSummaryText({ orderId: "o2", subtotalJpy: 500, discountJpy: 900, settled: false }),
    "¥0",
  );
});

test("formatCurrencyJpy rejects non-finite numbers", () => {
  assert.throws(() => formatCurrencyJpy(Number.NaN), RangeError);
});

test("calculateDiscountedConsumptionTaxJpy computes tax and validates negative inputs", () => {
  assert.equal(calculateDiscountedConsumptionTaxJpy(1055, 0, 10), 105);
  assert.throws(() => calculateDiscountedConsumptionTaxJpy(-1, 0, 10), RangeError);
});
