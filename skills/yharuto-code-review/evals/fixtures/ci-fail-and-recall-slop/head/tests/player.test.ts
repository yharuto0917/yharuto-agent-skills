import test from "node:test";
import assert from "node:assert/strict";
import { toggleFullscreen, type FullscreenTargetElement, type PlayerDocument } from "../src/player.ts";
import { renderPlayerView } from "../src/PlayerView.ts";

function createFakeDom(withFullscreenApi = true) {
  let activeFullscreen: FullscreenTargetElement | null = null;
  const shell: FullscreenTargetElement = { id: "talk-player-shell", dataset: { fullscreenActive: "false" } };
  const stage: FullscreenTargetElement = {
    id: "talk-player-stage",
    dataset: {},
    ...(withFullscreenApi
      ? {
          async requestFullscreen() {
            activeFullscreen = stage;
          },
        }
      : {}),
  };
  const video: FullscreenTargetElement = { id: "talk-video", dataset: {}, controls: true };
  const controls: FullscreenTargetElement = { id: "talk-player-controls", dataset: {} };

  const map: Record<string, FullscreenTargetElement> = {
    "#talk-player-shell": shell,
    "#talk-player-stage": stage,
    "#talk-video": video,
    "#talk-player-controls": controls,
  };

  const doc: PlayerDocument = {
    get fullscreenElement() {
      return activeFullscreen;
    },
    querySelector(selector: string) {
      return map[selector] ?? null;
    },
    async exitFullscreen() {
      activeFullscreen = null;
    },
  };

  return { doc, shell, stage, video, controls };
}

test("toggleFullscreen enters and exits fullscreen across consecutive calls", async () => {
  const { doc, shell, video } = createFakeDom(true);
  const first = await toggleFullscreen(doc);
  assert.equal(first, "entered");
  assert.equal(shell.dataset.fullscreenActive, "true");
  assert.equal(video.controls, false);

  const second = await toggleFullscreen(doc);
  assert.equal(second, "exited");
  assert.equal(shell.dataset.fullscreenActive, "false");
});

test("toggleFullscreen throws when required player elements are missing", async () => {
  const emptyDoc: PlayerDocument = {
    fullscreenElement: null,
    querySelector: () => null,
    exitFullscreen: async () => {},
  };
  await assert.rejects(() => toggleFullscreen(emptyDoc), /Player elements/);
});

test("toggleFullscreen throws when Fullscreen API is unavailable", async () => {
  const { doc } = createFakeDom(false);
  await assert.rejects(() => toggleFullscreen(doc), /Fullscreen API is not supported/);
});

test("renderPlayerView resolves poster URL and renders sibling control bar", () => {
  const html = renderPlayerView({
    talkId: "t1",
    streamUrl: "https://cdn.example.com/v1.mp4",
    posterPath: "/thumbs/t1.jpg",
    siteOrigin: "https://portal.example.com",
  });
  assert.match(html, /poster="https:\/\/portal\.example\.com\/thumbs\/t1\.jpg"/);
  assert.match(html, /id="talk-player-controls"/);
});
