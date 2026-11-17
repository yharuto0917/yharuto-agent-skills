import {
  requireAdminRole,
  requireAuthenticatedSession,
  type RequestSession,
} from "./authGuard.ts";

export function exportAuditEventsEndpoint(
  session: RequestSession | null,
  limit: number,
): { exportedLimit: number } {
  requireAuthenticatedSession(session);
  requireAdminRole(session);
  if (limit <= 0 || limit > 1000) {
    throw new RangeError("limit must be between 1 and 1000");
  }
  return { exportedLimit: limit };
}
