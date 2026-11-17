import type { MessageEventTarget, SharedMessageBus } from "./bus/messageBus.ts";

export interface PreviewSink {
  saveDraftRevision(pageId: string, htmlFragment: string): Promise<void>;
}

export async function applyPreviewPayload(
  payload: Record<string, unknown>,
  sink: PreviewSink,
): Promise<void> {
  if (typeof payload.pageId !== "string" || payload.pageId.trim() === "") {
    throw new TypeError("Preview pageId must be a non-empty string");
  }
  if (typeof payload.htmlFragment !== "string") {
    throw new TypeError("Preview htmlFragment must be a string");
  }
  await sink.saveDraftRevision(payload.pageId, payload.htmlFragment);
}

export function initPreviewEmbedBridge(
  win: MessageEventTarget,
  bus: SharedMessageBus,
  sink: PreviewSink,
): () => void {
  const unsubscribeBus = bus.subscribe("PREVIEW_UPDATE", async (payload) => {
    await applyPreviewPayload(payload, sink);
  });

  win.addEventListener("message", (event) => {
    const raw = event.data as { type?: string; payload?: Record<string, unknown> } | null;
    if (raw && raw.type === "PREVIEW_UPDATE" && raw.payload) {
      void applyPreviewPayload(raw.payload, sink);
    }
  });

  return unsubscribeBus;
}
