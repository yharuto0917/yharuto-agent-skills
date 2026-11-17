import test from "node:test";
import assert from "node:assert/strict";
import { isPlayerFullscreen } from "../src/player.ts";
import { renderPlayerView } from "../src/PlayerView.ts";

test("isPlayerFullscreen reflects current fullscreen element", () => {
  assert.equal(
    isPlayerFullscreen({
      fullscreenElement: null,
      querySelector: () => null,
      exitFullscreen: async () => {},
    }),
    false,
  );
});

test("renderPlayerView renders shell, stage, and sibling control bar", () => {
  const html = renderPlayerView({
    talkId: "t1",
    streamUrl: "https://cdn.example.com/v1.mp4",
    posterPath: "/thumbs/t1.jpg",
    siteOrigin: "https://portal.example.com",
  });
  assert.match(html, /id="talk-player-shell"/);
  assert.match(html, /id="talk-player-stage"/);
  assert.match(html, /id="talk-player-controls"/);
});
