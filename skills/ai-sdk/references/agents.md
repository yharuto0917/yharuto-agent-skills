# Agents Reference

## Table of Contents
- [ToolLoopAgent](#tooloopagent)
- [Agent Configuration](#agent-configuration)
- [Loop Control](#loop-control)
- [Workflow Patterns](#workflow-patterns)
- [Manual Agent Loops](#manual-agent-loops)
- [Call Options](#call-options)
- [Agent UI Types](#agent-ui-types)

---

## ToolLoopAgent

Use `ToolLoopAgent` to encapsulate model configuration, tools, and loop behavior.

```ts
import { ToolLoopAgent, stepCountIs, tool } from 'ai';
import { z } from 'zod';

const weatherAgent = new ToolLoopAgent({
  model: 'openai/gpt-4o',
  instructions: 'You are a helpful assistant.',
  tools: {
    getWeather: tool({
      description: 'Get weather for a location',
      inputSchema: z.object({ location: z.string() }),
      execute: async ({ location }) => ({ location, temperature: 22 }),
    }),
  },
  stopWhen: stepCountIs(5),
});

const { text, steps } = await weatherAgent.generate({
  prompt: 'What is the weather in Tokyo?',
});
```

## Agent Configuration

Agents accept the same settings as `generateText` and `streamText`, plus agent-specific options.

```ts
import { ToolLoopAgent } from 'ai';

const agent = new ToolLoopAgent({
  model: 'anthropic/claude-sonnet-4-20250514',
  instructions: 'You are a support agent for a SaaS product.',
  toolChoice: 'auto',
});
```

Common settings:

- `instructions`: system behavior and constraints
- `tools`: tool set for multi-step execution
- `toolChoice`: `auto`, `none`, `required`, or `tool`
- `output`: `Output.object` or `Output.array` for structured responses

## Loop Control

Control execution with `stopWhen` and `prepareStep`.

```ts
import { ToolLoopAgent, stepCountIs } from 'ai';

const agent = new ToolLoopAgent({
  model: 'openai/gpt-4o',
  stopWhen: stepCountIs(10),
  prepareStep: async ({ stepNumber, messages }) => {
    if (stepNumber > 3 && messages.length > 12) {
      return { model: 'openai/gpt-4o-mini' };
    }
    return {};
  },
});
```

## Workflow Patterns

Common patterns for reliable agents:

- Sequential chains for ordered steps
- Parallel subtasks for independent analysis
- Evaluator-optimizer loops for quality checks
- Orchestrator-worker for coordination
- Routing based on input classification

```ts
import { generateObject, generateText } from 'ai';
import { z } from 'zod';

async function routeRequest(query: string) {
  const { object: classification } = await generateObject({
    model: 'openai/gpt-4o',
    schema: z.object({ type: z.enum(['general', 'technical']) }),
    prompt: `Classify: ${query}`,
  });

  return generateText({
    model: classification.type === 'technical' ? 'openai/gpt-4o' : 'openai/gpt-4o-mini',
    prompt: query,
  });
}
```

## Manual Agent Loops

Use manual loops for full control over steps and message management.

```ts
import { generateText } from 'ai';

const messages = [{ role: 'user', content: 'Plan a trip to Tokyo.' }];
let step = 0;

while (step < 10) {
  const result = await generateText({
    model: 'openai/gpt-4o',
    messages,
    tools: {
      // tools here
    },
  });

  messages.push(...result.response.messages);

  if (result.text) break;
  step++;
}
```

## Call Options

Define type-safe inputs to customize agent behavior at runtime.

```ts
import { ToolLoopAgent } from 'ai';
import { z } from 'zod';

const supportAgent = new ToolLoopAgent({
  model: 'openai/gpt-4o',
  callOptionsSchema: z.object({
    userId: z.string(),
    accountType: z.enum(['free', 'pro', 'enterprise']),
  }),
  instructions: 'You are a support agent.',
  prepareCall: ({ options, ...settings }) => ({
    ...settings,
    instructions: `${settings.instructions}\nAccount: ${options.accountType}`,
  }),
});

await supportAgent.generate({
  prompt: 'How do I upgrade?',
  options: { userId: 'user_123', accountType: 'pro' },
});
```

## Agent UI Types

Use helper types to keep tool and message types aligned between server and UI.

```ts
import { InferAgentUIMessage, ToolLoopAgent } from 'ai';

const agent = new ToolLoopAgent({
  model: 'openai/gpt-4o',
  tools: {
    // tools here
  },
});

type MyAgentUIMessage = InferAgentUIMessage<typeof agent>;
```
