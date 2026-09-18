// Real client for docs/API_CONTRACT.md. Falls back to the in-browser mock when
// VITE_API_BASE is unset, so the UI can be built before the handlers exist.

import { getIdToken, logout } from "./auth";
import { mockApi } from "./mock/api";
import type { Api, Consult, Draft, DraftPatch, Patient } from "./types";

const BASE = import.meta.env.VITE_API_BASE as string | undefined;
const ENV_TOKEN = import.meta.env.VITE_ID_TOKEN as string | undefined;

export const usingMock = !BASE;

export class UnauthorizedError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = (await getIdToken()) ?? ENV_TOKEN;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    logout();
    window.dispatchEvent(new Event("medscribe:unauthorized"));
    throw new UnauthorizedError("session expired - please sign in again");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).error ?? msg; } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const realApi: Api = {
  listPatients: () => call<Patient[]>("GET", "/patients"),
  createConsult: (patientId) => call<Consult>("POST", "/consults", { patientId }),
  getConsult: (id) => call<Consult>("GET", `/consults/${id}`),
  appendTranscript: (id, line) => call("POST", `/consults/${id}/transcript`, line),
  getDraft: (id) => call<Draft>("GET", `/consults/${id}/draft`),
  patchDraft: (id, patch: DraftPatch) => call<Draft>("PATCH", `/consults/${id}/draft`, patch),
  approve: (id) => call("POST", `/consults/${id}/approve`),
  searchBrands: (q) => call("GET", `/brands?q=${encodeURIComponent(q)}`),
};

export const api: Api = usingMock ? mockApi : realApi;
