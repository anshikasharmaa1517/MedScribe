// Patient pool login (username = phone in E.164). Cognito without an SDK: InitiateAuth is a plain JSON POST. Tokens live
// in localStorage; the ID token is refreshed from the refresh token when it is
// within a minute of expiry, so a consult never dies mid-session.

const REGION = import.meta.env.VITE_COGNITO_REGION ?? "ap-south-1";
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined;
const ENDPOINT = `https://cognito-idp.${REGION}.amazonaws.com/`;
const KEY = "medscribe.patient.session";

export const authConfigured = Boolean(CLIENT_ID);

type Session = { idToken: string; refreshToken: string; email: string; exp: number };

function decodeExp(jwt: string): number {
  try {
    const payload = JSON.parse(atob(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return Number(payload.exp) * 1000;
  } catch { return 0; }
}

function load(): Session | null {
  try { const raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; }
  catch { return null; }
}

function save(s: Session | null) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch { /* private mode: session lives for this page only */ }
}

async function initiateAuth(flow: string, params: Record<string, string>) {
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth" },
    body: JSON.stringify({ AuthFlow: flow, ClientId: CLIENT_ID, AuthParameters: params }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.message ?? data.__type ?? `login failed (${res.status})`);
  if (data.ChallengeName) throw new Error(`account needs ${data.ChallengeName} - ask the admin to set a permanent password`);
  return data.AuthenticationResult as { IdToken: string; RefreshToken?: string };
}

export async function login(username: string, password: string): Promise<void> {
  if (!CLIENT_ID) throw new Error("VITE_COGNITO_CLIENT_ID is not set");
  const r = await initiateAuth("USER_PASSWORD_AUTH", { USERNAME: username, PASSWORD: password });
  save({ idToken: r.IdToken, refreshToken: r.RefreshToken ?? "", email: username, exp: decodeExp(r.IdToken) });
}

export function logout() { save(null); }

export function currentEmail(): string | null { return load()?.email ?? null; }

export async function getIdToken(): Promise<string | null> {
  const s = load();
  if (!s) return null;
  if (Date.now() < s.exp - 60_000) return s.idToken;
  if (!s.refreshToken) { save(null); return null; }
  try {
    const r = await initiateAuth("REFRESH_TOKEN_AUTH", { REFRESH_TOKEN: s.refreshToken });
    save({ ...s, idToken: r.IdToken, exp: decodeExp(r.IdToken) });
    return r.IdToken;
  } catch { save(null); return null; }
}
