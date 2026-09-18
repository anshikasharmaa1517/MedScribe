import { useState } from "react";
import { login } from "../auth";

type Props = { onLoggedIn: () => void };

export function Login({ onLoggedIn }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(email.trim(), password); onLoggedIn(); }
    catch (err) { setError(String((err as Error).message ?? err)); }
    finally { setBusy(false); }
  };

  return (
    <main className="page login">
      <form className="panel login-card" onSubmit={submit}>
        <h1>MedScribe</h1>
        <p className="muted">Doctor sign in</p>
        <label className="field">
          <span className="field-label">Email</span>
          <input className="input" type="email" autoComplete="username" autoFocus required
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input className="input" type="password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="btn primary large" type="submit" disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
