# Providers Reference

## Table of Contents

- [Vercel AI Gateway (Default)](#vercel-ai-gateway-default)
- [OpenAI](#openai)
- [Anthropic](#anthropic)
- [Google](#google)
- [DeepSeek](#deepseek)
- [Other Providers](#other-providers)
- [AI Gateway](#ai-gateway)
- [Vercel](#vercel)
- [xAI Grok](#xai-grok)
- [Groq](#groq)
- [Fal](#fal)
- [AssemblyAI](#assemblyai)
- [Custom Providers](#custom-providers)

---

## Vercel AI Gateway (Default)

The default way to use models with simple string identifiers.

```ts
import { generateText } from 'ai';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Hello!',
});
```

**Environment Variable:** `AI_GATEWAY_API_KEY`

### Supported Model Strings

```
openai/gpt-4o
openai/gpt-4o-mini
anthropic/claude-sonnet-4-20250514
anthropic/claude-opus-4-20250514
google/gemini-2.5-flash
google/gemini-2.5-pro
```

---

## OpenAI

### Installation

```bash
pnpm add @ai-sdk/openai
```

### Direct Provider Usage

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai('gpt-4o'),
  prompt: 'Hello!',
});
```

### Responses API

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai.responses('gpt-4o'),
  prompt: 'Hello!',
});
```

### Web Search Tool

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text, sources } = await generateText({
  model: openai.responses('gpt-4o'),
  prompt: 'What happened in tech news today?',
  tools: {
    web_search_preview: openai.tools.webSearchPreview(),
  },
});
```

### Environment Variable

`OPENAI_API_KEY`

### Available Models

- `gpt-4o` - Latest GPT-4 Omni
- `gpt-4o-mini` - Smaller, faster GPT-4 Omni
- `gpt-4-turbo` - GPT-4 Turbo
- `gpt-3.5-turbo` - GPT-3.5 Turbo
- `text-embedding-ada-002` - Embeddings
- `text-embedding-3-small` - Small embeddings
- `text-embedding-3-large` - Large embeddings

---

## Anthropic

### Installation

```bash
pnpm add @ai-sdk/anthropic
```

### Usage

```ts
import { anthropic } from '@ai-sdk/anthropic';
import { generateText } from 'ai';

const { text } = await generateText({
  model: anthropic('claude-sonnet-4-20250514'),
  prompt: 'Hello!',
});
```

### Extended Thinking

```ts
import { anthropic, AnthropicProviderOptions } from '@ai-sdk/anthropic';
import { streamText, convertToModelMessages, UIMessage } from 'ai';

const result = streamText({
  model: anthropic('claude-sonnet-4-20250514'),
  messages: await convertToModelMessages(messages),
  headers: {
    'anthropic-beta': 'interleaved-thinking-2025-05-14',
  },
  providerOptions: {
    anthropic: {
      thinking: { type: 'enabled', budgetTokens: 15000 },
    } satisfies AnthropicProviderOptions,
  },
});

return result.toUIMessageStreamResponse({ sendReasoning: true });
```

### Environment Variable

`ANTHROPIC_API_KEY`

### Available Models

- `claude-opus-4-20250514` - Most capable
- `claude-sonnet-4-20250514` - Balanced
- `claude-haiku-3-5` - Fast and efficient

---

## Google

### Installation

```bash
pnpm add @ai-sdk/google-generative-ai
```

### Usage

```ts
import { google } from '@ai-sdk/google-generative-ai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: google('gemini-2.5-flash'),
  prompt: 'Hello!',
});
```

### Google Search Tool

```ts
import { google } from '@ai-sdk/google-generative-ai';
import { generateText } from 'ai';

const { text, sources } = await generateText({
  model: google('gemini-2.5-flash'),
  prompt: 'What are the latest news?',
  tools: {
    google_search: google.tools.googleSearch({}),
  },
});
```

### Image Generation (Gemini 2.5 Flash Image)

```ts
import { generateText } from 'ai';

const result = await generateText({
  model: 'google/gemini-2.5-flash-image',
  prompt: 'Create a picture of a sunset over mountains',
});

for (const file of result.files) {
  if (file.mediaType.startsWith('image/')) {
    // Save or use file.uint8Array
  }
}
```

### Environment Variable

`GOOGLE_GENERATIVE_AI_API_KEY`

### Available Models

- `gemini-2.5-pro` - Most capable
- `gemini-2.5-flash` - Fast and efficient
- `gemini-2.5-flash-image` - Image generation

---

## DeepSeek

### Installation

```bash
pnpm add @ai-sdk/deepseek
```

### Usage

```ts
import { deepseek } from '@ai-sdk/deepseek';
import { generateText } from 'ai';

const { text } = await generateText({
  model: deepseek('deepseek-chat'),
  prompt: 'Hello!',
});
```

### Reasoning Model

```ts
import { deepseek } from '@ai-sdk/deepseek';
import { streamText, convertToModelMessages } from 'ai';

const result = streamText({
  model: deepseek('deepseek-reasoner'),
  messages: await convertToModelMessages(messages),
});

return result.toUIMessageStreamResponse({ sendReasoning: true });
```

### Environment Variable

`DEEPSEEK_API_KEY`

### Available Models

- `deepseek-chat` - Standard chat
- `deepseek-reasoner` - Enhanced reasoning

---

## Other Providers

### Mistral

```bash
pnpm add @ai-sdk/mistral
```

```ts
import { mistral } from '@ai-sdk/mistral';
const { text } = await generateText({
  model: mistral('mistral-large-latest'),
  prompt: 'Hello!',
});
```

### Cohere

```bash
pnpm add @ai-sdk/cohere
```

```ts
import { cohere } from '@ai-sdk/cohere';
const { text } = await generateText({
  model: cohere('command-r-plus'),
  prompt: 'Hello!',
});
```

### Amazon Bedrock

```bash
pnpm add @ai-sdk/amazon-bedrock
```

```ts
import { bedrock } from '@ai-sdk/amazon-bedrock';
const { text } = await generateText({
  model: bedrock('anthropic.claude-3-sonnet-20240229-v1:0'),
  prompt: 'Hello!',
});
```

### Azure OpenAI

```bash
pnpm add @ai-sdk/azure
```

```ts
import { azure } from '@ai-sdk/azure';
const { text } = await generateText({
  model: azure('my-deployment-name'),
  prompt: 'Hello!',
});
```

---

## AI Gateway

Use the Vercel AI Gateway to access models with a single API key.

```ts
import { generateText, gateway } from 'ai';

const { text } = await generateText({
  model: gateway('openai/gpt-5'),
  prompt: 'Hello world',
});
```

## Vercel

```ts
import { vercel } from '@ai-sdk/vercel';
import { generateText } from 'ai';

const { text } = await generateText({
  model: vercel('v0-1.5-md'),
  prompt: 'Create a Next.js AI chatbot.',
});
```

## xAI Grok

```ts
import { xai } from '@ai-sdk/xai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: xai('grok-3'),
  prompt: 'Write a vegetarian lasagna recipe for 4 people.',
});
```

## Groq

```ts
import { groq } from '@ai-sdk/groq';
import { generateText } from 'ai';

const { text } = await generateText({
  model: groq('gemma2-9b-it'),
  prompt: 'Hello!',
});
```

## Fal

```ts
import { fal } from '@ai-sdk/fal';
import { generateImage } from 'ai';

const { image } = await generateImage({
  model: fal.image('fal-ai/flux-lora'),
  prompt: 'A futuristic cityscape at sunset',
});
```

## AssemblyAI

```ts
import { assemblyai } from '@ai-sdk/assemblyai';
import { experimental_transcribe as transcribe } from 'ai';
import { readFile } from 'fs/promises';

const transcript = await transcribe({
  model: assemblyai.transcription('best'),
  audio: await readFile('audio.mp3'),
});
```

## Custom Providers

Create custom providers for model aliasing or default settings.

### Model Aliases

```ts
import { customProvider, gateway } from 'ai';

export const myProvider = customProvider({
  languageModels: {
    fast: gateway('openai/gpt-4o-mini'),
    smart: gateway('anthropic/claude-sonnet-4-20250514'),
    reasoning: gateway('openai/o1'),
  },
  fallbackProvider: gateway,
});

// Usage
const { text } = await generateText({
  model: myProvider.languageModel('smart'),
  prompt: 'Hello!',
});
```

### With Default Settings

```ts
import {
  customProvider,
  defaultSettingsMiddleware,
  wrapLanguageModel,
  gateway,
} from 'ai';

export const myProvider = customProvider({
  languageModels: {
    'gpt-4o-safe': wrapLanguageModel({
      model: gateway('openai/gpt-4o'),
      middleware: defaultSettingsMiddleware({
        settings: {
          temperature: 0.3,
          maxOutputTokens: 1000,
        },
      }),
    }),
  },
});
```

### Provider Registry

```ts
import { createProviderRegistry, gateway } from 'ai';
import { anthropic } from '@ai-sdk/anthropic';
import { openai } from '@ai-sdk/openai';

const registry = createProviderRegistry({
  anthropic,
  openai,
  gateway,
});

const model = registry.languageModel('anthropic:claude-sonnet-4-20250514');
```
