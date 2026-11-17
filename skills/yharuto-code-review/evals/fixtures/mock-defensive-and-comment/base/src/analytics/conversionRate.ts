import type { OrderPricingSummary } from "../checkoutBadge.ts";

export interface CheckoutConversionMetrics {
  settledOrderCount: number;
  averageSettledAmountJpy: number;
  allocatedBonusPerOrderJpy: number;
}

export function computeCheckoutConversionMetrics(
  orders: readonly OrderPricingSummary[],
  bonusPoolJpy: number,
): CheckoutConversionMetrics {
  const settledOrders = orders.filter((order) => order.settled);
  const settledTotalJpy = settledOrders.reduce(
    (sum, order) => sum + Math.max(0, order.subtotalJpy - order.discountJpy),
    0,
  );

  const averageSettledAmountJpy = Math.round(settledTotalJpy / settledOrders.length);
  const allocatedBonusPerOrderJpy = Math.floor(bonusPoolJpy / settledOrders.length);

  return {
    settledOrderCount: settledOrders.length,
    averageSettledAmountJpy,
    allocatedBonusPerOrderJpy,
  };
}
