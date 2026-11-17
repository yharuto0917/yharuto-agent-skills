import fs from "node:fs/promises";
import type { CustomerAccountItem } from "./router.ts";

export class SnapshotReadError extends Error {
  constructor(message: string, options: { cause: unknown }) {
    super(message, options);
    this.name = "SnapshotReadError";
  }
}

export interface PartnerAccountExportResponse {
  generatedAtIso: string;
  /** Public API field contracted with external partner integrations (v1 schema). */
  customerAccountDataList: CustomerAccountItem[];
}

export async function loadPartnerExportPayload(
  snapshotPath: string,
  generatedAtIso: string,
): Promise<PartnerAccountExportResponse> {
  let rawText: string;
  try {
    rawText = await fs.readFile(snapshotPath, "utf8");
  } catch (err) {
    throw new SnapshotReadError(`Unable to read account snapshot file at ${snapshotPath}`, {
      cause: err,
    });
  }

  const parsed: unknown = JSON.parse(rawText);
  if (!Array.isArray(parsed)) {
    throw new TypeError("Snapshot JSON root must be an array");
  }

  const items: CustomerAccountItem[] = parsed
    .filter((entry): entry is Record<string, unknown> => entry != null && typeof entry === "object")
    .map((entry) => {
      if (
        typeof entry.accountId !== "string" ||
        typeof entry.partitionKey !== "string" ||
        typeof entry.active !== "boolean"
      ) {
        throw new TypeError("Invalid account record shape in snapshot");
      }
      return {
        accountId: entry.accountId,
        partitionKey: entry.partitionKey,
        active: entry.active,
      };
    });

  return {
    generatedAtIso,
    customerAccountDataList: items,
  };
}
