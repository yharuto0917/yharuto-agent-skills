# Core Functions Reference

## Table of Contents
- [generateText](#generatetext)
- [streamText](#streamtext)
- [Structured Output (Output.object / Output.array)](#structured-output)
- [Structured Objects (generateObject / streamObject)](#structured-objects)
- [Embeddings](#embeddings)
- [Reranking](#reranking)
- [Image Generation](#image-generation)
- [Transcription](#transcription)
- [Speech](#speech)
- [Common Options](#common-options)

---

## generateText

Non-streaming text generation.

```ts
import { generateText } from 'ai';

const { text, usage, finishReason } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'What is the capital of France?',
});
```

### With System Prompt and Messages

```ts
const { text } = await generateText({
  model: 'anthropic/claude-sonnet-4-20250514',
  system: 'You are a helpful assistant.',
  messages: [
    { role: 'user', content: 'Hello!' },
    { role: 'assistant', content: 'Hi! How can I help?' },
    { role: 'user', content: 'What is 2+2?' },
  ],
});
```

### Return Value Properties

| Property | Type | Description |
|----------|------|-------------|
| `text` | `string` | Generated text |
| `usage` | `object` | Token usage (`promptTokens`, `completionTokens`, `totalTokens`) |
| `finishReason` | `string` | Why generation stopped (`stop`, `length`, `tool-calls`) |
| `response` | `object` | Response metadata (headers, body) |
| `sources` | `array` | Sources from web search (if applicable) |

---

## streamText

Streaming text generation for real-time responses.

```ts
import { streamText } from 'ai';

const result = streamText({
  model: 'openai/gpt-4o',
  prompt: 'Write a story about a robot.',
});

// Option 1: Iterate over text stream
for await (const chunk of result.textStream) {
  process.stdout.write(chunk);
}

// Option 2: Get full text after streaming
const fullText = await result.text;
```

### Using fullStream for Detailed Parts

```ts
const result = streamText({
  model: 'openai/gpt-4o',
  prompt: 'Hello',
});

for await (const part of result.fullStream) {
  switch (part.type) {
    case 'text':
      console.log('Text:', part.text);
      break;
    case 'tool-call':
      console.log('Tool call:', part.toolName, part.args);
      break;
    case 'tool-result':
      console.log('Tool result:', part.result);
      break;
    case 'finish':
      console.log('Finished:', part.finishReason);
      break;
    case 'error':
      console.error('Error:', part.error);
      break;
  }
}
```

### Server Response Methods

```ts
// For Next.js App Router
return result.toUIMessageStreamResponse();

// For Node.js HTTP server
result.pipeUIMessageStreamToResponse(res);

// With reasoning tokens (for models like Claude)
return result.toUIMessageStreamResponse({ sendReasoning: true });
```

### Stream Callbacks

```ts
const result = streamText({
  model: 'openai/gpt-4o',
  prompt: 'Hello',
  onChunk: ({ chunk }) => {
    console.log('Received chunk:', chunk);
  },
  onFinish: ({ text, usage, finishReason }) => {
    console.log('Finished:', { text, usage, finishReason });
  },
  onError: ({ error }) => {
    console.error('Stream error:', error);
  },
});
```

---

## Structured Output

Generate type-safe structured data using schemas.

### Output.object()

```ts
import { generateText, Output } from 'ai';
import { z } from 'zod';

const { output } = await generateText({
  model: 'openai/gpt-4o',
  output: Output.object({
    schema: z.object({
      name: z.string(),
      age: z.number().nullable(),
      skills: z.array(z.string()),
    }),
  }),
  prompt: 'Generate a developer profile.',
});

// output is typed: { name: string; age: number | null; skills: string[] }
```

### Output.array()

```ts
const { output } = await generateText({
  model: 'openai/gpt-4o',
  output: Output.array({
    element: z.object({
      city: z.string(),
      country: z.string(),
      population: z.number(),
    }),
  }),
  prompt: 'List the 5 largest cities in the world.',
});

// output is typed: Array<{ city: string; country: string; population: number }>
```

### Streaming Structured Output

```ts
import { streamText, Output } from 'ai';

// Stream partial objects
const { partialOutputStream } = streamText({
  model: 'openai/gpt-4o',
  output: Output.object({
    schema: z.object({
      title: z.string(),
      chapters: z.array(z.object({
        title: z.string(),
        summary: z.string(),
      })),
    }),
  }),
  prompt: 'Create a book outline.',
});

for await (const partial of partialOutputStream) {
  console.log(partial); // Partial object as it's being generated
}
```

### Streaming Array Elements

```ts
const { elementStream } = streamText({
  model: 'openai/gpt-4o',
  output: Output.array({
    element: z.object({
      name: z.string(),
      description: z.string(),
    }),
  }),
  prompt: 'Generate 5 fantasy characters.',
});

for await (const element of elementStream) {
  console.log(element); // Each complete element as it's generated
}
```

---

## Structured Objects

Use `generateObject` or `streamObject` for schema-bound outputs.

```ts
import { generateObject } from 'ai';
import { z } from 'zod';

const { object } = await generateObject({
  model: 'openai/gpt-4o',
  schema: z.object({
    recipe: z.object({
      name: z.string(),
      ingredients: z.array(z.string()),
      steps: z.array(z.string()),
    }),
  }),
  prompt: 'Generate a lasagna recipe.',
});
```

---

## Embeddings

Create vector embeddings for semantic search and RAG.

### Single Embedding

```ts
import { embed } from 'ai';

const { embedding } = await embed({
  model: 'openai/text-embedding-ada-002',
  value: 'The quick brown fox jumps over the lazy dog.',
});

// embedding is number[]
```

### Multiple Embeddings

```ts
import { embedMany } from 'ai';

const { embeddings } = await embedMany({
  model: 'openai/text-embedding-ada-002',
  values: [
    'First document text',
    'Second document text',
    'Third document text',
  ],
});

// embeddings is number[][]
```

### Cosine Similarity

```ts
import { cosineSimilarity } from 'ai';

const similarity = cosineSimilarity(embedding1, embedding2);
// Returns value between -1 and 1 (1 = identical)
```

---

## Reranking

Re-rank retrieved documents to improve relevance.

```ts
import { rerank } from 'ai';
import { cohere } from '@ai-sdk/cohere';

const { ranking } = await rerank({
  model: cohere.reranking('rerank-v3.5'),
  documents: ['doc one', 'doc two'],
  query: 'find the relevant one',
  topN: 1,
});
```

---

## Image Generation

Generate images with `generateImage`.

```ts
import { generateImage } from 'ai';
import { openai } from '@ai-sdk/openai';

const { image } = await generateImage({
  model: openai.image('dall-e-3'),
  prompt: 'Santa Claus driving a Cadillac',
});
```

---

## Transcription

Transcribe audio with `experimental_transcribe`.

```ts
import { experimental_transcribe as transcribe } from 'ai';
import { openai } from '@ai-sdk/openai';
import { readFile } from 'fs/promises';

const transcript = await transcribe({
  model: openai.transcription('whisper-1'),
  audio: await readFile('audio.mp3'),
});
```

---

## Speech

Generate speech from text with `experimental_generateSpeech`.

```ts
import { experimental_generateSpeech as generateSpeech } from 'ai';
import { openai } from '@ai-sdk/openai';

const audio = await generateSpeech({
  model: openai.speech('tts-1'),
  text: 'Hello, world!',
  voice: 'alloy',
});
```

---

## Common Options

Options available for both `generateText` and `streamText`.

### Basic Options

| Option | Type | Description |
|--------|------|-------------|
| `model` | `string` | Model identifier (e.g., `'openai/gpt-4o'`) |
| `prompt` | `string` | Simple text prompt |
| `messages` | `array` | Conversation messages |
| `system` | `string` | System instructions |
| `maxTokens` | `number` | Maximum tokens to generate |
| `temperature` | `number` | Randomness (0-2, default varies by model) |
| `topP` | `number` | Nucleus sampling parameter |
| `topK` | `number` | Top-k sampling parameter |
| `seed` | `number` | Random seed for reproducibility |
| `stopSequences` | `string[]` | Sequences that stop generation |

### Advanced Options

| Option | Type | Description |
|--------|------|-------------|
| `tools` | `object` | Tool definitions |
| `stopWhen` | `function` | Condition to stop multi-step execution |
| `output` | `Output.*` | Structured output specification |
| `providerOptions` | `object` | Provider-specific options |
| `headers` | `object` | Custom HTTP headers |
| `abortSignal` | `AbortSignal` | Signal to cancel the request |

### Provider-Specific Options Example

```ts
const result = await generateText({
  model: 'anthropic/claude-sonnet-4-20250514',
  prompt: 'Complex reasoning task',
  headers: {
    'anthropic-beta': 'interleaved-thinking-2025-05-14',
  },
  providerOptions: {
    anthropic: {
      thinking: { type: 'enabled', budgetTokens: 15000 },
    },
  },
});
```
