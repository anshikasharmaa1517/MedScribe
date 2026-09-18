import { useState } from "react";
import type { Medicine, MedicinePatch } from "../types";
import { BrandPicker } from "./BrandPicker";

type Props = { med: Medicine; onPatch: (p: MedicinePatch) => void };

const FREQ = ["1-0-1", "1-1-1", "1-0-0", "0-0-1", "OD", "BD", "TDS", "QID", "SOS", "HS", "stat"];
const FOOD = ["after food", "before food"];

export function MedicineRow({ med, onPatch }: Props) {
  const [picking, setPicking] = useState(false);
  const tone = med.status === "AUTO" ? "green" : med.status === "CONFIRM" ? "amber" : "red";
  const flagged = med.clinical === "CONTRADICTS";

  if (med.deleted) {
    return (
      <li className="med deleted">
        <span className="muted">Removed: {med.matched ?? med.spoken}</span>
      </li>
    );
  }

  return (
    <li className={`med ${tone}${flagged ? " flagged" : ""}${med.locked ? " locked" : ""}`}>
      <div className="med-head">
        <span className={`badge ${tone}`}>{med.status}</span>
        <span className="med-name">
          {med.matched ?? <em className="muted">unresolved</em>}
          <span className="spoken">heard "{med.spoken}"</span>
        </span>
        {med.locked && <span className="lock" title="Doctor edited — later extraction passes will not overwrite">🔒</span>}
        <button className="btn ghost small" onClick={() => onPatch({ med_key: med.med_key, deleted: true })}>Remove</button>
      </div>

      {med.status === "CONFIRM" && (
        <div className="reason amber-text">
          {flagged ? `⚠ ${med.clinical_reason}` : med.reason}
          <span className="actions">
            <button className="btn small" onClick={() => onPatch({ med_key: med.med_key, confirm: true })}>
              Accept {med.matched}
            </button>
            {med.alternatives.map((a) => (
              <button key={a.brand_id} className="btn small ghost"
                onClick={() => onPatch({ med_key: med.med_key, accept_alternative: a.brand_id })}>
                {a.label}
              </button>
            ))}
            <button className="btn small ghost" onClick={() => setPicking(true)}>Other…</button>
          </span>
        </div>
      )}

      {med.status === "RESOLVE" && !picking && (
        <div className="reason red-text">
          {med.reason || "no credible match"} — blocks approval
          <span className="actions">
            <button className="btn small danger" onClick={() => setPicking(true)}>Resolve</button>
          </span>
        </div>
      )}

      {med.status === "AUTO" && flagged && (
        <div className="reason amber-text">⚠ {med.clinical_reason}</div>
      )}

      {picking && (
        <BrandPicker
          spoken={med.spoken}
          alternatives={med.alternatives}
          onCancel={() => setPicking(false)}
          onPick={(brandId) => { setPicking(false); onPatch({ med_key: med.med_key, brand_id: brandId }); }}
        />
      )}

      <div className="dose">
        <select value={med.frequency ?? ""} onChange={(e) => onPatch({ med_key: med.med_key, frequency: e.target.value || null })}>
          <option value="">frequency</option>
          {FREQ.map((f) => <option key={f} value={f}>{f}</option>)}
          {med.frequency && !FREQ.includes(med.frequency) && <option value={med.frequency}>{med.frequency}</option>}
        </select>
        <select value={med.food_relation ?? ""} onChange={(e) => onPatch({ med_key: med.med_key, food_relation: e.target.value || null })}>
          <option value="">food</option>
          {FOOD.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <input
          className="input small"
          placeholder="duration"
          defaultValue={med.duration ?? ""}
          key={`${med.med_key}-${med.duration ?? ""}`}
          onBlur={(e) => { const v = e.target.value.trim() || null; if (v !== med.duration) onPatch({ med_key: med.med_key, duration: v }); }}
        />
      </div>
    </li>
  );
}
