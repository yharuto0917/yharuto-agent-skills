import type { SharedMessageBus } from "./bus/messageBus.ts";

export interface PreviewSink {
  saveDraftRevision(pageId: string, htmlFragment: string): Promise<void>;
}

export async function applyPreviewPayload(
  payload: Record<string, unknown>,
  sink: PreviewSink,
): Promise<void> {
  if (typeof payload.pageId !== "string" || typeof payload.htmlFragment !== "string") {
    throw new TypeError("Invalid preview payload");
  }
  await sink.saveDraftRevision(payload.pageId, payload.htmlFragment);
}

export function registerPreviewBusSubscription(
  bus: SharedMessageBus,
  sink: PreviewSink,
): () => void {
  return bus.subscribe("PREVIEW_UPDATE", async (payload) => {
    await applyPreviewPayload(payload, sink);
  });
}
