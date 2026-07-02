// useWhoami — the ONE auth signal for the shell. Wraps client.whoami() into a
// small reactive shape: the EFFECTIVE user, the real_user (present only under
// impersonation), the impersonating flag, an authenticated gate, and loading.
// The shell renders <LoginScreen/> while !authenticated and the app otherwise;
// ImpersonationBar drives its banner off impersonating/real_user. This hook
// re-derives NOTHING — it mirrors the server envelope verbatim and, on any error
// or a client without whoami, fails CLOSED (authenticated=false → login gate).

import { useCallback, useEffect, useRef, useState } from "react";
import type { ArborClient } from "../api";

export type WhoamiState = {
  // The effective identity ACL runs against (the impersonated user while acting
  // as, else the real session user). null until the first whoami resolves.
  user: string | null;
  // The truly-authenticated principal (present + != user only under impersonation).
  real_user: string | null;
  impersonating: boolean;
  // false for a Guest / unresolved / failed whoami — drives the login gate.
  authenticated: boolean;
  loading: boolean;
  // Re-run whoami (after login, begin/end impersonation, or a manual refresh).
  refetch: () => Promise<void>;
};

export function useWhoami(client: ArborClient): WhoamiState {
  const [user, setUser] = useState<string | null>(null);
  const [realUser, setRealUser] = useState<string | null>(null);
  const [impersonating, setImpersonating] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  // Guards against a stale in-flight whoami resolving after a newer one.
  const reqId = useRef(0);
  // Hold the client behind a ref so `refetch` keeps a STABLE identity even when
  // the caller passes a fresh client object per render — otherwise the mount
  // effect (which depends on refetch) would re-fire on every state update.
  const clientRef = useRef(client);
  clientRef.current = client;

  const refetch = useCallback(async () => {
    const id = ++reqId.current;
    setLoading(true);
    try {
      const c = clientRef.current;
      if (!c.whoami) {
        // No whoami surface → fail closed (never assume an identity).
        if (id === reqId.current) {
          setUser(null);
          setRealUser(null);
          setImpersonating(false);
          setAuthenticated(false);
        }
        return;
      }
      const w = await c.whoami();
      if (id !== reqId.current) return; // superseded
      setUser(w.user ?? null);
      setRealUser(w.real_user ?? null);
      setImpersonating(Boolean(w.impersonating));
      setAuthenticated(Boolean(w.authenticated));
    } catch {
      // Fail closed: a whoami error is treated as "not authenticated" so the
      // shell falls back to the login gate rather than leaking a stale identity.
      if (id !== reqId.current) return;
      setUser(null);
      setRealUser(null);
      setImpersonating(false);
      setAuthenticated(false);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
    // Stable identity: reads the client through clientRef, so it never needs to
    // be recreated when the caller passes a new client object per render.
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { user, real_user: realUser, impersonating, authenticated, loading, refetch };
}
