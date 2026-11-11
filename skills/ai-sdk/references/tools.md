# Tools & MCP Reference

## Table of Contents

- [Defining Tools](#defining-tools)
- [Multi-Step Tool Calls](#multi-step-tool-calls)
- [Tool Best Practices](#tool-best-practices)
- [Tool Approval](#tool-approval)
- [Tool Repair](#tool-repair)
- [MCP (Model Context Protocol)](#mcp-model-context-protocol)
- [Built-in Provider Tools](#built-in-provider-tools)

---

## Defining Tools

Tools enable LLMs to perform actions and retrieve information.

### Basic Tool Definition

```ts
import { generateText, tool } from 'ai';
import { z } from 'zod';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'What is the weather in Tokyo?',
  tools: {
    getWeather: tool({
      description: 'Get the current weather for a location',
      inputSchema: z.object({
        location: z.string().describe('City name'),
        unit: z.enum(['celsius', 'fahrenheit']).optional(),
      }),
      execute: async ({ location, unit = 'celsius' }) => {
        // Fetch weather data
        return {
          location,
          temperature: 22,
          unit,
          condition: 'Sunny',
        };
      },
    }),
  },
});
```

### Tool Properties

| Property | Required | Description |
| -------- | -------- | ----------- |
| `description` | Yes | Explains what the tool does (helps LLM decide when to use it) |
| `inputSchema` | Yes | Zod schema defining input parameters |
| `execute` | Yes | Async function that runs when tool is called |
| `outputSchema` | No | Zod schema for validating/typing output |

### With Output Schema

```ts
const weatherTool = tool({
  description: 'Get weather data',
  inputSchema: z.object({
    location: z.string(),
  }),
  outputSchema: z.object({
    temperature: z.number(),
    condition: z.string(),
    humidity: z.number(),
  }),
  execute: async ({ location }) => ({
    temperature: 22,
    condition: 'Sunny',
    humidity: 65,
  }),
});
```

### Multiple Tools

```ts
const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'What is the weather in Tokyo and what restaurants are nearby?',
  tools: {
    getWeather: tool({
      description: 'Get weather for a location',
      inputSchema: z.object({ location: z.string() }),
      execute: async ({ location }) => ({ temp: 22, condition: 'Sunny' }),
    }),
    searchRestaurants: tool({
      description: 'Search for restaurants in a location',
      inputSchema: z.object({
        location: z.string(),
        cuisine: z.string().optional(),
      }),
      execute: async ({ location, cuisine }) => ({
        restaurants: ['Sushi Place', 'Ramen Shop', 'Tempura House'],
      }),
    }),
  },
  stopWhen: stepCountIs(5),
});
```

---

## Multi-Step Tool Calls

Enable the model to call tools multiple times in sequence.

### Using stopWhen

```ts
import { generateText, tool, stepCountIs } from 'ai';

const { text, steps } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Plan a trip to Tokyo including weather and restaurants',
  tools: {
    getWeather: tool({ /* ... */ }),
    searchRestaurants: tool({ /* ... */ }),
    bookRestaurant: tool({ /* ... */ }),
  },
  stopWhen: stepCountIs(10), // Allow up to 10 steps
});

// Access all steps
for (const step of steps) {
  console.log('Step:', step.toolCalls, step.toolResults);
}
```

### Custom Stop Conditions

```ts
import { generateText, hasToolCall, hasNoToolCall } from 'ai';

// Stop when a specific tool is called
const result = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Complete the task',
  tools: { /* ... */ },
  stopWhen: hasToolCall('finalAnswer'),
});

// Stop when no tools are called (natural completion)
const result2 = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'Answer the question',
  tools: { /* ... */ },
  stopWhen: hasNoToolCall(),
});
```

### Streaming with Tools

```ts
import { streamText } from 'ai';

const result = streamText({
  model: 'openai/gpt-4o',
  prompt: 'What is the weather?',
  tools: { getWeather: tool({ /* ... */ }) },
  stopWhen: stepCountIs(5),
});

for await (const part of result.fullStream) {
  switch (part.type) {
    case 'tool-call':
      console.log('Calling tool:', part.toolName, part.args);
      break;
    case 'tool-result':
      console.log('Tool result:', part.toolName, part.result);
      break;
    case 'text':
      process.stdout.write(part.text);
      break;
  }
}
```

---

## Tool Best Practices

### Schema Tips

1. **Use descriptive names**: Tool and parameter names help the model understand intent
2. **Add `.describe()`**: Provide context for parameters

```ts
inputSchema: z.object({
  query: z.string().describe('The search query to find relevant documents'),
  limit: z.number().describe('Maximum number of results (1-100)').optional(),
})
```

3. **Use `.nullable()` for optional params** (better compatibility with strict mode):

```ts
// Better compatibility
inputSchema: z.object({
  required: z.string(),
  optional: z.string().nullable(),
})

// May cause issues with strict schema validation
inputSchema: z.object({
  required: z.string(),
  optional: z.string().optional(),
})
```

### Handling Dates

```ts
inputSchema: z.object({
  date: z
    .string()
    .date()
    .transform(value => new Date(value))
    .describe('Date in YYYY-MM-DD format'),
})
```

### Tool Description Tips

- Be specific about what the tool does
- Include information about output format if relevant
- Mention any limitations or requirements

```ts
tool({
  description: `Search the product database. Returns up to 10 matching products
    with name, price, and availability. Use specific keywords for best results.`,
  inputSchema: z.object({ query: z.string() }),
  execute: async ({ query }) => { /* ... */ },
})
```

---

## Tool Approval

Require approval for sensitive tool executions.

```ts
import { tool } from 'ai';
import { z } from 'zod';

const runCommand = tool({
  description: 'Run a shell command',
  inputSchema: z.object({ command: z.string() }),
  needsApproval: true,
  execute: async ({ command }) => ({ output: command }),
});
```

## Tool Repair

Use experimental repair hooks to fix invalid tool calls.

```ts
import { generateObject, generateText, NoSuchToolError } from 'ai';

await generateText({
  model: 'openai/gpt-4o',
  tools: { runCommand },
  prompt: 'Run ls',
  experimental_repairToolCall: async ({ toolCall, tools, inputSchema, error }) => {
    if (NoSuchToolError.isInstance(error)) return null;

    const tool = tools[toolCall.toolName as keyof typeof tools];
    const { object: repairedArgs } = await generateObject({
      model: 'openai/gpt-4o',
      schema: tool.inputSchema,
      prompt: `Fix inputs: ${JSON.stringify(toolCall.input)}`,
    });

    return { ...toolCall, input: JSON.stringify(repairedArgs) };
  },
});
```

## MCP (Model Context Protocol)

Connect to MCP servers to use their tools.

### Installation

```bash
pnpm add @ai-sdk/mcp
```

### HTTP Transport (Recommended)

```ts
import { createMCPClient } from '@ai-sdk/mcp';
import { generateText } from 'ai';

const mcpClient = await createMCPClient({
  transport: {
    type: 'http',
    url: 'https://your-mcp-server.com/mcp',
    headers: { Authorization: 'Bearer your-api-key' },
  },
});

const tools = await mcpClient.tools();

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: 'What is the weather in Brooklyn?',
  tools,
});

await mcpClient.close();
```

### SSE Transport

```ts
const mcpClient = await createMCPClient({
  transport: {
    type: 'sse',
    url: 'https://your-server.com/sse',
  },
});
```

### Stdio Transport (Local Only)

```ts
import { createMCPClient } from '@ai-sdk/mcp';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const mcpClient = await createMCPClient({
  transport: new StdioClientTransport({
    command: 'node',
    args: ['path/to/mcp-server.js'],
  }),
});
```

### With Schema Definition (Type Safety)

```ts
import { z } from 'zod';

const tools = await mcpClient.tools({
  schemas: {
    'get-weather': {
      inputSchema: z.object({
        location: z.string(),
      }),
      outputSchema: z.object({
        temperature: z.number(),
        condition: z.string(),
      }),
    },
  },
});
```

### Closing the Client

```ts
// With streaming - close in onFinish
const result = streamText({
  model: 'openai/gpt-4o',
  tools,
  prompt: 'Question',
  onFinish: async () => {
    await mcpClient.close();
  },
});

// Without streaming - use try/finally
try {
  const { text } = await generateText({
    model: 'openai/gpt-4o',
    tools,
    prompt: 'Question',
  });
} finally {
  await mcpClient.close();
}
```

---

## Built-in Provider Tools

### OpenAI Web Search

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text, sources } = await generateText({
  model: openai.responses('gpt-4o'),
  prompt: 'What happened in tech news today?',
  tools: {
    web_search_preview: openai.tools.webSearchPreview({
      searchContextSize: 'high',
      userLocation: {
        type: 'approximate',
        city: 'San Francisco',
        region: 'California',
      },
    }),
  },
});
```

### OpenAI MCP Tool

```ts
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai.responses('gpt-4o'),
  prompt: 'Search for NYC news',
  tools: {
    mcp: openai.tools.mcp({
      serverLabel: 'web-search',
      serverUrl: 'https://mcp.exa.ai/mcp',
      serverDescription: 'Web search API',
    }),
  },
});
```

### Google Search

```ts
import { google } from '@ai-sdk/google-generative-ai';
import { generateText } from 'ai';

const { text, sources } = await generateText({
  model: google('gemini-2.5-flash'),
  prompt: 'What are the latest tech trends?',
  tools: {
    google_search: google.tools.googleSearch({}),
  },
});
```

---

## Agent Patterns

### Simple Agent Loop

```ts
import { generateText, tool, stepCountIs } from 'ai';
import { z } from 'zod';

const agent = async (task: string) => {
  const { text, steps } = await generateText({
    model: 'openai/gpt-4o',
    system: `You are a helpful assistant. Use tools to complete tasks.
             When done, provide a final summary.`,
    prompt: task,
    tools: {
      search: tool({
        description: 'Search for information',
        inputSchema: z.object({ query: z.string() }),
        execute: async ({ query }) => {
          // Implement search
          return { results: ['Result 1', 'Result 2'] };
        },
      }),
      calculate: tool({
        description: 'Perform calculations',
        inputSchema: z.object({ expression: z.string() }),
        execute: async ({ expression }) => {
          return { result: eval(expression) };
        },
      }),
    },
    stopWhen: stepCountIs(10),
  });

  return { text, stepCount: steps.length };
};

const result = await agent('Research the population of Tokyo and calculate the density');
```

### RAG Pattern

```ts
import { generateText, tool, stepCountIs } from 'ai';
import { z } from 'zod';

const { text } = await generateText({
  model: 'openai/gpt-4o',
  system: `You are a helpful assistant with access to a knowledge base.
           Always check the knowledge base before answering questions.`,
  prompt: userQuestion,
  tools: {
    searchKnowledgeBase: tool({
      description: 'Search the knowledge base for relevant information',
      inputSchema: z.object({
        query: z.string().describe('Search query'),
      }),
      execute: async ({ query }) => {
        // Generate embedding for query
        const { embedding } = await embed({
          model: 'openai/text-embedding-ada-002',
          value: query,
        });

        // Search vector database
        const results = await vectorDb.search(embedding, { limit: 5 });
        return { documents: results };
      },
    }),
  },
  stopWhen: stepCountIs(3),
});
```
