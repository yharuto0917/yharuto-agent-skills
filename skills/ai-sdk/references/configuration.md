# Configuration Reference

## Table of Contents
- [Prompt Engineering](#prompt-engineering)
- [Settings](#settings)
- [Language Model Middleware](#language-model-middleware)
- [Provider & Model Management](#provider--model-management)

---

## Prompt Engineering

Guidelines for tool-heavy prompts:

- Favor strong tool-calling models (e.g., `gpt-5`, `gpt-4.1`).
- Keep tool count small (5 or fewer).
- Use `.describe()` for schema fields.
- Prefer `nullable()` over `optional()` for strict schemas.
- Set `temperature: 0` for deterministic tool calls.

```ts
const result = await generateText({
  model: 'openai/gpt-5',
  temperature: 0,
  tools: {
    search: tool({
      description: 'Search the knowledge base',
      inputSchema: z.object({ query: z.string().describe('Search query') }),
    }),
  },
  prompt: 'Find the latest onboarding docs.',
});
```

## Settings

Common settings shared by `generateText` and `streamText`:

```ts
const result = await generateText({
  model: 'openai/gpt-4o',
  maxOutputTokens: 512,
  temperature: 0.3,
  maxRetries: 5,
  prompt: 'Invent a new holiday and describe its traditions.',
});
```

## Language Model Middleware

Enhance models with `wrapLanguageModel`.

```ts
import { wrapLanguageModel, extractJsonMiddleware } from 'ai';

const model = wrapLanguageModel({
  model: 'openai/gpt-4o',
  middleware: extractJsonMiddleware(),
});
```

Built-in middleware:

- `extractReasoningMiddleware`
- `extractJsonMiddleware`
- `simulateStreamingMiddleware`
- `defaultSettingsMiddleware`
- `addToolInputExamplesMiddleware`

## Provider & Model Management

Use `customProvider` or `createProviderRegistry` to manage models.

```ts
import { createProviderRegistry, gateway } from 'ai';
import { anthropic } from '@ai-sdk/anthropic';
import { openai } from '@ai-sdk/openai';

const registry = createProviderRegistry({
  gateway,
  anthropic,
  openai,
});

const { text } = await generateText({
  model: registry.languageModel('openai:gpt-5'),
  prompt: 'Invent a new holiday and describe its traditions.',
});
```
