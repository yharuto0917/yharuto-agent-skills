export interface RequestSession {
  userId: string;
  role: "admin" | "editor" | "viewer";
}

export class UnauthorizedError extends Error {
  readonly statusCode = 401;
  constructor(message = "Authentication required") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export class ForbiddenError extends Error {
  readonly statusCode = 403;
  constructor(message = "Administrator privileges required") {
    super(message);
    this.name = "ForbiddenError";
  }
}

export function requireAuthenticatedSession(
  session: RequestSession | null | undefined,
): asserts session is RequestSession {
  if (!session || !session.userId) {
    throw new UnauthorizedError();
  }
}

export function requireAdminRole(session: RequestSession): void {
  if (session.role !== "admin") {
    throw new ForbiddenError(`User ${session.userId} lacks admin role`);
  }
}
