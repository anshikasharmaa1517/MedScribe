import { getIdToken, logout } from "./auth";

const BASE = import.meta.env.VITE_API_BASE as string;

export type Medicine = {
  brand_id: string | null; label: string; frequency: string | null;
  food_relation: string | null; duration: string | null; unverified?: boolean;
};
export type Prescription = {
  rxId: string; date: string; doctor: string | null; clinic: string | null;
  diagnosis: string | null; symptoms: string[]; tests_advised: string[]; next_visit: string | null;
  medicines: Medicine[];
  document: { format: "pdf" | "html"; url: string; expires_in: number } | null;
};
export type Reminder = { remId: string; dueAt: string; label: string | null; status: string; takenAt?: string | null };
export type History = {
  patient: { patientId: string; name: string; age?: number; sex?: string; phone: string;
             chronicConditions?: string[]; allergies?: string[] };
  prescriptions: Prescription[];
  reminders: Reminder[];
};
export type Answer = { answer: string; sources: string[]; error?: string };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getIdToken();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) { logout(); window.dispatchEvent(new Event("medscribe:unauthorized")); throw new Error("session expired"); }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).error ?? msg; } catch { /* keep */ }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  history: () => call<History>("GET", "/me/history"),
  ask: (question: string) => call<Answer>("POST", "/me/ask", { question }),
};

export const FREQ_WORDS: Record<string, string> = {
  "1-0-0": "1 tablet morning", "0-1-0": "1 tablet afternoon", "0-0-1": "1 tablet night",
  "1-0-1": "1 tablet morning, 1 tablet night", "1-1-1": "1 tablet morning, afternoon and night",
  "1-1-0": "1 tablet morning, 1 tablet afternoon", "0-1-1": "1 tablet afternoon, 1 tablet night",
  OD: "once a day", BD: "twice a day", TDS: "three times a day", QID: "four times a day",
  HS: "at bedtime", SOS: "only when needed", stat: "once, now",
};

export function timing(m: Medicine): string {
  return [FREQ_WORDS[m.frequency ?? ""] ?? m.frequency, m.food_relation, m.duration ? `for ${m.duration}` : null]
    .filter(Boolean).join(", ");
}
