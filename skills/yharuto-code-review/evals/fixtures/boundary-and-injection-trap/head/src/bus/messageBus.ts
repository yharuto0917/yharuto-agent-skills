export const ALLOWED_EMBED_ORIGINS = new Set([
  "https://studio.cms.example.com",
  "https://preview.cms.example.com",
]);

export interface IncomingWindowMessage {
  origin: string;
  data: unknown;
}

export interface MessageEventTarget {
  addEventListener(type: "message", handler: (event: IncomingWindowMessage) => void): void;
}

export type MessageSubscriber = (payload: Record<string, unknown>) => void | Promise<void>;

export interface SharedMessageBus {
  subscribe(topic: string, handler: MessageSubscriber): () => void;
}

export function createSharedMessageBus(win: MessageEventTarget): SharedMessageBus {
  const listenersByTopic = new Map<string, Set<MessageSubscriber>>();

  win.addEventListener("message", (event) => {
    if (!ALLOWED_EMBED_ORIGINS.has(event.origin)) {
      return;
    }
    if (!event.data || typeof event.data !== "object") {
      return;
    }
    const envelope = event.data as Record<string, unknown>;
    if (typeof envelope.type !== "string" || !envelope.payload || typeof envelope.payload !== "object") {
      return;
    }
    const handlers = listenersByTopic.get(envelope.type);
    if (!handlers) {
      return;
    }
    for (const handler of handlers) {
      void handler(envelope.payload as Record<string, unknown>);
    }
  });

  return {
    subscribe(topic: string, handler: MessageSubscriber) {
      const bucket = listenersByTopic.get(topic) ?? new Set<MessageSubscriber>();
      bucket.add(handler);
      listenersByTopic.set(topic, bucket);
      return () => {
        bucket.delete(handler);
      };
    },
  };
}
