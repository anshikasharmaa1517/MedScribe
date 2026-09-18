import { useState } from "react";
import type { Draft, DraftPatch, TopField } from "../types";
import { BrandPicker } from "./BrandPicker";
import { MedicineRow } from "./MedicineRow";

type Props = {
  draft: Draft | null;
  onPatch: (p: DraftPatch) => void;
  onApprove: () => void;
  approving: boolean;
  approved: boolean;
};

function LockedMark({ draft, field }: { draft: Draft; field: TopField }) {
  return draft.locked_fields.includes(field) ? <span className="lock" title="Doctor edited">🔒</span> : null;
}

function TextField({ draft, field, label, onPatch }: { draft: Draft; field: "diagnosis" | "next_visit"; label: string; onPatch: (p: DraftPatch) => void }) {
  const value = draft[field] ?? "";
  return (
    <label className="field">
      <span className="field-label">{label} <LockedMark draft={draft} field={field} /></span>
      <input
        className="input"
        key={`${field}-${value}`}
        defaultValue={value}
        placeholder="—"
        onBlur={(e) => { const v = e.target.value.trim() || null; if (v !== (draft[field] ?? null)) onPatch({ field, value: v }); }}
      />
    </label>
  );
}

function ListField({ draft, field, label, onPatch }: { draft: Draft; field: "symptoms" | "tests_advised"; label: string; onPatch: (p: DraftPatch) => void }) {
  const value = draft[field].join(", ");
  return (
    <label className="field">
      <span className="field-label">{label} <LockedMark draft={draft} field={field} /></span>
      <input
        className="input"
        key={`${field}-${value}`}
        defaultValue={value}
        placeholder="—"
        onBlur={(e) => {
          const items = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
          if (items.join(", ") !== value) onPatch({ field, value: items });
        }}
      />
    </label>
  );
}

export function DraftPanel({ draft, onPatch, onApprove, approving, approved }: Props) {
  const [adding, setAdding] = useState(false);
  if (!draft) return <section className="panel draft"><p className="muted">Waiting for first extraction…</p></section>;

  const active = draft.medicines.filter((m) => !m.deleted);
  const unresolved = active.filter((m) => m.status === "RESOLVE").length;
  const toConfirm = active.filter((m) => m.status === "CONFIRM").length;

  return (
    <section className="panel draft">
      <header className="panel-head">
        <h2>Draft prescription</h2>
        {draft.extraction_error && <span className="pill warn" title={draft.extraction_error}>extraction paused</span>}
      </header>

      <ListField draft={draft} field="symptoms" label="Symptoms" onPatch={onPatch} />
      <TextField draft={draft} field="diagnosis" label="Diagnosis" onPatch={onPatch} />
      {draft.conditions_matched.length > 0 && (
        <p className="muted small-text">Matched: {draft.conditions_matched.join(", ")}</p>
      )}
      <ListField draft={draft} field="tests_advised" label="Tests advised" onPatch={onPatch} />

      <div className="field-label meds-label">
        Medicines
        <button className="btn ghost small" onClick={() => setAdding(true)}>+ Add</button>
      </div>
      {adding && (
        <BrandPicker spoken="" alternatives={[]} onCancel={() => setAdding(false)}
          onPick={(brandId) => { setAdding(false); onPatch({ medicine: { add: { brand_id: brandId } } }); }} />
      )}
      {active.length === 0 && !adding && <p className="muted">No medicines yet.</p>}
      <ul className="meds">
        {draft.medicines.map((m) => (
          <MedicineRow key={m.med_key} med={m} onPatch={(p) => onPatch({ medicine: p })} />
        ))}
      </ul>

      {draft.duplicate_salts.length > 0 && (
        <div className="notice amber-bg">
          {draft.duplicate_salts.map(([salt, brands]) => (
            <div key={salt}>⚠ Same salt twice — <strong>{salt}</strong>: {brands.join(", ")}</div>
          ))}
        </div>
      )}

      <TextField draft={draft} field="next_visit" label="Next visit" onPatch={onPatch} />

      <footer className="approve-bar">
        <span className="muted">
          {unresolved > 0 ? `${unresolved} unresolved — resolve to approve`
            : toConfirm > 0 ? `${toConfirm} to confirm (optional)` : "Ready"}
        </span>
        <button
          className="btn primary large"
          disabled={draft.blocks_approval || approving || approved || active.length === 0}
          onClick={onApprove}
        >
          {approved ? "Approved ✓" : approving ? "Approving…" : "Approve & send"}
        </button>
      </footer>
    </section>
  );
}
