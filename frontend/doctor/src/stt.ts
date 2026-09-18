// Transcript sources. The consultation screen only knows this interface.

import hotwords from "./hotwords.json";
import { DEMO_SCRIPT } from "./mock/api";
import type { Speaker } from "./types";

export type OnLine = (line: { speaker: Speaker; text: string }) => void;
export type OnPartial = (text: string) => void;

export interface TranscriptSource {
  start(onLine: OnLine, onPartial?: OnPartial): Promise<void>;
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

// ---------------------------------------------------------------------------
// Live mic -> Sarvam Saaras realtime WebSocket.
//
// The browser authenticates with the API key as a WebSocket subprotocol
// (Sarvam issues no short-lived tokens), so the key is visible to anyone who
// can open devtools on this page. Acceptable for a locally served demo; a
// production deployment must relay the socket through the backend instead.
//
// Audio is captured at 16 kHz mono via an AudioWorklet, sent as PCM16 base64
// in ~100 ms chunks. Sarvam's VAD splits utterances; each `transcript.final`
// becomes one transcript line with speaker = null - the doctor tags roles.

const SARVAM_WS = "wss://api.sarvam.ai/speech-to-text-realtime/ws";
const SAMPLE_RATE = 16000;

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = []; this.len = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    this.buf.push(new Float32Array(ch)); this.len += ch.length;
    if (this.len >= 1600) {                       // ~100 ms at 16 kHz
      const out = new Int16Array(this.len); let o = 0;
      for (const b of this.buf) for (let i = 0; i < b.length; i++) {
        const s = Math.max(-1, Math.min(1, b[i]));
        out[o++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(out.buffer, [out.buffer]);
      this.buf = []; this.len = 0;
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

function b64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000) as unknown as number[]);
  }
  return btoa(s);
}

export function sarvamSource(): TranscriptSource {
  const key = import.meta.env.VITE_SARVAM_API_KEY as string | undefined;
  const lang = (import.meta.env.VITE_STT_LANG as string | undefined) ?? "hi-IN";
  const mode = (import.meta.env.VITE_STT_MODE as string | undefined) ?? "codemix";
  const model = (import.meta.env.VITE_STT_MODEL as string | undefined) ?? "saaras:v3-realtime";

  let ws: WebSocket | null = null;
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let node: AudioWorkletNode | null = null;
  let stopped = false;

  const teardown = () => {
    node?.port.close(); node?.disconnect(); node = null;
    stream?.getTracks().forEach((t) => t.stop()); stream = null;
    ctx?.close().catch(() => {}); ctx = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ event: "end" })); } catch { /* closing anyway */ }
      ws.close();
    }
    ws = null;
  };

  return {
    async start(onLine, onPartial) {
      if (!key) throw new Error("VITE_SARVAM_API_KEY is not set");
      stopped = false;

      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: SAMPLE_RATE, echoCancellation: true, noiseSuppression: true },
      });
      ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));

      const params = new URLSearchParams({
        language_code: lang, model, mode, encoding: "linear16", sample_rate: String(SAMPLE_RATE),
        stream_type: "balanced", endpointing: "vad",
      });
      const socket = new WebSocket(`${SARVAM_WS}?${params}`, [`api-subscription-key.${key}`]);
      ws = socket;

      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve();
        socket.onerror = () => reject(new Error("could not connect to Sarvam"));
        socket.onclose = (e) => { if (!stopped) reject(new Error(`Sarvam closed the socket (${e.code})`)); };
      });

      socket.onclose = (e) => {
        if (!stopped) onPartial?.(`[connection closed: ${e.code}${e.reason ? " " + e.reason : ""}]`);
      };
      socket.onmessage = (ev) => {
        let msg: { event: string; text?: string; message?: string; is_fatal?: boolean; code?: string };
        try { msg = JSON.parse(ev.data); } catch { return; }
        switch (msg.event) {
          case "session.begin":
            // Drug-name hints: v3 realtime has no keyterms, so they ride on the prompt.
            socket.send(JSON.stringify({
              event: "config.update",
              prompt: "Doctor-patient consultation in Hinglish. Medicine names: " + (hotwords as string[]).join(", "),
            }));
            break;
          case "transcript.partial":
            if (msg.text) onPartial?.(msg.text);
            break;
          case "transcript.final":
            onPartial?.("");
            if (msg.text?.trim()) onLine({ speaker: null, text: msg.text.trim() });
            break;
          case "error":
            onPartial?.(`[stt ${msg.code ?? "error"}: ${msg.message ?? ""}]`);
            if (msg.is_fatal) teardown();
            break;
        }
      };

      node = new AudioWorkletNode(ctx, "pcm-capture");
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ event: "audio_input", audio: b64(e.data) }));
        }
      };
      ctx.createMediaStreamSource(stream).connect(node);
      // Worklet output is not routed to speakers: nothing to hear, nothing to echo.
    },
    stop() { stopped = true; teardown(); },
  };
}

export function transcriptSource(): TranscriptSource {
  return (import.meta.env.VITE_STT ?? "scripted") === "sarvam" ? sarvamSource() : scriptedSource();
}
