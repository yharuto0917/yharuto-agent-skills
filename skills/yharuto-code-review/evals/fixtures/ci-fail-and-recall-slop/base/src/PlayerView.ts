export interface PlayerViewProps {
  talkId: string;
  streamUrl: string;
  posterPath: string;
  siteOrigin: string;
}

export function renderPlayerView(props: PlayerViewProps): string {
  const posterHref = new URL(props.posterPath, props.siteOrigin).toString();
  return `
<section id="talk-player-shell" class="player-shell" data-fullscreen-active="false">
  <div id="talk-player-stage" class="player-stage">
    <video id="talk-video" src="${props.streamUrl}" poster="${posterHref}" controls></video>
  </div>
  <div id="talk-player-controls" class="player-control-bar">
    <button type="button" data-action="play-toggle">再生 / 一時停止</button>
    <button type="button" data-action="fullscreen-toggle">全画面表示</button>
  </div>
</section>`.trim();
}
