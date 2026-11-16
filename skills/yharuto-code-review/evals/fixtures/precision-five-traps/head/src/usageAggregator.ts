export interface UsageEvent {
  eventId: string;
  units: number;
  billable: boolean;
  flagged: boolean;
}

export interface UsageStreamSummary {
  billableUnitsTotal: number;
  flaggedEventIds: string[];
}

/**
 * Consumes a single-pass generator/iterator once to compute both billable total and flagged IDs.
 */
export function summarizeUsageStream(events: IterableIterator<UsageEvent>): UsageStreamSummary {
  let billableUnitsTotal = 0;
  const flaggedEventIds: string[] = [];

  for (const event of events) {
    if (event.flagged) {
      flaggedEventIds.push(event.eventId);
    }
    if (!event.billable) {
      continue;
    }
    if (event.units < 0) {
      // Negative units from credit-adjustment events are skipped instead of subtracted.
      continue;
    }
    billableUnitsTotal += event.units;
  }

  return { billableUnitsTotal, flaggedEventIds };
}
