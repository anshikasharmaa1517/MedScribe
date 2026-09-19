import { useEffect, useState } from "react";
import { usingMock } from "./api";
import { authConfigured, currentEmail, logout } from "./auth";
import { Consultation } from "./screens/Consultation";
import { Login } from "./screens/Login";
import { Review } from "./screens/Review";
import { WaitingList } from "./screens/WaitingList";
import type { Patient } from "./types";

const ENV_TOKEN = import.meta.env.VITE_ID_TOKEN as string | undefined;

export default function App() {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [view, setView] = useState<"consult" | "review">("consult");
  const [email, setEmail] = useState<string | null>(() => currentEmail());

  useEffect(() => {
    const onUnauthorized = () => { setEmail(null); setPatient(null); };
    window.addEventListener("medscribe:unauthorized", onUnauthorized);
    return () => window.removeEventListener("medscribe:unauthorized", onUnauthorized);
  }, []);

  // Login is required when talking to the real API and no dev token is provided.
  const needsLogin = !usingMock && authConfigured && !email && !ENV_TOKEN;
  if (needsLogin) return <Login onLoggedIn={() => setEmail(currentEmail())} />;

  const signOut = () => { logout(); setEmail(null); setPatient(null); };

  return (
    <>
      {usingMock && <div className="mockbar">MOCK API — set VITE_API_BASE to use the real backend</div>}
      {(email || usingMock) && (
        <div className="sessionbar">
          <button className="btn ghost small" onClick={() => setView(view === "review" ? "consult" : "review")}>
            {view === "review" ? "Consultations" : "Review queue"}
          </button>
          <span className="muted">{email}</span>
          <button className="btn ghost small" onClick={signOut}>Sign out</button>
        </div>
      )}
      {view === "review"
        ? <Review onBack={() => setView("consult")} />
        : patient
        ? <Consultation key={patient.patientId} patient={patient} onBack={() => setPatient(null)} />
        : <WaitingList onStart={setPatient} />}
    </>
  );
}
