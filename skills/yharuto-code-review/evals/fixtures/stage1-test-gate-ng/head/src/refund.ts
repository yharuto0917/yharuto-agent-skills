import type { OrderRecord, RefundAllocation } from "./schema.ts";

export const MAX_SINGLE_REFUND_JPY = 50_000;

export class RefundValidationError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "RefundValidationError";
    this.code = code;
  }
}

export interface PaymentGateway {
  issueRefund(orderId: string, amountJpy: number): Promise<{ refundTxId: string }>;
}

export function calculateRefundAllocation(
  order: OrderRecord,
  requestAmountJpy: number,
): RefundAllocation {
  if (order.currencyCode !== "JPY") {
    throw new RefundValidationError(
      "UNSUPPORTED_CURRENCY",
      `Unsupported currency: ${String(order.currencyCode)}`,
    );
  }
  if (order.status !== "captured") {
    throw new RefundValidationError(
      "ORDER_NOT_REFUNDABLE",
      `Order ${order.id} is in status ${order.status}`,
    );
  }
  if (!Number.isInteger(requestAmountJpy) || requestAmountJpy <= 0) {
    throw new RefundValidationError(
      "INVALID_REFUND_AMOUNT",
      "Refund amount must be a positive integer in JPY",
    );
  }
  if (requestAmountJpy > MAX_SINGLE_REFUND_JPY) {
    throw new RefundValidationError(
      "SINGLE_REFUND_LIMIT_EXCEEDED",
      `Single refund cannot exceed ${MAX_SINGLE_REFUND_JPY} JPY (requested ${requestAmountJpy})`,
    );
  }

  const refundableBalanceJpy = order.amountMinorJpy - order.refundedMinorJpy;
  if (requestAmountJpy > refundableBalanceJpy) {
    throw new RefundValidationError(
      "REFUND_BALANCE_EXCEEDED",
      `Requested ${requestAmountJpy} JPY exceeds refundable balance ${refundableBalanceJpy} JPY`,
    );
  }

  const remaining = refundableBalanceJpy - requestAmountJpy;
  return {
    orderId: order.id,
    approvedAmountJpy: requestAmountJpy,
    remainingCapturableJpy: remaining,
    nextStatus: remaining === 0 ? "refunded" : "captured",
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
