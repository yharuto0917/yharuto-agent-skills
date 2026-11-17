import type { OrderRecord, RefundAllocation } from "./schema.ts";

export interface PaymentGateway {
  issueRefund(orderId: string, amountJpy: number): Promise<{ refundTxId: string }>;
}

export function calculateRefundAllocation(
  order: OrderRecord,
  requestAmountJpy: number,
): RefundAllocation {
  const remaining = order.amountMinorJpy - order.refundedMinorJpy - requestAmountJpy;
  return {
    orderId: order.id,
    approvedAmountJpy: requestAmountJpy,
    remainingCapturableJpy: remaining,
    nextStatus: remaining <= 0 ? "refunded" : "captured",
  };
}

export const refundService = {
  calculateRefundAllocation,
  async processOrderRefund(
    order: OrderRecord,
    requestAmountJpy: number,
    gateway: PaymentGateway,
  ): Promise<RefundAllocation & { refundTxId: string }> {
    const allocation = refundService.calculateRefundAllocation(order, requestAmountJpy);
    const receipt = await gateway.issueRefund(order.id, allocation.approvedAmountJpy);
    return {
      ...allocation,
      refundTxId: receipt.refundTxId,
    };
  },
};
