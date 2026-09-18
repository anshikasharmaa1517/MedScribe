import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { DraftPanel } from "../components/DraftPanel";
import { Transcript } from "../components/Transcript";
import { transcriptSource } from "../stt";
import type { Consult, Draft, DraftPatch, Patient, Speaker, TranscriptLine } from "../types";

const POLL_MS = 3000;

type Props = { patient: Patient; onBack: () => void };

export function Consultation({ patient, onBack }: Props) {
  const [consult, setConsult] = useState<Consult | null>(null);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [recording, setRecording] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const source = useRef(transcriptSource());
  const patching = useRef(0);

  useEffect(() => {
    api.createConsult(patient.patientId).then(setConsult).catch((e) => setError(String(e.message ?? e)));
    const src = source.current;
    return () => src.stop();
  }, [patient.patientId]);

  // Poll the draft. Skip a tick while a PATCH is in flight so a stale poll
  // can't briefly undo the doctor's edit on screen.
  useEffect(() => {
    if (!consult || approved) return;
    let live = true;
    const tick = async () => {
      if (patching.current > 0) return;
      try {
        const d = await api.getDraft(consult.consultId);
        if (live && patching.current === 0) setDraft(d);
      } catch (e) { if (live) setError(String((e as Error).message ?? e)); }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, [consult, approved]);

  const onLine = useCallback((line: { speaker: Speaker; text: string }) => {
    if (!consult) return;
    const entry: TranscriptLine = { ...line, seq: ++seq.current, at: new Date().toISOString() };
    setLines((prev) => [...prev, entry]);
    api.appendTranscript(consult.consultId, { text: line.text, speaker: line.speaker, seq: entry.seq })
      .catch((e) => setError(String(e.message ?? e)));
  }, [consult]);

  const toggleRecording = async () => {
    if (recording) { source.current.stop(); setRecording(false); return; }
    try { await source.current.start(onLine); setRecording(true); }
    catch (e) { setError(String((e as Error).message ?? e)); }
  };

  const swapSpeaker = (lineSeq: number, speaker: Speaker) =>
    setLines((prev) => prev.map((l) => (l.seq === lineSeq ? { ...l, speaker } : l)));

  const patch = async (p: DraftPatch) => {
    if (!consult) return;
    patching.current += 1;
    try { setDraft(await api.patchDraft(consult.consultId, p)); }
    catch (e) { setError(String((e as Error).message ?? e)); }
    finally { patching.current -= 1; }
  };

  const approve = async () => {
    if (!consult) return;
    setApproving(true);
    try {
      source.current.stop(); setRecording(false);
      await api.approve(consult.consultId);
      setApproved(true);
    } catch (e) { setError(String((e as Error).message ?? e)); }
    finally { setApproving(false); }
  };

  return (
    <main className="page consult">
      <header className="topbar">
        <button className="btn ghost" onClick={onBack}>← Waiting list</button>
        <span className="who">
          <strong>{patient.name}</strong>
          <span className="muted"> {patient.age ? `${patient.age}` : ""}{patient.sex ? ` / ${patient.sex}` : ""} · {patient.phone}</span>
        </span>
        <span className="muted small-text">{consult ? consult.consultId : "starting…"}</span>
      </header>
      {error && <p className="error" onClick={() => setError(null)}>{error} (tap to dismiss)</p>}
      <div className="split">
        <Transcript lines={lines} recording={recording} onToggleRecording={toggleRecording} onSwapSpeaker={swapSpeaker} />
        <DraftPanel draft={draft} onPatch={patch} onApprove={approve} approving={approving} approved={approved} />
      </div>
    </main>
  );
}
