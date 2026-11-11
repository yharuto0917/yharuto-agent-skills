# Server Integrations Reference

## Table of Contents
- [Node.js HTTP Server](#nodejs-http-server)
- [Express](#express)
- [Hono](#hono)
- [Fastify](#fastify)
- [Nest.js](#nestjs)

---

## Node.js HTTP Server

```ts
import { createServer } from 'http';
import { streamText } from 'ai';

createServer(async (_req, res) => {
  const result = streamText({
    model: 'openai/gpt-4o',
    prompt: 'Invent a new holiday and describe its traditions.',
  });

  result.pipeUIMessageStreamToResponse(res);
}).listen(8080);
```

## Express

```ts
import express from 'express';
import { streamText } from 'ai';

const app = express();

app.post('/', async (_req, res) => {
  const result = streamText({
    model: 'openai/gpt-4o',
    prompt: 'Invent a new holiday and describe its traditions.',
  });

  result.pipeUIMessageStreamToResponse(res);
});

app.listen(8080);
```

## Hono

```ts
import { serve } from '@hono/node-server';
import { streamText } from 'ai';
import { Hono } from 'hono';

const app = new Hono();

app.post('/', async () => {
  const result = streamText({
    model: 'openai/gpt-4o',
    prompt: 'Invent a new holiday and describe its traditions.',
  });

  return result.toUIMessageStreamResponse();
});

serve({ fetch: app.fetch, port: 8080 });
```

## Fastify

```ts
import Fastify from 'fastify';
import { streamText } from 'ai';

const fastify = Fastify({ logger: true });

fastify.post('/', async (_request, reply) => {
  const result = streamText({
    model: 'openai/gpt-4o',
    prompt: 'Invent a new holiday and describe its traditions.',
  });

  reply.header('Content-Type', 'text/plain; charset=utf-8');

  return reply.send(result.textStream);
});

fastify.listen({ port: 8080 });
```

## Nest.js

```ts
import { Controller, Post, Res } from '@nestjs/common';
import { streamText } from 'ai';
import { Response } from 'express';

@Controller()
export class AppController {
  @Post('/')
  async root(@Res() res: Response) {
    const result = streamText({
      model: 'openai/gpt-4o',
      prompt: 'Invent a new holiday and describe its traditions.',
    });

    result.pipeUIMessageStreamToResponse(res);
  }
}
```
