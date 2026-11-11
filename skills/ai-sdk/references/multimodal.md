# Multimodal Reference

## Table of Contents
- [Image Generation](#image-generation)
- [Gemini Flash Image](#gemini-flash-image)
- [Transcription](#transcription)
- [Speech](#speech)

---

## Image Generation

Use `generateImage` with image models such as DALL-E or Imagen.

```ts
import { generateImage } from 'ai';
import { openai } from '@ai-sdk/openai';

const { image } = await generateImage({
  model: openai.image('dall-e-3'),
  prompt: 'Santa Claus driving a Cadillac',
});
```

## Gemini Flash Image

Gemini 2.5 Flash Image returns images in `result.files` from `generateText`.

```ts
import { generateText } from 'ai';

const result = await generateText({
  model: 'google/gemini-2.5-flash-image',
  prompt: 'Create a picture of a sunset over mountains',
});

for (const file of result.files) {
  if (file.mediaType.startsWith('image/')) {
    // file.uint8Array
  }
}
```

## Transcription

Transcribe audio with `experimental_transcribe`.

```ts
import { experimental_transcribe as transcribe } from 'ai';
import { openai } from '@ai-sdk/openai';
import { readFile } from 'fs/promises';

const transcript = await transcribe({
  model: openai.transcription('whisper-1'),
  audio: await readFile('audio.mp3'),
});
```

## Speech

Generate speech from text with `experimental_generateSpeech`.

```ts
import { experimental_generateSpeech as generateSpeech } from 'ai';
import { openai } from '@ai-sdk/openai';

const audio = await generateSpeech({
  model: openai.speech('tts-1'),
  text: 'Hello, world!',
  voice: 'alloy',
});
```
