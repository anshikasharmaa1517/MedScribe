// In-browser stand-in for the step-7 API. Same contract, no network.
// A keyword "extractor" turns transcript lines into a draft so every UI state
// (AUTO / CONFIRM / RESOLVE / CONTRADICTS / duplicate salt) can be exercised
// without Bedrock. Merge rules mirror core/pipeline.py: locked and deleted win.

import brandsJson from "./brands.json";
import type {
  Api, BrandHit, Consult, Draft, Medicine, Patient, Prescription, Speaker, TranscriptLine,
} from "../types";

const brands = brandsJson as BrandHit[];
const byId = new Map(brands.map((b) => [b.brand_id, b]));

const patients: Patient[] = [
  { patientId: "pat-demo-001", name: "Ramesh Iyer", age: 54, sex: "M", phone: "+919800000001", lastVisit: "2026-08-20" },
  { patientId: "pat-demo-002", name: "Priya Sharma", age: 29, sex: "F", phone: "+919800000002", lastVisit: "2026-09-05" },
  { patientId: "pat-demo-003", name: "Arjun Mehta", age: 35, sex: "M", phone: "+919800000003", lastVisit: "2026-08-29" },
];

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function emptyDraft(): Draft {
  return {
    symptoms: [], diagnosis: null, conditions_matched: [], tests_advised: [], medicines: [],
    duplicate_salts: [], next_visit: null, locked_fields: [], blocks_approval: false,
    approval_blocked_by: [], updatedAt: now(),
  };
}

const base = (spoken: string, over: Partial<Medicine>): Medicine => ({
  med_key: spoken.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(),
  spoken, brand_id: null, matched: null, salt_ids: [], status: "RESOLVE",
  clinical: "PENDING_CHECK", reason: "", clinical_reason: "no diagnosis stated yet",
  alternatives: [], frequency: null, food_relation: null, duration: null,
  locked: false, deleted: false, ...over,
});

const resolved = (spoken: string, brandId: string, reason: string, over: Partial<Medicine> = {}) => {
  const b = byId.get(brandId)!;
  return base(spoken, { med_key: brandId, brand_id: brandId, matched: b.label, salt_ids: b.salt_ids,
    status: "AUTO", reason, ...over });
};

// keyword -> extraction fragment
type Rule = { test: RegExp; apply: (d: Draft, line: string) => void };
const RULES: Rule[] = [
  { test: /bukhar|fever/i, apply: (d) => add(d.symptoms, "fever") },
  { test: /body pain/i, apply: (d) => add(d.symptoms, "body pain") },
  { test: /pet (dard|kharab)|stomach/i, apply: (d) => add(d.symptoms, "stomach pain") },
  { test: /viral fever/i, apply: (d) => { d.diagnosis = "Viral fever"; } },
  { test: /sugar|diabetes/i, apply: (d) => { d.diagnosis = "Type 2 diabetes"; } },
  { test: /\bcbc\b/i, apply: (d) => add(d.tests_advised, "CBC") },
  { test: /dolo 650/i, apply: (d, l) => push(d, resolved("Dolo 650", "B001", "exact alias", dose(l))) },
  { test: /crocin 650/i, apply: (d, l) => push(d, resolved("Crocin 650", "B006", "exact alias", dose(l))) },
  { test: /cetzine 10/i, apply: (d, l) => push(d, resolved("Cetzine 10", "B025", "exact alias", dose(l))) },
  { test: /metrogyl 400/i, apply: (d, l) => push(d, resolved("Metrogyl 400", "B073", "exact alias", dose(l))) },
  { test: /azithro\b/i, apply: (d, l) => push(d, resolved("azithro", "B058", "score 86, only 8 pts from Azithral", {
      status: "CONFIRM", med_key: "B058",
      alternatives: [{ brand_id: "B059", label: byId.get("B059")?.label ?? "Zithrox 500mg", score: 78 },
                     { brand_id: "B060", label: byId.get("B060")?.label ?? "Azee 500mg", score: 74 }],
      ...dose(l) })) },
  { test: /zorblaxitron/i, apply: (d, l) => push(d, base("zorblaxitron 900", { reason: "no credible match",
      alternatives: [{ brand_id: "B016", label: "Zerodol 100mg", score: 58 }], ...dose(l) })) },
  { test: /paanch din baad|5 days later|after 5 days/i, apply: (d) => { d.next_visit = "5 days"; } },
  { test: /ek hafte baad|1 week/i, apply: (d) => { d.next_visit = "1 week"; } },
];

