import { useEffect, useState } from "react";
import { api } from "../api";
import type { Patient } from "../types";

type Props = { onStart: (patient: Patient) => void };

export function WaitingList({ onStart }: Props) {
  const [patients, setPatients] = useState<Patient[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listPatients().then(setPatients).catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <main className="page">
      <h1>Waiting</h1>
      <p className="muted">Patients who scanned the clinic QR. Tap to start a consultation.</p>
      {error && <p className="error">{error}</p>}
      {!patients && !error && <p className="muted">Loading…</p>}
      {patients && (
        <ul className="list">
          {patients.map((p) => (
            <li key={p.patientId}>
              <button className="row" onClick={() => onStart(p)}>
                <span className="row-main">
                  <strong>{p.name}</strong>
                  <span className="muted">
                    {p.age ? `${p.age}` : ""}{p.sex ? ` / ${p.sex}` : ""} · {p.phone}
                  </span>
                </span>
                <span className="muted">{p.lastVisit ? `Last visit ${p.lastVisit}` : "New patient"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
