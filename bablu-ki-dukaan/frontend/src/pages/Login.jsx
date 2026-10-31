import { useState } from "react";
import { api, session } from "../api";

export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const r = await api.post("/auth/login/", { username, password });
      session.set(r.token);
      onLogin({ username: r.username, is_staff: r.is_staff });
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <form className="card login" onSubmit={submit}>
      <div className="login-emoji">🔐</div>
      <h2>Dukaan ka darwaza</h2>
      <p className="hint">Log in to open Bablu ki dukaan.</p>
      <label>Username
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoFocus />
      </label>
      <label>Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      <button className="btn btn-tomato" type="submit" disabled={busy}>{busy ? "Checking…" : "Open the shop"}</button>
      {err && <p className="note bad">{err}</p>}
    </form>
  );
}
