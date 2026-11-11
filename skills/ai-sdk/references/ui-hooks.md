# UI Hooks Reference

## Table of Contents

- [useChat](#usechat)
- [useCompletion](#usecompletion)
- [useObject](#useobject)
- [Server-Side Setup](#server-side-setup)
- [Message Persistence](#message-persistence)
- [Resume Streams](#resume-streams)
- [Tool Usage](#tool-usage)
- [Message Metadata](#message-metadata)
- [Direct Transport](#direct-transport)

---

## useChat

The primary hook for building chat interfaces.

### Installation

```bash
pnpm add @ai-sdk/react
```

### Basic Usage

```tsx
'use client';

import { useChat } from '@ai-sdk/react';
import { useState } from 'react';

export default function Chat() {
  const [input, setInput] = useState('');
  const { messages, sendMessage, status } = useChat();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      sendMessage({ text: input });
      setInput('');
    }
  };

  return (
    <div>
      {messages.map(m => (
        <div key={m.id}>
          <strong>{m.role}:</strong>
          {m.parts.map((part, i) => {
            if (part.type === 'text') return <span key={i}>{part.text}</span>;
            if (part.type === 'reasoning') {
              return (
                <details key={i}>
                  <summary>View reasoning</summary>
                  <pre>{part.text}</pre>
                </details>
              );
            }
            return null;
          })}
        </div>
      ))}

      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Type a message..."
        />
        <button type="submit" disabled={status === 'streaming'}>
          Send
        </button>
      </form>
    </div>
  );
}
```

### With Custom Transport

```tsx
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';

const { messages, sendMessage } = useChat({
  transport: new DefaultChatTransport({
    api: '/api/chat',
  }),
});
```

### Return Values

| Property | Type | Description |
| -------- | ---- | ----------- |
| `messages` | `UIMessage[]` | Array of chat messages |
| `sendMessage` | `function` | Send a new message |
| `status` | `string` | `'idle'`, `'streaming'`, `'error'` |
| `error` | `Error` | Error object if present |
| `setMessages` | `function` | Manually set messages |
| `reload` | `function` | Regenerate last response |
| `stop` | `function` | Stop current generation |

### Message Parts

Messages contain a `parts` array with different content types:

```ts
interface UIMessage {
  id: string;
  role: 'user' | 'assistant';
  parts: Array<
    | { type: 'text'; text: string }
    | { type: 'reasoning'; text: string }
    | { type: 'tool-call'; toolName: string; args: object }
    | { type: 'tool-result'; toolName: string; result: any }
    | { type: 'file'; url: string; mediaType: string }
  >;
}
```

### Rendering Tool Calls

```tsx
{messages.map(m => (
  <div key={m.id}>
    {m.parts.map((part, i) => {
      switch (part.type) {
        case 'text':
          return <p key={i}>{part.text}</p>;
        case 'tool-call':
          return (
            <div key={i} className="tool-call">
              Calling {part.toolName}...
              <pre>{JSON.stringify(part.args, null, 2)}</pre>
            </div>
          );
        case 'tool-result':
          return (
            <div key={i} className="tool-result">
              Result from {part.toolName}:
              <pre>{JSON.stringify(part.result, null, 2)}</pre>
            </div>
          );
        default:
          return null;
      }
    })}
  </div>
))}
```

### With File Uploads

```tsx
'use client';

import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { useRef, useState } from 'react';

async function fileToDataURL(file: File) {
  return new Promise<{ type: 'file'; mediaType: string; url: string }>(
    (resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () =>
        resolve({
          type: 'file',
          mediaType: file.type,
          url: reader.result as string,
        });
      reader.onerror = reject;
      reader.readAsDataURL(file);
    }
  );
}

export default function Chat() {
  const [input, setInput] = useState('');
  const [files, setFiles] = useState<FileList | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { messages, sendMessage } = useChat({
    transport: new DefaultChatTransport({ api: '/api/chat' }),
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const fileParts = files
      ? await Promise.all(Array.from(files).map(fileToDataURL))
      : [];

    sendMessage({
      role: 'user',
      parts: [{ type: 'text', text: input }, ...fileParts],
    });

    setInput('');
    setFiles(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        type="file"
        accept="image/*,application/pdf"
        ref={fileInputRef}
        onChange={e => setFiles(e.target.files)}
        multiple
      />
      <input value={input} onChange={e => setInput(e.target.value)} />
      <button type="submit">Send</button>
    </form>
  );
}
```

---

## useCompletion

Hook for simple text completions (non-chat).

```tsx
'use client';

import { useCompletion } from '@ai-sdk/react';

export default function Completion() {
  const { completion, input, handleInputChange, handleSubmit, isLoading } =
    useCompletion({ api: '/api/completion' });

  return (
    <div>
      <form onSubmit={handleSubmit}>
        <input value={input} onChange={handleInputChange} />
        <button type="submit" disabled={isLoading}>
          Complete
        </button>
      </form>
      <p>{completion}</p>
    </div>
  );
}
```

### Server Route

```ts
// app/api/completion/route.ts
import { streamText } from 'ai';

export async function POST(req: Request) {
  const { prompt } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    prompt,
  });

  return result.toDataStreamResponse();
}
```

---

## useObject

Hook for streaming structured objects.

```tsx
'use client';

import { useObject } from '@ai-sdk/react';
import { z } from 'zod';

const recipeSchema = z.object({
  name: z.string(),
  ingredients: z.array(z.string()),
  steps: z.array(z.string()),
});

export default function RecipeGenerator() {
  const { object, submit, isLoading } = useObject({
    api: '/api/recipe',
    schema: recipeSchema,
  });

  return (
    <div>
      <button onClick={() => submit('chocolate cake')} disabled={isLoading}>
        Generate Recipe
      </button>

      {object && (
        <div>
          <h2>{object.name}</h2>
          <h3>Ingredients:</h3>
          <ul>
            {object.ingredients?.map((ing, i) => (
              <li key={i}>{ing}</li>
            ))}
          </ul>
          <h3>Steps:</h3>
          <ol>
            {object.steps?.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
```

### Server Route

```ts
// app/api/recipe/route.ts
import { streamText, Output } from 'ai';
import { z } from 'zod';

export async function POST(req: Request) {
  const { prompt } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    output: Output.object({
      schema: z.object({
        name: z.string(),
        ingredients: z.array(z.string()),
        steps: z.array(z.string()),
      }),
    }),
    prompt: `Generate a recipe for: ${prompt}`,
  });

  return result.toDataStreamResponse();
}
```

---

## Server-Side Setup

### Next.js App Router

```ts
// app/api/chat/route.ts
import { streamText, convertToModelMessages, UIMessage } from 'ai';

export const maxDuration = 30;

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    system: 'You are a helpful assistant.',
    messages: await convertToModelMessages(messages),
  });

  return result.toUIMessageStreamResponse();
}
```

### With Tools

```ts
// app/api/chat/route.ts
import { streamText, convertToModelMessages, tool, stepCountIs, UIMessage } from 'ai';
import { z } from 'zod';

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    messages: await convertToModelMessages(messages),
    tools: {
      getWeather: tool({
        description: 'Get weather for a location',
        inputSchema: z.object({ location: z.string() }),
        execute: async ({ location }) => ({ temp: 72, condition: 'Sunny' }),
      }),
    },
    stopWhen: stepCountIs(5),
  });

  return result.toUIMessageStreamResponse();
}
```

---

## Message Persistence

### Saving Messages

```ts
// app/api/chat/route.ts
import { streamText, convertToModelMessages, UIMessage } from 'ai';
import { saveChat } from '@/lib/chat-store';

export async function POST(req: Request) {
  const { messages, chatId }: { messages: UIMessage[]; chatId: string } =
    await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    messages: await convertToModelMessages(messages),
  });

  return result.toUIMessageStreamResponse({
    originalMessages: messages,
    onFinish: ({ messages }) => {
      saveChat({ chatId, messages });
    },
  });
}
```

### Loading Messages

```tsx
// app/chat/[id]/page.tsx
import { loadChat } from '@/lib/chat-store';
import Chat from '@/components/chat';

export default async function Page({ params }: { params: { id: string } }) {
  const messages = await loadChat(params.id);
  return <Chat id={params.id} initialMessages={messages} />;
}
```

```tsx
// components/chat.tsx
'use client';

import { useChat, UIMessage } from '@ai-sdk/react';

export default function Chat({
  id,
  initialMessages,
}: {
  id: string;
  initialMessages: UIMessage[];
}) {
  const { messages, sendMessage } = useChat({
    id,
    messages: initialMessages,
  });

  // ... rest of component
}
```

### Handling Disconnects

Use `consumeStream` to ensure messages are saved even if client disconnects:

```ts
export async function POST(req: Request) {
  const { messages, chatId } = await req.json();

  const result = streamText({
    model: 'openai/gpt-4o',
    messages: await convertToModelMessages(messages),
  });

  // Consume stream to ensure completion even on disconnect
  result.consumeStream(); // no await

  return result.toUIMessageStreamResponse({
    originalMessages: messages,
    onFinish: ({ messages }) => {
      saveChat({ chatId, messages });
    },
  });
}
```

---

## Resume Streams

Resume streaming after client disconnects using `resume: true` and a GET resume endpoint.

```tsx
const { messages } = useChat({
  id: chatId,
  messages: initialMessages,
  resume: true,
});
```

## Tool Usage

Handle client-side tool calls using `onToolCall` and `addToolOutput`.

```tsx
const { addToolOutput } = useChat({
  onToolCall({ toolCall }) {
    if (toolCall.toolName === 'getLocation') {
      addToolOutput({ toolCallId: toolCall.toolCallId, output: 'Tokyo' });
    }
  },
});
```

## Message Metadata

Attach metadata to messages with `messageMetadata`.

```ts
return result.toUIMessageStreamResponse({
  messageMetadata: ({ part }) =>
    part.type === 'finish'
      ? { model: part.response.modelId, totalTokens: part.totalUsage.totalTokens }
      : undefined,
});
```

## Direct Transport

Use `DirectChatTransport` for in-process agent calls.

```ts
import { DirectChatTransport, ToolLoopAgent } from 'ai';

const agent = new ToolLoopAgent({
  model: 'openai/gpt-4o',
});

const transport = new DirectChatTransport({ agent });
```
