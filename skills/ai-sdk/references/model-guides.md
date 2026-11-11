# Model Guides Reference

## Table of Contents
- [Gemini 3](#gemini-3)
- [Claude 4](#claude-4)
- [OpenAI Responses API](#openai-responses-api)
- [Gemini Image Generation](#gemini-image-generation)
- [Claude 3.7 Sonnet](#claude-37-sonnet)
- [Llama 3.1](#llama-31)
- [GPT-5](#gpt-5)
- [OpenAI o1](#openai-o1)
- [OpenAI o3-mini](#openai-o3-mini)
- [DeepSeek R1](#deepseek-r1)
- [DeepSeek V3.2](#deepseek-v32)

---

## Gemini 3

```ts
import { google } from '@ai-sdk/google';
import { generateText } from 'ai';

const { text } = await generateText({
  model: google('gemini-3-pro-preview'),
  prompt: 'Explain the concept of Hilbert space.',
});
```

Thinking mode:

```ts
import { google, GoogleGenerativeAIProviderOptions } from '@ai-sdk/google';

await generateText({
  model: google('gemini-3-pro-preview'),
  prompt: 'What is the sum of the first 10 primes?',
  providerOptions: {
    google: {
      thinkingConfig: {
        includeThoughts: true,
        thinkingLevel: 'low',
      },
    } satisfies GoogleGenerativeAIProviderOptions,
  },
});
```

## Claude 4

```ts
import { anthropic } from '@ai-sdk/anthropic';
import { generateText } from 'ai';

const { text, reasoningText } = await generateText({
  model: anthropic('claude-sonnet-4-20250514'),
  prompt: 'How will quantum computing impact cryptography by 2050?',
});
```

Extended thinking:

```ts
import { AnthropicProviderOptions, anthropic } from '@ai-sdk/anthropic';

await generateText({
  model: anthropic('claude-sonnet-4-20250514'),
  prompt: 'Explain quantum entanglement.',
  providerOptions: {
    anthropic: {
      thinking: { type: 'enabled', budgetTokens: 15000 },
    } satisfies AnthropicProviderOptions,
  },
  headers: {
    'anthropic-beta': 'interleaved-thinking-2025-05-14',
  },
});
```

## OpenAI Responses API

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai.responses('gpt-4o'),
  prompt: 'Explain the concept of quantum entanglement.',
});
```

Web search:

```ts
const { text, sources } = await generateText({
  model: openai.responses('gpt-4o-mini'),
  prompt: 'What happened in San Francisco last week?',
  tools: {
    web_search_preview: openai.tools.webSearchPreview({
      searchContextSize: 'high',
      userLocation: { type: 'approximate', city: 'San Francisco', region: 'CA' },
    }),
  },
});
```

## Gemini Image Generation

Gemini 2.5 Flash Image uses `generateText` with `files` output.

```ts
import { generateText } from 'ai';

const result = await generateText({
  model: 'google/gemini-2.5-flash-image',
  prompt: 'Create a picture of a sunset over mountains.',
});

for (const file of result.files) {
  if (file.mediaType.startsWith('image/')) {
    // file.uint8Array
  }
}
```

## Claude 3.7 Sonnet

```ts
import { anthropic } from '@ai-sdk/anthropic';
import { generateText } from 'ai';

const { text, reasoningText } = await generateText({
  model: anthropic('claude-3-7-sonnet-20250219'),
  prompt: 'How many people will live in the world in 2040?',
});
```

## Llama 3.1

```ts
import { deepinfra } from '@ai-sdk/deepinfra';
import { generateText } from 'ai';

const { text } = await generateText({
  model: deepinfra('meta-llama/Meta-Llama-3.1-405B-Instruct'),
  prompt: 'What is love?',
});
```

## GPT-5

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai('gpt-5'),
  prompt: 'Explain quantum entanglement.',
});
```

Verbosity control:

```ts
await generateText({
  model: openai('gpt-5'),
  prompt: 'Explain quantum computing.',
  providerOptions: { openai: { textVerbosity: 'low' } },
});
```

## OpenAI o1

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai('o1'),
  prompt: 'Explain quantum entanglement.',
});
```

Reasoning effort:

```ts
await generateText({
  model: openai('o1'),
  prompt: 'Explain quantum entanglement briefly.',
  providerOptions: { openai: { reasoningEffort: 'low' } },
});
```

## OpenAI o3-mini

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai('o3-mini'),
  prompt: 'Explain quantum entanglement.',
});
```

## DeepSeek R1

```ts
import { deepseek } from '@ai-sdk/deepseek';
import { generateText } from 'ai';

const { text, reasoningText } = await generateText({
  model: deepseek('deepseek-reasoner'),
  prompt: 'Explain quantum entanglement.',
});
```

Middleware for third-party providers:

```ts
import { extractReasoningMiddleware, wrapLanguageModel } from 'ai';
import { fireworks } from '@ai-sdk/fireworks';

const model = wrapLanguageModel({
  model: fireworks('accounts/fireworks/models/deepseek-r1'),
  middleware: extractReasoningMiddleware({ tagName: 'think', startWithReasoning: true }),
});
```

## DeepSeek V3.2

```ts
import { deepseek } from '@ai-sdk/deepseek';
import { generateText } from 'ai';

const { text } = await generateText({
  model: deepseek('deepseek-chat'),
  prompt: 'Explain sparse attention.',
});
```
