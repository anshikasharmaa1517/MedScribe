import { useEffect, useState } from "react";
import { api } from "../api";
import type { Proposal } from "../types";

type Props = { onBack: () => void };

// Tier 2 review: agent-proposed medicines wait here until a human decides.
// Nothing on this screen writes to Tier 0 - approval only records the decision.
export function Review({ onBack }: Props) {
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => api.listPending().then(setItems).catch((e) => setError(String(e.message ?? e)));
  useEffect(() => { load(); }, []);

  const decide = async (p: Proposal, decision: "APPROVED" | "REJECTED") => {
    setBusy(p.propId);
    try { await api.review(p.createdAt, p.propId, decision); setItems((xs) => (xs ?? []).filter((x) => x.propId !== p.propId)); }
    catch (e) { setError(String((e as Error).message ?? e)); }
    finally { setBusy(null); }
  };

  return (
    <main className="page">
      <header className="topbar">
        <button className="btn ghost" onClick={onBack}>← Waiting list</button>
        <h1>Review queue</h1>
      </header>
      <p className="muted">
        Medicines proposed by the web-search agent (Tier 2). They are invisible to matching until
        a reviewer approves them — and even then a human adds them to the verified list.
      </p>
      {error && <p className="error" onClick={() => setError(null)}>{error} (tap to dismiss)</p>}
      {!items && !error && <p className="muted">Loading…</p>}
      {items && items.length === 0 && <p className="muted">Nothing pending.</p>}
      {items && items.length > 0 && (
        <table className="review">
          <thead>
            <tr><th>Heard</th><th>Proposed</th><th>Salts</th><th>Evidence</th><th>Conf.</th><th>Proposed on</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.propId}>
                <td><em>"{p.spoken}"</em></td>
                <td><strong>{p.proposed_brand}</strong>{p.note && <div className="muted small-text">{p.note}</div>}</td>
                <td>{(p.proposed_salts ?? []).join(" + ")}</td>
                <td>{p.evidence_url ? <a href={p.evidence_url} target="_blank" rel="noreferrer">source</a> : "—"}</td>
                <td>{p.confidence != null ? Math.round(p.confidence * 100) + "%" : "—"}</td>
                <td className="muted">{p.createdAt.slice(0, 10)}</td>
                <td className="actions-cell">
                  <button className="btn small primary" disabled={busy === p.propId} onClick={() => decide(p, "APPROVED")}>Approve</button>
                  <button className="btn small danger" disabled={busy === p.propId} onClick={() => decide(p, "REJECTED")}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
