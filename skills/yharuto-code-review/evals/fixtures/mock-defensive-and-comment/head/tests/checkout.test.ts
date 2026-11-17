import test from "node:test";
import assert from "node:assert/strict";
import { syncCheckoutDiscountBadge } from "../src/checkoutBadge.ts";
import { formatCurrencyJpy } from "../src/shared/currencyFormatter.ts";
import { calculateDiscountedConsumptionTaxJpy } from "../src/taxCalculator.ts";
import { createBareStubElement } from "./helpers/stubElement.ts";

test("syncCheckoutDiscountBadge updates discount state for discounted, non-discounted, and clamped boundaries", () => {
  const container = createBareStubElement();
  const badge = createBareStubElement();

  const discounted = syncCheckoutDiscountBadge(container, badge, {
    orderId: "ord_501",
    subtotalJpy: 4800,
    discountJpy: 800,
    settled: false,
    campaignCode: "SPRING_FLASH_2026",
  });
  assert.equal(discounted.isDiscountActive, true);
  assert.equal(discounted.formattedPayable, "¥4,000 [春のタイムセール特別価格・ポイント5倍対象]");
  assert.equal(badge.dataset.discountActive, "true");

  const regular = syncCheckoutDiscountBadge(container, badge, {
    orderId: "ord_502",
    subtotalJpy: 4800,
    discountJpy: 0,
    settled: true,
  });
  assert.equal(regular.isDiscountActive, false);
  assert.equal(regular.formattedPayable, "¥4,800");
  assert.equal(badge.dataset.discountActive, "false");

  const clamped = syncCheckoutDiscountBadge(container, badge, {
    orderId: "ord_503",
    subtotalJpy: 500,
    discountJpy: 1200,
    settled: false,
  });
  assert.equal(clamped.formattedPayable, "¥0");
});

test("formatCurrencyJpy formats regular amounts, campaign labels, and rejects invalid inputs", () => {
  assert.equal(formatCurrencyJpy(12500), "¥12,500");
  assert.equal(formatCurrencyJpy(0), "¥0");
  assert.equal(
    formatCurrencyJpy(3000, "VIP_GOLD_WEEKEND"),
    "¥3,000 [ゴールド会員週末限定優待]",
  );
  assert.throws(() => formatCurrencyJpy(Number.NaN), RangeError);
  assert.throws(() => formatCurrencyJpy(Number.POSITIVE_INFINITY), RangeError);
});

test("calculateDiscountedConsumptionTaxJpy handles rounding boundaries and negative errors", () => {
  assert.equal(calculateDiscountedConsumptionTaxJpy(1055, 0, 10), 106);
  assert.equal(calculateDiscountedConsumptionTaxJpy(1054, 0, 10), 105);
  assert.equal(calculateDiscountedConsumptionTaxJpy(500, 800, 10), 0);
  assert.throws(() => calculateDiscountedConsumptionTaxJpy(-10, 0, 10), RangeError);
  assert.throws(() => calculateDiscountedConsumptionTaxJpy(1000, -5, 10), RangeError);
});
