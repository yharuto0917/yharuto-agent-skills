export const LEGACY_MAINTENANCE_NOTICE =
  "Scheduled read-only maintenance window: Sunday 02:00-03:00 JST";

export function formatLegacyBanner(active: boolean): string {
  return active ? LEGACY_MAINTENANCE_NOTICE : "";
}
