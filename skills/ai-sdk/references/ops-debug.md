# Ops & Debug Reference

## Table of Contents
- [Error Handling (Core)](#error-handling-core)
- [Error Handling (UI)](#error-handling-ui)
- [Error Types](#error-types)
- [Testing](#testing)
- [Telemetry](#telemetry)
- [DevTools](#devtools)

---

## Error Handling (Core)

Use `try/catch` for sync errors, and handle streaming errors via `fullStream` or `onError`.

```ts
import { streamText } from 'ai';

try {
  const { fullStream } = streamText({
    model: 'openai/gpt-4o',
    prompt: 'Write a story about a robot.',
  });

  for await (const part of fullStream) {
    if (part.type === 'error') {
      console.error('Stream error', part.error);
    }
  }
} catch (error) {
  console.error('Call failed', error);
}
```

## Error Handling (UI)

```tsx
const { error, regenerate } = useChat();

return (
  <>
    {error && (
      <>
        <div>Something went wrong.</div>
        <button onClick={() => regenerate()}>Retry</button>
      </>
    )}
  </>
);
```

## Error Types

Common error classes include:

- `APICallError`
- `DownloadError`
- `EmptyResponseBodyError`
- `InvalidArgumentError`
- `InvalidToolInputError`
- `NoSuchToolError`
- `NoTranscriptGeneratedError`
- `NoSpeechGeneratedError`
- `ToolCallRepairError`

## Testing

Use mock models from `ai/test` to avoid provider calls.

```ts
import { generateText } from 'ai';
import { MockLanguageModelV3 } from 'ai/test';

const result = await generateText({
  model: new MockLanguageModelV3({
    doGenerate: async () => ({
      content: [{ type: 'text', text: 'Hello, world!' }],
      finishReason: { unified: 'stop', raw: undefined },
      usage: {
        inputTokens: { total: 10, noCache: 10, cacheRead: undefined, cacheWrite: undefined },
        outputTokens: { total: 20, text: 20, reasoning: undefined },
      },
      warnings: [],
    }),
  }),
  prompt: 'Hello, test!',
});
```

## Telemetry

Enable OpenTelemetry tracing per call.

```ts
const result = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Write a short story about a cat.',
  experimental_telemetry: { isEnabled: true },
});
```

## DevTools

Capture runs locally with the DevTools middleware.

```ts
import { wrapLanguageModel, gateway } from 'ai';
import { devToolsMiddleware } from '@ai-sdk/devtools';

const model = wrapLanguageModel({
  model: gateway('anthropic/claude-sonnet-4.5'),
  middleware: devToolsMiddleware(),
});
```