function dose(line: string): Partial<Medicine> {
  const out: Partial<Medicine> = {};
  if (/ek subah ek shaam|subah shaam/i.test(line)) out.frequency = "1-0-1";
  else if (/teen baar|1-1-1/i.test(line)) out.frequency = "1-1-1";
  else if (/raat ko|sote samay/i.test(line)) out.frequency = "0-0-1";
  else if (/\bod\b|ek baar/i.test(line)) out.frequency = "OD";
  else if (/\btds\b/i.test(line)) out.frequency = "TDS";
  if (/khaane ke baad|after food/i.test(line)) out.food_relation = "after food";
  if (/khaane se pehle|before food/i.test(line)) out.food_relation = "before food";
  const m = line.match(/(paanch|teen|saat|das|\d+)\s*din/i);
  if (m) out.duration = `${({ paanch: 5, teen: 3, saat: 7, das: 10 } as Record<string, number>)[m[1].toLowerCase()] ?? m[1]} days`;
  return out;
}
const add = (arr: string[], v: string) => { if (!arr.includes(v)) arr.push(v); };
const push = (d: Draft, m: Medicine) => { if (!d.medicines.some((x) => x.med_key === m.med_key)) d.medicines.push(m); };

function validate(d: Draft): Draft {
  const dx = (d.diagnosis ?? "").toLowerCase();
  const conditions = dx.includes("viral") ? ["Viral fever"] : dx.includes("diabetes") ? ["Type 2 diabetes"] : [];
  d.conditions_matched = conditions;
  for (const m of d.medicines) {
    if (m.deleted) continue;
    if (!conditions.length) { m.clinical = "PENDING_CHECK"; m.clinical_reason = "no diagnosis stated yet"; continue; }
    if (!m.salt_ids.length) { m.clinical = "UNKNOWN"; m.clinical_reason = "no salt mapping"; continue; }
    const contradicts = conditions.includes("Type 2 diabetes") && m.salt_ids.includes("S050") ||
                        conditions.includes("Viral fever") && m.brand_id === "B073";
    m.clinical = contradicts ? "CONTRADICTS" : "OK";
    m.clinical_reason = contradicts ? `not expected for ${conditions[0]}` : "";
    if (contradicts && m.status === "AUTO" && !m.locked) m.status = "CONFIRM";
  }
  const bySalt = new Map<string, string[]>();
  for (const m of d.medicines) if (!m.deleted && m.matched) for (const s of m.salt_ids) bySalt.set(s, [...(bySalt.get(s) ?? []), m.matched]);
  d.duplicate_salts = [...bySalt.entries()].filter(([, v]) => v.length > 1).map(([s, v]) => [s === "S001" ? "Paracetamol" : s, v]);
  d.approval_blocked_by = d.medicines.filter((m) => !m.deleted && m.status === "RESOLVE").map((m) => m.med_key);
  d.blocks_approval = d.approval_blocked_by.length > 0;
  d.updatedAt = now();
  return d;
}

function extract(transcript: TranscriptLine[], prior: Draft): Draft {
  const fresh = emptyDraft();
  for (const line of transcript) for (const r of RULES) if (r.test.test(line.text)) r.apply(fresh, line.text);
  const next: Draft = { ...fresh, locked_fields: prior.locked_fields };
  for (const f of prior.locked_fields) (next as unknown as Record<string, unknown>)[f] = prior[f];
  const freshByKey = new Map(fresh.medicines.map((m) => [m.med_key, m]));
  const merged: Medicine[] = [];
  for (const pm of prior.medicines) merged.push(pm.locked || pm.deleted || !freshByKey.has(pm.med_key) ? { ...pm } : freshByKey.get(pm.med_key)!);
  for (const m of fresh.medicines) if (!merged.some((x) => x.med_key === m.med_key)) merged.push(m);
  next.medicines = merged;
  return validate(next);
}

