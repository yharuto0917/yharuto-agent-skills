import {
  requireAdminRole,
  requireAuthenticatedSession,
  type RequestSession,
} from "./authGuard.ts";

export function listTenantUsersEndpoint(
  session: RequestSession | null,
  users: readonly string[],
): { count: number; users: readonly string[] } {
  requireAuthenticatedSession(session);
  requireAdminRole(session);
  return { count: users.length, users };
}
