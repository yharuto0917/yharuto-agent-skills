# UI Advanced Reference

## Table of Contents
- [Chatbot Basics](#chatbot-basics)
- [Message Persistence](#message-persistence)
- [Resume Streams](#resume-streams)
- [Tool Usage](#tool-usage)
- [Streaming Data](#streaming-data)
- [Generative UI](#generative-ui)
- [Completion](#completion)
- [Object Generation](#object-generation)
- [Transport](#transport)
- [Message Metadata](#message-metadata)
- [Reading UIMessage Streams](#reading-uimessage-streams)

---

## Chatbot Basics

```tsx
import { useChat } from '@ai-sdk/react';

const { messages, sendMessage } = useChat();
```

## Message Persistence

```ts
import { streamText, convertToModelMessages } from 'ai';

return result.toUIMessageStreamResponse({
  originalMessages: messages,
  onFinish: ({ messages }) => saveChat({ chatId, messages }),
});
```

## Resume Streams

Enable resumable streams with `resume: true` and a GET resume endpoint.

```tsx
const { messages } = useChat({ id, messages: initialMessages, resume: true });
```

## Tool Usage

```tsx
const { messages, addToolOutput } = useChat({
  onToolCall({ toolCall }) {
    if (toolCall.toolName === 'getLocation') {
      addToolOutput({ toolCallId: toolCall.toolCallId, output: 'Tokyo' });
    }
  },
});
```

## Streaming Data

```ts
import { createUIMessageStream } from 'ai';

const stream = createUIMessageStream({
  execute: ({ writer }) => {
    writer.write({ type: 'data-custom', data: { status: 'loading' } });
  },
});
```

## Generative UI

Use tool calls to render custom UI components.

```tsx
import { useChat } from '@ai-sdk/react';

const { messages } = useChat();

messages.forEach(message => {
  message.parts.forEach(part => {
    if (part.type === 'tool-call') {
      // map to a UI component
    }
  });
});
```

## Completion

Use `useCompletion` for streaming text completions.

```tsx
import { useCompletion } from '@ai-sdk/react';

const { completion, input, handleInputChange, handleSubmit } = useCompletion({
  api: '/api/completion',
});
```

## Object Generation

Use `useObject` for streamed structured data.

```tsx
import { useObject } from '@ai-sdk/react';
import { z } from 'zod';

const schema = z.object({ title: z.string(), items: z.array(z.string()) });
const { object, submit } = useObject({ api: '/api/list', schema });
```

## Transport

Use `DirectChatTransport` to call an agent in-process.

```ts
import { DirectChatTransport, ToolLoopAgent } from 'ai';

const agent = new ToolLoopAgent({ model: 'openai/gpt-4o' });
const transport = new DirectChatTransport({ agent });
```

## Message Metadata

```ts
return result.toUIMessageStreamResponse({
  messageMetadata: ({ part }) =>
    part.type === 'finish'
      ? { model: part.response.modelId, totalTokens: part.totalUsage.totalTokens }
      : undefined,
});
```

## Reading UIMessage Streams

```ts
import { readUIMessageStream, streamText } from 'ai';

const result = streamText({ model: 'openai/gpt-4o', prompt: 'Hello' });

for await (const uiMessage of readUIMessageStream({
  stream: result.toUIMessageStream(),
})) {
  console.log(uiMessage);
}
```
