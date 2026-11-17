/**
 * Internal helper invoked exclusively from `resolveAccountPartitionKey` after boundary validation.
 */
export function buildVerifiedPartitionKey(
  verifiedTenantId: string,
  verifiedAccountId: string,
): string {
  return `acct:${verifiedTenantId}:${verifiedAccountId.toLowerCase()}`;
}
