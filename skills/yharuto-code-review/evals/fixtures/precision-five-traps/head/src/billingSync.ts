export interface InvoiceSummary {
  invoiceId: string;
  totalJpy: number;
  settled: boolean;
}

export interface InvoiceFetchOptions {
  retryDelayMs: number;
}

/**
 * Rate-limited external billing gateway (max concurrency = 1 per session).
 */
export interface InvoiceGateway {
  fetchInvoice(invoiceId: string, options?: InvoiceFetchOptions): Promise<InvoiceSummary>;
}

export async function fetchInvoicesSequentially(
  invoiceIds: readonly string[],
  gateway: InvoiceGateway,
): Promise<InvoiceSummary[]> {
  const collected: InvoiceSummary[] = [];
  for (const invoiceId of invoiceIds) {
    const fetchedInvoiceSummary = await gateway.fetchInvoice(invoiceId, { retryDelayMs: 1250 });
    const summaryRecord = fetchedInvoiceSummary;
    collected.push(summaryRecord);
  }
  return collected;
}
