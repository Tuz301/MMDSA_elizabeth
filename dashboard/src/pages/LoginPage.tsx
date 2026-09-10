/**
 * Sign in.
 *
 * Cognito mode is the pilot: username and password over SRP, with the forced
 * first-password change surfaced inline rather than as a dead-end error.
 * Token mode is development only: a bearer token pasted from a local tool.
 */

import { useState, type FormEvent } from "react";

import { AUTH_MODE, useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { signInWithToken, signInWithCognito } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [needsNewPassword, setNeedsNewPassword] = useState(false);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);

    if (AUTH_MODE === "token") {
      if (token.trim()) signInWithToken(token);
      else setMessage("Paste an access token to continue.");
      return;
    }

    setBusy(true);
    try {
      const outcome = await signInWithCognito(
        username,
        password,
        needsNewPassword ? newPassword : undefined,
      );
      if (outcome.kind === "new-password-required") {
        setNeedsNewPassword(true);
        setMessage("First sign-in: choose a new password to replace the temporary one.");
      } else if (outcome.kind === "failure") {
        setMessage(outcome.message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>MMDSA Supervision</h1>
        <p className="subtitle">Sign in with your programme account.</p>
        {message && <div className="banner-error">{message}</div>}

        {AUTH_MODE === "token" ? (
          <div className="field">
            <label htmlFor="token">Access token (development mode)</label>
            <textarea
              id="token"
              rows={4}
              style={{ width: "100%" }}
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </div>
        ) : (
          <>
            <div className="field">
              <label htmlFor="username">Username</label>
              <input
                id="username"
                autoComplete="username"
                style={{ width: "100%" }}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="password">
                {needsNewPassword ? "Temporary password" : "Password"}
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                style={{ width: "100%" }}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {needsNewPassword && (
              <div className="field">
                <label htmlFor="new-password">New password (12 characters or more)</label>
                <input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  style={{ width: "100%" }}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                />
              </div>
            )}
          </>
        )}

        <button disabled={busy} style={{ width: "100%" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
