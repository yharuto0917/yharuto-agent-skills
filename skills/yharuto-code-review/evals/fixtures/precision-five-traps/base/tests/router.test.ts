import test from "node:test";
import assert from "node:assert/strict";
import { parseVerifiedRouteParams } from "../src/router.ts";

test("parseVerifiedRouteParams accepts valid tenant and account IDs", () => {
  const res = parseVerifiedRouteParams("tnt_alpha1", "acc_0001");
  assert.deepEqual(res, { tenantId: "tnt_alpha1", accountId: "acc_0001" });
});

test("parseVerifiedRouteParams rejects malformed inputs", () => {
  assert.throws(() => parseVerifiedRouteParams("", "acc_0001"), TypeError);
  assert.throws(() => parseVerifiedRouteParams("tnt_alpha1", "bad"), TypeError);
});
