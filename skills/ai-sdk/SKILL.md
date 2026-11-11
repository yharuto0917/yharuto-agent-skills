---
name: ai-sdk
description: Comprehensive toolkit for building AI applications using AI SDK by Vercel. Use when Claude needs to (1) Build chatbots or conversational AI interfaces with React/Next.js/Vue/Svelte, (2) Generate text or stream responses from LLMs (OpenAI, Anthropic, Google, etc.), (3) Generate structured data using schemas with generateText/streamText and Output.object, (4) Implement tool calling or function calling in AI applications, (5) Create RAG (Retrieval Augmented Generation) applications, (6) Work with embeddings for semantic search, (7) Build multi-modal AI apps with images/PDFs, or (8) Integrate MCP (Model Context Protocol) servers.
---

# AI SDK by Vercel

## Overview

AI SDK is a TypeScript toolkit for building AI-powered applications with LLMs. It provides a unified API across multiple providers (OpenAI, Anthropic, Google, etc.) and includes React hooks for building chat interfaces.

## Quick Start

### Installation

```bash
pnpm add ai @ai-sdk/openai @ai-sdk/react
```

### Basic Text Generation

```ts
import { generateText } from 'ai';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Explain quantum computing in simple terms.',
});
```

### Streaming Text

```ts
import { streamText } from 'ai';

const result = streamText({
  model: 'openai/gpt-4o',
  prompt: 'Write a poem about the ocean.',
});

for await (const chunk of result.textStream) {
  process.stdout.write(chunk);
}
```

### Structured Output

```ts
import { generateText, Output } from 'ai';
import { z } from 'zod';

const { output } = await generateText({
  model: 'openai/gpt-4o',
  output: Output.object({
    schema: z.object({
      name: z.string(),
      ingredients: z.array(z.string()),
      steps: z.array(z.string()),
    }),
  }),
  prompt: 'Generate a pancake recipe.',
});
```

## Core Concepts

### Model Strings

Use provider-prefixed model strings:
- `openai/gpt-4o`, `openai/gpt-4o-mini`
- `anthropic/claude-sonnet-4-20250514`, `anthropic/claude-opus-4-20250514`
- `google/gemini-2.5-flash`, `google/gemini-2.5-pro`

### Main Functions

| Function | Purpose |
|----------|---------|
| `generateText` | Generate text (non-streaming) |
| `streamText` | Stream text responses |
| `Output.object()` | Generate structured objects |
| `Output.array()` | Generate arrays of objects |
| `embed` / `embedMany` | Create embeddings |
| `tool()` | Define callable tools |

### Tool Calling

```ts
import { generateText, tool, stepCountIs } from 'ai';
import { z } from 'zod';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'What is the weather in Tokyo?',
  tools: {
    getWeather: tool({
      description: 'Get weather for a location',
      inputSchema: z.object({
        location: z.string(),
      }),
      execute: async ({ location }) => ({
        temperature: 22,
        condition: 'Sunny',
      }),
    }),
  },
  stopWhen: stepCountIs(5), // Enable multi-step tool calls
});
```

### React Chat UI (Next.js)

**Route Handler:**
```ts
// app/api/chat/route.ts
import { streamText, convertToModelMessages, UIMessage } from 'ai';

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    messages: await convertToModelMessages(messages),
  });

  return result.toUIMessageStreamResponse();
}
```

**Client Component:**
```tsx
'use client';
import { useChat } from '@ai-sdk/react';
import { useState } from 'react';

export default function Chat() {
  const [input, setInput] = useState('');
  const { messages, sendMessage } = useChat();

  return (
    <div>
      {messages.map(m => (
        <div key={m.id}>
          {m.role}: {m.parts.map(p => p.type === 'text' ? p.text : '').join('')}
        </div>
      ))}
      <form onSubmit={e => {
        e.preventDefault();
        sendMessage({ text: input });
        setInput('');
      }}>
        <input value={input} onChange={e => setInput(e.target.value)} />
      </form>
    </div>
  );
}
```

## References

For detailed information on specific topics:

- **[Core Functions](references/core-functions.md)**: `generateText`, `streamText`, structured output, embeddings, reranking, image/audio helpers
- **[Agents](references/agents.md)**: `ToolLoopAgent`, loop control, workflow patterns, call options
- **[Agent Guides](references/agent-guides.md)**: RAG, multi-modal, Slackbot, Natural Language Postgres, Computer Use
- **[Model Guides](references/model-guides.md)**: Gemini, Claude, GPT-5, OpenAI Responses, o1/o3, DeepSeek, Llama
- **[Retrieval](references/retrieval.md)**: embeddings, reranking, RAG flow patterns
- **[Multimodal](references/multimodal.md)**: image generation, Gemini Flash Image, transcription, speech
- **[Server Integrations](references/server-integrations.md)**: Node.js HTTP, Express, Hono, Fastify, Nest.js
- **[Configuration](references/configuration.md)**: prompt engineering, settings, middleware, provider management
- **[Ops & Debug](references/ops-debug.md)**: error handling, telemetry, DevTools, testing
- **[UI Advanced](references/ui-advanced.md)**: resume streams, tool usage, metadata, transports, UIMessage streams
- **[Providers](references/providers.md)**: OpenAI, Anthropic, Google, DeepSeek, AI Gateway, and other provider configurations
- **[UI Hooks](references/ui-hooks.md)**: `useChat`, `useCompletion`, `useObject` for React/Next.js
- **[Tools & MCP](references/tools.md)**: tool definitions, multi-step calls, approvals, MCP integration
