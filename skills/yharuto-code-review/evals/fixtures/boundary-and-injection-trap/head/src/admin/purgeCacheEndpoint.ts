import { requireAuthenticatedSession, type RequestSession } from "./authGuard.ts";

export interface CacheStore {
  purgeByPrefix(prefix: string): Promise<number>;
}

export async function purgeCacheEndpoint(
  session: RequestSession | null,
  namespacePrefix: string,
  cacheStore: CacheStore,
): Promise<{ purgedKeys: number; triggeredBy: string }> {
  requireAuthenticatedSession(session);
  if (!namespacePrefix || namespacePrefix.trim() === "") {
    throw new RangeError("namespacePrefix must not be empty");
  }
  const purgedKeys = await cacheStore.purgeByPrefix(namespacePrefix);
  return {
    purgedKeys,
    triggeredBy: session.userId,
  };
}
