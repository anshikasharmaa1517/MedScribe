// Transcript sources. The consultation screen only knows this interface, so the
// existing Sarvam Saaras WebSocket client plugs in here without touching the UI.

import { DEMO_SCRIPT } from "./mock/api";
import type { Speaker } from "./types";

export type OnLine = (line: { speaker: Speaker; text: string }) => void;

export interface TranscriptSource {
  start(onLine: OnLine): Promise<void>;
  stop(): void;
}

// Streams the scripted demo consultation, one line every `intervalMs`.
export function scriptedSource(intervalMs = 2500): TranscriptSource {
  let timer: number | undefined;
  return {
    async start(onLine) {
      let i = 0;
      const tick = () => {
        if (i >= DEMO_SCRIPT.length) return;
        onLine(DEMO_SCRIPT[i++]);
        timer = window.setTimeout(tick, intervalMs);
      };
      tick();
    },
    stop() { clearTimeout(timer); },
  };
}

// Placeholder for the live mic -> Sarvam Saaras v3 path. Wire the existing
// WebSocket client here: open the socket on start(), call onLine() for each
// final segment with speaker = null (the doctor tags roles on screen), close on stop().
export function sarvamSource(): TranscriptSource {
  return {
    async start() { throw new Error("Sarvam source not wired yet - set VITE_STT=scripted"); },
    stop() {},
  };
}

export function transcriptSource(): TranscriptSource {
  return (import.meta.env.VITE_STT ?? "scripted") === "sarvam" ? sarvamSource() : scriptedSource();
}
