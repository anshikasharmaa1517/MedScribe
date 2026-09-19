// Mirrors docs/API_CONTRACT.md. Change the contract first, then this file.

export type Patient = {
  patientId: string;
  name: string;
  age?: number;
  sex?: "M" | "F";
  phone: string;
  lastVisit?: string;
};

export type Speaker = "doctor" | "patient" | null;

export type TranscriptLine = { seq: number; speaker: Speaker; text: string; at: string };

export type Status = "AUTO" | "CONFIRM" | "RESOLVE";
export type Clinical = "OK" | "UNKNOWN" | "CONTRADICTS" | "PENDING_CHECK";

export type Alternative = { brand_id: string; label: string; score: number };

export type Medicine = {
  med_key: string;
  spoken: string;
  brand_id: string | null;
  matched: string | null;
  salt_ids: string[];
  status: Status;
  clinical: Clinical;
  reason: string;
  clinical_reason: string;
  alternatives: Alternative[];
  frequency: string | null;
  food_relation: string | null;
  duration: string | null;
  locked: boolean;
  deleted: boolean;
};

export type TopField = "symptoms" | "diagnosis" | "tests_advised" | "next_visit";

export type Draft = {
  symptoms: string[];
  diagnosis: string | null;
  conditions_matched: string[];
  tests_advised: string[];
  medicines: Medicine[];
  duplicate_salts: [string, string[]][];
  next_visit: string | null;
  locked_fields: TopField[];
  blocks_approval: boolean;
  approval_blocked_by: string[];
  extraction_error?: string;
  updatedAt: string;
};

export type Consult = {
  consultId: string;
  patientId: string;
  doctorId: string;
  status: "LIVE" | "APPROVING" | "APPROVED" | "APPROVAL_FAILED" | "CANCELLED";
  rxId?: string;
  approvalError?: string;
  approvalBlockedBy?: string[];
  createdAt: string;
  transcript: TranscriptLine[];
  draft: Draft;
};

export type BrandHit = { brand_id: string; label: string; salt_ids: string[] };

export type Prescription = {
  rxId: string;
  approvedAt?: string;
  diagnosis: string | null;
  medicines: { brand_id: string | null; label: string; frequency: string | null; food_relation: string | null; duration: string | null }[];
  sentAt?: string | null;
  document: { format: "pdf" | "html"; url: string; expires_in: number } | null;
};

export type Proposal = {
  propId: string; createdAt: string; status: string; source?: string;
  spoken?: string; proposed_brand?: string; proposed_salts?: string[];
  evidence_url?: string; confidence?: number; note?: string;
};

export type MedicinePatch =
  | { med_key: string; frequency?: string | null; food_relation?: string | null; duration?: string | null }
  | { med_key: string; brand_id: string }
  | { med_key: string; accept_alternative: string }
  | { med_key: string; confirm: true }
  | { med_key: string; deleted: true }
  | { add: { brand_id: string; frequency?: string | null; food_relation?: string | null; duration?: string | null } };

export type DraftPatch =
  | { field: "diagnosis" | "next_visit"; value: string | null }
  | { field: "symptoms" | "tests_advised"; value: string[] }
  | { medicine: MedicinePatch };

export interface Api {
  listPatients(): Promise<Patient[]>;
  createConsult(patientId: string): Promise<Consult>;
  getConsult(id: string): Promise<Consult>;
  appendTranscript(id: string, line: { text: string; speaker: Speaker; seq: number }): Promise<void>;
  getDraft(id: string): Promise<Draft>;
  patchDraft(id: string, patch: DraftPatch): Promise<Draft>;
  approve(id: string): Promise<{ status: string; rxId: string }>;
  getPrescription(id: string): Promise<Prescription>;
  listPending(): Promise<Proposal[]>;
  review(createdAt: string, propId: string, decision: "APPROVED" | "REJECTED"): Promise<{ status: string }>;
  searchBrands(q: string): Promise<BrandHit[]>;
}
