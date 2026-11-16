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
