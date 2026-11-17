import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { resolveAccountPartitionKey } from "../src/router.ts";
import { fetchInvoicesSequentially } from "../src/billingSync.ts";
import { AccountStatusPanel } from "../src/AccountStatusPanel.ts";
import { summarizeUsageStream, type UsageEvent } from "../src/usageAggregator.ts";
import { loadPartnerExportPayload, SnapshotReadError } from "../src/exportPayload.ts";

test("resolveAccountPartitionKey validates boundary inputs and formats partition key", () => {
  assert.equal(resolveAccountPartitionKey("tnt_prod01", "acc_9876ab"), "acct:tnt_prod01:acc_9876ab");
  assert.throws(() => resolveAccountPartitionKey(null, "acc_9876ab"), TypeError);
  assert.throws(() => resolveAccountPartitionKey("tnt_prod01", "INVALID-ID"), TypeError);
  assert.throws(() => resolveAccountPartitionKey("tnt_x", "acc_9876ab"), TypeError);
});

test("fetchInvoicesSequentially enforces concurrency=1 and preserves order", async () => {
  let inFlight = 0;
  let maxObservedInFlight = 0;
  const observedRetryDelays: Array<number | undefined> = [];

  const summaries = await fetchInvoicesSequentially(["inv_1", "inv_2", "inv_3"], {
    async fetchInvoice(invoiceId: string, options?: { retryDelayMs: number }) {
      inFlight += 1;
      maxObservedInFlight = Math.max(maxObservedInFlight, inFlight);
      observedRetryDelays.push(options?.retryDelayMs);
      if (inFlight > 1) {
        throw new Error("429 Too Many Requests: concurrent calls forbidden");
      }
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return { invoiceId, totalJpy: 1000, settled: true };
    },
  });

  assert.equal(maxObservedInFlight, 1);
  assert.deepEqual(observedRetryDelays, [1250, 1250, 1250]);
  assert.deepEqual(
    summaries.map((s) => s.invoiceId),
    ["inv_1", "inv_2", "inv_3"],
  );
  assert.deepEqual(
    await fetchInvoicesSequentially([], {
      async fetchInvoice() {
        throw new Error("should not be called");
      },
    }),
    [],
  );
});

test("AccountStatusPanel calls hooks unconditionally before null early return and cleans up", () => {
  const hookOrderPerRender: string[][] = [];
  let unsubscribedCount = 0;

  const statusBus = {
    subscribe(_accountId: string, onChange: (online: boolean) => void) {
      onChange(true);
      return () => {
        unsubscribedCount += 1;
      };
    },
  };

  function renderWithTracker(summary: { displayName: string; planName: string } | null) {
    const calls: string[] = [];
    let stateVal = false;
    let cleanupFn: (() => void) | undefined;
    const node = AccountStatusPanel(
      { accountId: "acc_1001", accountSummary: summary, statusBus },
      {
        useState<T>(initial: T): [T, (next: T) => void] {
          calls.push("useState");
          stateVal = Boolean(initial);
          return [stateVal as unknown as T, (next: T) => { stateVal = Boolean(next); }];
        },
        useEffect(effect) {
          calls.push("useEffect");
          const res = effect();
          if (typeof res === "function") {
            cleanupFn = res;
          }
        },
      },
    );
    hookOrderPerRender.push(calls);
    cleanupFn?.();
    return node;
  }

  const emptyNode = renderWithTracker(null);
  const fullNode = renderWithTracker({ displayName: "Acme Corp", planName: "Enterprise" });

  assert.equal(emptyNode, null);
  assert.notEqual(fullNode, null);
  assert.equal(fullNode?.text, "Acme Corp (Enterprise)");
  assert.deepEqual(hookOrderPerRender[0], ["useState", "useEffect"]);
  assert.deepEqual(hookOrderPerRender[1], ["useState", "useEffect"]);
  assert.equal(unsubscribedCount, 2);
});

test("summarizeUsageStream consumes single-pass generator once and handles empty/zero boundaries", () => {
  function* oneShotGenerator(): IterableIterator<UsageEvent> {
    yield { eventId: "ev_1", units: 12, billable: true, flagged: false };
    yield { eventId: "ev_2", units: 0, billable: true, flagged: true };
    yield { eventId: "ev_3", units: 8, billable: false, flagged: true };
    yield { eventId: "ev_credit_adj", units: -4, billable: true, flagged: false };
    yield { eventId: "ev_4", units: 5, billable: true, flagged: true };
  }

  const result = summarizeUsageStream(oneShotGenerator());
  assert.deepEqual(result, {
    billableUnitsTotal: 17,
    flaggedEventIds: ["ev_2", "ev_3", "ev_4"],
  });

  function* emptyGenerator(): IterableIterator<UsageEvent> {}
  assert.deepEqual(summarizeUsageStream(emptyGenerator()), {
    billableUnitsTotal: 0,
    flaggedEventIds: [],
  });
});

test("loadPartnerExportPayload preserves customerAccountDataList key and wraps I/O cause", async () => {
  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "snapshot-test-"));
  try {
    const validPath = path.join(tmpDir, "accounts.json");
    await fs.writeFile(
      validPath,
      JSON.stringify([
        { accountId: "acc_1001", partitionKey: "acct:tnt_a:acc_1001", active: true },
      ]),
      "utf8",
    );

    const payload = await loadPartnerExportPayload(validPath, "2026-11-13T00:00:00Z");
    assert.equal(payload.generatedAtIso, "2026-11-13T00:00:00Z");
    assert.deepEqual(payload.customerAccountDataList, [
      { accountId: "acc_1001", partitionKey: "acct:tnt_a:acc_1001", active: true },
    ]);

    const missingPath = path.join(tmpDir, "missing.json");
    await assert.rejects(
      () => loadPartnerExportPayload(missingPath, "2026-11-13T00:00:00Z"),
      (err: unknown) => {
        assert.ok(err instanceof SnapshotReadError);
        assert.ok(err.cause instanceof Error);
        return true;
      },
    );

    const brokenJsonPath = path.join(tmpDir, "broken.json");
    await fs.writeFile(brokenJsonPath, "{not-valid-json", "utf8");
    await assert.rejects(
      () => loadPartnerExportPayload(brokenJsonPath, "2026-11-13T00:00:00Z"),
      SyntaxError,
    );
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true });
  }
});
