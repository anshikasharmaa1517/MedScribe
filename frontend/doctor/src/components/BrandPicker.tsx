import { useEffect, useState } from "react";
import { api } from "../api";
import type { Alternative, BrandHit } from "../types";

type Props = {
  spoken: string;
  alternatives: Alternative[];
  onPick: (brandId: string) => void;
  onCancel: () => void;
};

export function BrandPicker({ spoken, alternatives, onPick, onCancel }: Props) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<BrandHit[]>([]);

  useEffect(() => {
    if (!q.trim()) return;
    let live = true;
    api.searchBrands(q).then((h) => { if (live) setHits(h); });
    return () => { live = false; };
  }, [q]);

  const onChange = (value: string) => {
    setQ(value);
    if (!value.trim()) setHits([]);
  };

  return (
    <div className="picker">
      <div className="picker-head">
        <span>Heard <em>"{spoken}"</em> — choose the medicine</span>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
      {alternatives.length > 0 && (
        <div className="chips">
          {alternatives.map((a) => (
            <button key={a.brand_id} className="chip" onClick={() => onPick(a.brand_id)}>
              {a.label} <span className="muted">{Math.round(a.score)}</span>
            </button>
          ))}
        </div>
      )}
      <input
        autoFocus
        className="input"
        placeholder="Search brand…"
        value={q}
        onChange={(e) => onChange(e.target.value)}
      />
      {hits.length > 0 && (
        <ul className="hits">
          {hits.map((h) => (
            <li key={h.brand_id}>
              <button className="hit" onClick={() => onPick(h.brand_id)}>{h.label}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
