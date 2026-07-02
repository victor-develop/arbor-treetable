// LoginScreen — a provider-agnostic username/password form mounted by the shell
// when whoami reports a Guest / unauthenticated session. It POSTs to the
// Frappe-native login endpoint (/api/method/login with usr/pwd, the surface the
// default LocalAuthProvider serves) and, on success, calls onAuthenticated so the
// shell re-runs whoami and swaps in the app. On bad creds or a network fault it
// shows an inline error and never signals success.
//
// OSS-clean: this file carries ZERO auth-vendor strings. A private overlay build
// gates authentication with its own bridge and never renders this screen, so the
// public login path stays generic (ARCHITECTURE §10 isolation seam).

import { useState } from "react";

// The login POST is a plain fetch (NOT the api.ts capability client — login is a
// Frappe-native session endpoint, not a whitelisted capability). fetchImpl is
// injectable for tests; it defaults to the global fetch in the browser.
export function LoginScreen({
  onAuthenticated,
  fetchImpl = (...args: Parameters<typeof fetch>) => fetch(...args),
}: {
  // Called after a successful login; the shell re-checks whoami and mounts the app.
  onAuthenticated: () => void;
  fetchImpl?: typeof fetch;
}): JSX.Element {
  const [usr, setUsr] = useState("");
  const [pwd, setPwd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canSubmit = usr.trim() !== "" && pwd !== "" && !busy;

  async function submit(): Promise<void> {
    if (usr.trim() === "" || pwd === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetchImpl("/api/method/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usr: usr.trim(), pwd }),
      });
      if (!res.ok) {
        setError("Incorrect username or password.");
        return;
      }
      onAuthenticated();
    } catch {
      setError("Could not sign in. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="arbor-login" data-testid="login-screen">
      <form
        className="arbor-login-form"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <h1 className="arbor-login-title">Sign in</h1>
        <label className="arbor-field">
          <span className="arbor-field-label">Username</span>
          <input
            data-testid="login-username"
            type="text"
            autoComplete="username"
            value={usr}
            onChange={(e) => setUsr(e.target.value)}
          />
        </label>
        <label className="arbor-field">
          <span className="arbor-field-label">Password</span>
          <input
            data-testid="login-password"
            type="password"
            autoComplete="current-password"
            value={pwd}
            onChange={(e) => setPwd(e.target.value)}
          />
        </label>
        {error && (
          <p className="arbor-login-error" data-testid="login-error" role="alert">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="arbor-login-submit"
          data-testid="login-submit"
          disabled={!canSubmit}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