const consults = new Map<string, Consult>();
const timers = new Map<string, number>();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const mockApi: Api = {
  async listPatients() { await sleep(150); return patients; },

  async createConsult(patientId) {
    const c: Consult = { consultId: `c-${Date.now().toString(36)}`, patientId, doctorId: "doc-demo-meera",
      status: "LIVE", createdAt: now(), transcript: [], draft: emptyDraft() };
    consults.set(c.consultId, c);
    return c;
  },

  async getConsult(id) { const c = consults.get(id); if (!c) throw new Error("consult not found"); return c; },

  async appendTranscript(id, line) {
    const c = await this.getConsult(id);
    c.transcript.push({ ...line, at: now() });
    // debounce like the server: extract 1.5 s after the last line
    clearTimeout(timers.get(id));
    timers.set(id, window.setTimeout(() => { c.draft = extract(c.transcript, c.draft); }, 1500));
  },

  async getDraft(id) { return structuredClone((await this.getConsult(id)).draft); },

  async patchDraft(id, patch) {
    const c = await this.getConsult(id);
    const d = c.draft;
    if ("field" in patch) {
      (d as unknown as Record<string, unknown>)[patch.field] = patch.value;
      if (!d.locked_fields.includes(patch.field)) d.locked_fields.push(patch.field);
    } else {
      const p = patch.medicine;
      if ("add" in p) {
        const b = byId.get(p.add.brand_id);
        if (b) d.medicines.push(resolved(b.label, b.brand_id, "added by doctor", { locked: true,
          frequency: p.add.frequency ?? null, food_relation: p.add.food_relation ?? null, duration: p.add.duration ?? null }));
      } else {
        const m = d.medicines.find((x) => x.med_key === p.med_key);
        if (!m) throw new Error("medicine not found");
        m.locked = true;
        if ("deleted" in p) m.deleted = true;
        else if ("confirm" in p) m.status = "AUTO";
        else if ("brand_id" in p || "accept_alternative" in p) {
          const b = byId.get("brand_id" in p ? p.brand_id : p.accept_alternative);
          if (b) { m.brand_id = b.brand_id; m.matched = b.label; m.salt_ids = b.salt_ids; m.status = "AUTO"; m.reason = "set by doctor"; }
        } else {
          if ("frequency" in p) m.frequency = p.frequency ?? null;
          if ("food_relation" in p) m.food_relation = p.food_relation ?? null;
          if ("duration" in p) m.duration = p.duration ?? null;
        }
      }
    }
    c.draft = validate(d);
    return structuredClone(c.draft);
  },

  async approve(id) {
    const c = await this.getConsult(id);
    if (c.draft.blocks_approval) throw new Error("409: unresolved medicines block approval");
    c.status = "APPROVED";
    c.rxId = `rx-${c.consultId}`;
    return { status: "APPROVED", rxId: c.rxId };
  },

  async getPrescription(id) {
    const c = await this.getConsult(id);
    if (c.status !== "APPROVED" || !c.rxId) throw new Error("consult has no approved prescription yet");
    const meds = c.draft.medicines.filter((x) => !x.deleted);
    const rx: Prescription = {
      rxId: c.rxId, approvedAt: now(), diagnosis: c.draft.diagnosis,
      medicines: meds.map((x) => ({ brand_id: x.brand_id, label: x.matched ?? x.spoken, frequency: x.frequency, food_relation: x.food_relation, duration: x.duration })),
      sentAt: now(), document: { format: "html", url: "data:text/html," + encodeURIComponent("<h1>Mock prescription " + c.rxId + "</h1>"), expires_in: 3600 },
    };
    return rx;
  },

  async searchBrands(q) {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    return brands.filter((b) => b.label.toLowerCase().includes(s)).slice(0, 20);
  },
};

// Scripted "microphone" for the demo: streams a consultation line by line.
export const DEMO_SCRIPT: { speaker: Speaker; text: string }[] = [
  { speaker: "doctor", text: "Namaste, kya problem hai?" },
  { speaker: "patient", text: "Sir, do din se bukhar hai aur body pain bhi hai." },
  { speaker: "doctor", text: "Temperature check karte hain... 101 hai. Viral fever lag raha hai." },
  { speaker: "doctor", text: "CBC karwa lena." },
  { speaker: "doctor", text: "Dolo 650 le lena, ek subah ek shaam, khaane ke baad, paanch din." },
  { speaker: "doctor", text: "Aur Cetzine 10 raat ko." },
  { speaker: "doctor", text: "Azithro bhi de deta hoon, ek baar, teen din." },
  { speaker: "doctor", text: "Aur zorblaxitron 900 lena raat ko." },
  { speaker: "doctor", text: "Paanch din baad dikha dena." },
];
