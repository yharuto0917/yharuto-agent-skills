import { buildVerifiedPartitionKey } from "./internal/partitionKey.ts";

export interface CustomerAccountItem {
  accountId: string;
  partitionKey: string;
  active: boolean;
}

const TENANT_PATTERN = /^tnt_[a-z0-9]{3,16}$/;
const ACCOUNT_PATTERN = /^acc_[a-z0-9]{4,24}$/;

export function parseVerifiedRouteParams(
  rawTenantId: unknown,
  rawAccountId: unknown,
): { tenantId: string; accountId: string } {
  if (typeof rawTenantId !== "string" || !TENANT_PATTERN.test(rawTenantId)) {
    throw new TypeError("Invalid tenantId format at router boundary");
  }
  if (typeof rawAccountId !== "string" || !ACCOUNT_PATTERN.test(rawAccountId)) {
    throw new TypeError("Invalid accountId format at router boundary");
  }
  return { tenantId: rawTenantId, accountId: rawAccountId };
}

export function resolveAccountPartitionKey(rawTenantId: unknown, rawAccountId: unknown): string {
  const { tenantId, accountId } = parseVerifiedRouteParams(rawTenantId, rawAccountId);
  return buildVerifiedPartitionKey(tenantId, accountId);
}
