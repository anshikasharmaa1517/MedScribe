import { useState } from "react";
import { usingMock } from "./api";
import { Consultation } from "./screens/Consultation";
import { WaitingList } from "./screens/WaitingList";
import type { Patient } from "./types";

export default function App() {
  const [patient, setPatient] = useState<Patient | null>(null);
  return (
    <>
      {usingMock && <div className="mockbar">MOCK API — set VITE_API_BASE to use the real backend</div>}
      {patient
        ? <Consultation key={patient.patientId} patient={patient} onBack={() => setPatient(null)} />
        : <WaitingList onStart={setPatient} />}
    </>
  );
}
