import { useEffect, useState } from "react";
import { api, type Answer, type History, timing } from "./api";
import { currentEmail, login, logout } from "./auth";

function Login({ onDone }: { onDone: () => void }) {
  const [phone, setPhone] = useState("+91");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try { await login(phone.replace(/\s+/g, ""), password); onDone(); }
    catch (err) { setError(String((err as Error).message ?? err)); }
    finally { setBusy(false); }
  };
  return (
    <main className="page login">
      <form className="card" onSubmit={submit}>
        <h1>MedScribe</h1>
        <p className="muted">Your prescriptions and reminders</p>
        <label className="field"><span>WhatsApp number</span>
          <input className="input" type="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} required /></label>
        <label className="field"><span>Password</span>
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <p className="error">{error}</p>}
        <button className="btn primary" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}

function Timeline({ history }: { history: History }) {
  if (history.prescriptions.length === 0) return <p className="muted">No prescriptions yet.</p>;
  return (
    <ol className="timeline">
      {history.prescriptions.map((rx) => (
        <li key={rx.rxId} className="card rx">
          <div className="rx-head">
            <strong>{rx.date}</strong>
            <span className="muted">{rx.doctor}{rx.clinic ? ` · ${rx.clinic}` : ""}</span>
            {rx.document && (
              <a className="btn small" href={rx.document.url} target="_blank" rel="noreferrer">
                Download {rx.document.format.toUpperCase()}
              </a>
            )}
          </div>
          {rx.diagnosis && <p><span className="label">Diagnosis</span> {rx.diagnosis}</p>}
          <ul className="meds">
            {rx.medicines.map((m, i) => (
              <li key={i}><strong>{m.label}</strong><span className="muted"> — {timing(m)}</span></li>
            ))}
          </ul>
          {rx.tests_advised.length > 0 && <p><span className="label">Tests</span> {rx.tests_advised.join(", ")}</p>}
          {rx.next_visit && <p><span className="label">Next visit</span> {rx.next_visit}</p>}
        </li>
      ))}
    </ol>
  );
}

function Reminders({ history, now }: { history: History; now: number }) {
  const upcoming = history.reminders
    .filter((r) => r.status === "SCHEDULED" && new Date(r.dueAt).getTime() > now - 3600_000)
    .sort((a, b) => a.dueAt.localeCompare(b.dueAt)).slice(0, 6);
  const recent = history.reminders.filter((r) => r.status !== "SCHEDULED").slice(-4).reverse();
  if (upcoming.length === 0 && recent.length === 0) return null;
  const fmt = (iso: string) => new Date(iso).toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
  return (
    <section className="card">
      <h2>Reminders</h2>
      {upcoming.length > 0 && <ul className="plain">{upcoming.map((r) => <li key={r.remId}>⏰ {fmt(r.dueAt)} — {r.label}</li>)}</ul>}
      {recent.length > 0 && <ul className="plain muted">{recent.map((r) => <li key={r.remId}>{r.status === "TAKEN" ? "✅" : "📩"} {fmt(r.dueAt)} — {r.label} ({r.status.toLowerCase()})</li>)}</ul>}
      <p className="muted small">Reply <strong>TAKEN</strong> on WhatsApp after each dose.</p>
    </section>
  );
}

function Ask() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [thread, setThread] = useState<{ q: string; a: Answer }[]>([]);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const question = q.trim(); if (!question) return;
    setBusy(true); setQ("");
    try { const a = await api.ask(question); setThread((t) => [...t, { q: question, a }]); }
    catch (err) { setThread((t) => [...t, { q: question, a: { answer: String((err as Error).message ?? err), sources: [] } }]); }
    finally { setBusy(false); }
  };
  return (
    <section className="card">
      <h2>Ask about my history</h2>
      <p className="muted small">Answers come only from your records here. For anything new, contact your doctor.</p>
      <div className="thread">
        {thread.map((t, i) => (
          <div key={i}>
            <p className="q">{t.q}</p>
            <p className="a">{t.a.answer}{t.a.sources.length > 0 && <span className="muted small"> · from {t.a.sources.length} prescription{t.a.sources.length > 1 ? "s" : ""}</span>}</p>
          </div>
        ))}
        {busy && <p className="a muted">…</p>}
      </div>
      <form className="askbar" onSubmit={send}>
        <input className="input" placeholder="e.g. raat ko kya lena hai?" value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} />
        <button className="btn primary" disabled={busy || !q.trim()}>Ask</button>
      </form>
    </section>
  );
}

export default function App() {
  const [who, setWho] = useState<string | null>(() => currentEmail());
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState(0);

  useEffect(() => {
    const off = () => { setWho(null); setHistory(null); };
    window.addEventListener("medscribe:unauthorized", off);
    return () => window.removeEventListener("medscribe:unauthorized", off);
  }, []);

  useEffect(() => {
    if (!who) return;
    api.history().then((h) => { setHistory(h); setLoadedAt(Date.now()); }).catch((e) => setError(String(e.message ?? e)));
  }, [who]);

  if (!who) return <Login onDone={() => setWho(currentEmail())} />;

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>{history?.patient.name ?? "…"}</h1>
          <span className="muted">{history?.patient.age ? `${history.patient.age}` : ""}{history?.patient.sex ? ` / ${history.patient.sex}` : ""} · {who}</span>
        </div>
        <button className="btn ghost small" onClick={() => { logout(); setWho(null); setHistory(null); }}>Sign out</button>
      </header>
      {error && <p className="error" onClick={() => setError(null)}>{error}</p>}
      {!history && !error && <p className="muted">Loading…</p>}
      {history && (
        <>
          <Reminders history={history} now={loadedAt} />
          <Ask />
          <h2>Prescriptions</h2>
          <Timeline history={history} />
        </>
      )}
    </main>
  );
}
