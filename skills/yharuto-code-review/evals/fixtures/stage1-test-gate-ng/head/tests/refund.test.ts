import test from "node:test";
import assert from "node:assert/strict";
import { refundService } from "../src/refund.ts";
import type { OrderRecord } from "../src/schema.ts";

test("processes a standard refund within limit", async (t) => {
  const mockOrder = {
    id: "ord_201",
    customerId: "cus_9",
    totalAmountUsdCents: 12000,
    refundedUsdCents: 0,
    currency: "USD",
    state: "paid",
  } as unknown as OrderRecord;

  t.mock.method(refundService, "calculateRefundAllocation", () => ({
    orderId: "ord_201",
    approvedAmountJpy: 3000,
    remainingCapturableJpy: 9000,
    nextStatus: "captured" as const,
  }));

  const result = await refundService.processOrderRefund(mockOrder, 3000, {
    async issueRefund() {
      return { refundTxId: "tx_mock_201" };
    },
  });

  assert.equal(result.approvedAmountJpy, 3000);
  assert.equal(result.remainingCapturableJpy, 9000);
  assert.equal(result.refundTxId, "tx_mock_201");
});
