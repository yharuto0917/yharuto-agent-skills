export interface FullscreenTargetElement {
  id: string;
  dataset: Record<string, string | undefined>;
  controls?: boolean;
  requestFullscreen?: () => Promise<void>;
}

export interface PlayerDocument {
  fullscreenElement: FullscreenTargetElement | null;
  querySelector(selector: string): FullscreenTargetElement | null;
  exitFullscreen(): Promise<void>;
}

export function isPlayerFullscreen(doc: PlayerDocument): boolean {
  return doc.fullscreenElement !== null;
}

export async function toggleFullscreen(doc: PlayerDocument): Promise<"entered" | "exited"> {
  const shellEl = doc.querySelector("#talk-player-shell");
  const stageEl = doc.querySelector("#talk-player-stage");
  const videoEl = doc.querySelector("#talk-video");
  if (!shellEl || !stageEl) {
    throw new Error("Player elements (#talk-player-shell / #talk-player-stage) not found");
  }

  if (doc.fullscreenElement) {
    await doc.exitFullscreen();
    shellEl.dataset.fullscreenActive = "false";
    return "exited";
  }

  if (typeof stageEl.requestFullscreen !== "function") {
    throw new Error("Fullscreen API is not supported on stage element");
  }

  if (videoEl) {
    videoEl.controls = false;
  }
  await stageEl.requestFullscreen();
  shellEl.dataset.fullscreenActive = "true";
  return "entered";
}
