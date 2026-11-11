# Retrieval Reference

## Table of Contents
- [Embeddings](#embeddings)
- [Reranking](#reranking)
- [RAG Flow](#rag-flow)

---

## Embeddings

Embed values for semantic search with `embed` or `embedMany`.

```ts
import { embedMany } from 'ai';

const { embeddings } = await embedMany({
  model: 'openai/text-embedding-3-small',
  values: ['first doc', 'second doc'],
});
```

Similarity with cosine distance:

```ts
import { cosineSimilarity } from 'ai';

const similarity = cosineSimilarity(embeddings[0], embeddings[1]);
```

## Reranking

Rerank documents for higher relevance after initial retrieval.

```ts
import { rerank } from 'ai';
import { cohere } from '@ai-sdk/cohere';

const { ranking } = await rerank({
  model: cohere.reranking('rerank-v3.5'),
  documents: ['doc one', 'doc two'],
  query: 'find the relevant one',
  topN: 1,
});
```

## RAG Flow

Typical retrieval-augmented generation flow:

1. Chunk and embed source content.
2. Persist embeddings in a vector index.
3. Embed user query.
4. Retrieve top-K similar items.
5. Optionally rerank the candidates.
6. Send retrieved context to the model in `prompt` or `messages`.

```ts
import { embed, generateText } from 'ai';

const { embedding } = await embed({
  model: 'openai/text-embedding-3-small',
  value: userQuery,
});

const context = await vectorDb.search(embedding, { limit: 4 });

const { text } = await generateText({
  model: 'openai/gpt-4o',
  prompt: `Use this context:\n${context.join('\n')}`,
});
```
