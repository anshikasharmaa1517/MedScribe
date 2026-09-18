import { useEffect, useRef } from "react";
import type { Speaker, TranscriptLine } from "../types";

type Props = {
  lines: TranscriptLine[];
  partial?: string;
  recording: boolean;
  onToggleRecording: () => void;
  onSwapSpeaker: (seq: number, speaker: Speaker) => void;
};

const NEXT: Record<string, Speaker> = { doctor: "patient", patient: "doctor", null: "doctor" };

export function Transcript({ lines, partial, recording, onToggleRecording, onSwapSpeaker }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [lines.length, partial]);

  return (
    <section className="panel transcript">
      <header className="panel-head">
        <h2>Transcript</h2>
        <button className={recording ? "btn danger" : "btn primary"} onClick={onToggleRecording}>
          {recording ? "■ Stop" : "● Record"}
        </button>
      </header>
      <div className="lines">
        {lines.length === 0 && <p className="muted">Press Record to begin. Tap a role tag to swap a mislabel.</p>}
        {lines.map((l) => (
          <div key={l.seq} className={`line ${l.speaker ?? "unknown"}`}>
            <button
              className={`tag ${l.speaker ?? "unknown"}`}
              title="Tap to swap speaker"
              onClick={() => onSwapSpeaker(l.seq, NEXT[String(l.speaker)])}
            >
              {l.speaker === "doctor" ? "Dr." : l.speaker === "patient" ? "Patient" : "?"}
            </button>
            <span className="text">{l.text}</span>
          </div>
        ))}
        {partial && <div className="line partial"><span className="tag unknown">…</span><span className="text">{partial}</span></div>}
        <div ref={endRef} />
      </div>
    </section>
  );
}
