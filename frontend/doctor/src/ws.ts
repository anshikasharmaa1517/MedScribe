// WebSocket transport for a live consult (step 14). One socket per consult,
// opened with a single-use ticket from the REST API. Falls back to polling
// after repeated failures: `onFallback` fires once and the caller switches.

import { api } from "./api";
import type { Draft, Speaker } from "./types";

const WS_URL = import.meta.env.VITE_WS_URL as string | undefined;
export const wsConfigured = Boolean(WS_URL) && (import.meta.env.VITE_TRANSPORT ?? "ws") !== "poll";

type Handlers = {
  onDraft: (d: Draft) => void;
  onError: (msg: string) => void;
  onFallback: (reason: string) => void;
  onStatus?: (s: "connecting" | "open" | "closed") => void;
};

export class ConsultSocket {
  private ws: WebSocket | null = null;
  private failures = 0;
  private closed = false;
  private refreshTimer: number | undefined;
  private pending: string[] = [];
  private consultId: string;
  private h: Handlers;

  constructor(consultId: string, h: Handlers) {
    this.consultId = consultId;
    this.h = h;
  }

  async open() {
    if (this.closed) return;
    this.h.onStatus?.("connecting");
    let ticket: string;
    try { ticket = (await api.wsTicket(this.consultId)).ticket; }
    catch (e) { this.fail(`ticket: ${(e as Error).message}`); return; }

    const ws = new WebSocket(`${WS_URL}?ticket=${encodeURIComponent(ticket)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.failures = 0;
      this.h.onStatus?.("open");
      for (const m of this.pending.splice(0)) ws.send(m);
    };
    ws.onmessage = (ev) => {
      let msg: { action: string; draft?: Draft; error?: string; nextRefreshMs?: number };
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.action === "draft.update" && msg.draft) this.h.onDraft(msg.draft);
      else if (msg.action === "draft.error") this.h.onError(msg.error ?? "extraction failed");
      else if (msg.action === "error") this.h.onError(msg.error ?? "socket error");
      else if (msg.action === "ack" && msg.nextRefreshMs) this.scheduleRefresh(msg.nextRefreshMs + 500);
    };
    ws.onclose = (ev) => {
      this.h.onStatus?.("closed");
      if (this.closed) return;
      if (ev.code === 1000) return;
      this.fail(`closed (${ev.code})`);
    };
    ws.onerror = () => { /* onclose follows with the code */ };
  }

  private fail(reason: string) {
    this.failures += 1;
    if (this.failures >= 3) { this.h.onFallback(reason); this.close(); return; }
    window.setTimeout(() => this.open(), 1000 * this.failures);
  }

  // The server only extracts when the interval has elapsed; ack tells us when
  // to nudge it so the draft arrives without polling.
  private scheduleRefresh(ms: number) {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = window.setTimeout(() => this.send({ action: "draft.refresh" }), ms);
  }

  private send(obj: object) {
    const raw = JSON.stringify(obj);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(raw);
    else this.pending.push(raw);
  }

  appendTranscript(line: { text: string; speaker: Speaker; seq: number }) {
    this.send({ action: "transcript.append", ...line });
  }

  refresh() { this.send({ action: "draft.refresh" }); }

  close() {
    this.closed = true;
    clearTimeout(this.refreshTimer);
    this.ws?.close(1000);
    this.ws = null;
  }
}
