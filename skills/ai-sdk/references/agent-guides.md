# Agent Guides Reference

## Table of Contents
- [RAG Agent](#rag-agent)
- [Multi-Modal Agent](#multi-modal-agent)
- [Slackbot Agent](#slackbot-agent)
- [Natural Language Postgres](#natural-language-postgres)
- [Computer Use](#computer-use)

---

## RAG Agent

Build a retrieval-augmented agent with embeddings, chunking, and tool calls.

```ts
import { embedMany, tool, streamText, stepCountIs } from 'ai';
import { z } from 'zod';

const result = streamText({
  model: 'openai/gpt-4o',
  tools: {
    addResource: tool({
      description: 'Add a resource to the knowledge base',
      inputSchema: z.object({ content: z.string() }),
      execute: async ({ content }) => ({ ok: true, content }),
    }),
  },
  stopWhen: stepCountIs(5),
  messages: [{ role: 'user', content: 'Remember that I like noodles.' }],
});
```

Key steps:

- Chunk source material and store embeddings
- Add tools for write + retrieval
- Use `stopWhen` for multi-step tool loops
- Stream the response via `toUIMessageStreamResponse`

## Multi-Modal Agent

Support images and PDFs in chat interfaces using file parts.

```tsx
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';

const { messages, sendMessage } = useChat({
  transport: new DefaultChatTransport({ api: '/api/chat' }),
});
```

Server handler:

```ts
import { streamText, convertToModelMessages } from 'ai';

export async function POST(req: Request) {
  const { messages } = await req.json();
  const result = streamText({
    model: 'openai/gpt-4o',
    messages: await convertToModelMessages(messages),
  });
  return result.toUIMessageStreamResponse();
}
```

## Slackbot Agent

Use Slack events to drive AI responses with the SDK.

```ts
import { generateText } from 'ai';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Summarize the latest thread updates.',
});
```

Key steps:

- Validate Slack events and respond within 3s
- Use `waitUntil` to complete AI work asynchronously
- Format and send responses back to Slack

## Natural Language Postgres

Convert natural language into SQL and visualize results.

```ts
import { generateText } from 'ai';

const { text: sql } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Show the top 10 companies by valuation.',
});
```

Key steps:

- Use a schema-aware prompt to generate SQL
- Run SQL, then generate chart configs from results
- Explain query intent to users

## Computer Use

Claude Computer Use enables tool-driven UI automation.

```ts
import { anthropic } from '@ai-sdk/anthropic';
import { generateText } from 'ai';

const computerTool = anthropic.tools.computer_20250124({
  displayWidthPx: 1920,
  displayHeightPx: 1080,
  execute: async ({ action, coordinate, text }) => {
    return { type: 'text', data: `Ran ${action}` };
  },
  toModelOutput({ output }) {
    return typeof output === 'string'
      ? [{ type: 'text', text: output }]
      : [{ type: 'image', data: output.data, mediaType: 'image/png' }];
  },
});

await generateText({
  model: 'anthropic/claude-sonnet-4-20250514',
  prompt: 'Open the browser and take a screenshot.',
  tools: { computer: computerTool },
});
```

Notes:

- Implement execute logic in a controlled environment
- Use `stopWhen` for multi-step automation
- Add safety constraints and human approval
