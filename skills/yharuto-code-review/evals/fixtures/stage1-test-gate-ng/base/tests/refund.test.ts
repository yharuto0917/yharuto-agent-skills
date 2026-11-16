import test from "node:test";
import assert from "node:assert/strict";
import { refundService } from "../src/refund.ts";
import type { OrderRecord } from "../src/schema.ts";

test("processes basic refund request", async () => {
  const order: OrderRecord = {
    id: "ord_100",
    customerId: "cus_1",
    amountMinorJpy: 10000,
    refundedMinorJpy: 0,
    currencyCode: "JPY",
    status: "captured",
  };
  const res = await refundService.processOrderRefund(order, 2000, {
    async issueRefund() {
      return { refundTxId: "tx_base_1" };
    },
  });
  assert.equal(res.approvedAmountJpy, 2000);
});
