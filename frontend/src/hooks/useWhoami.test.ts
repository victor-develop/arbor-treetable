// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// useWhoami — the auth-gate + impersonation-banner signal. It wraps
// client.whoami() into {user, real_user, impersonating, authenticated, loading}
// plus a refetch(). The shell renders <LoginScreen/> while !authenticated and
// the app otherwise; ImpersonationBar reads the same signal. The hook re-derives
// NOTHING — it mirrors the server envelope verbatim.

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ArborClient, Whoami } from "../api";
import { useWhoami } from "./useWhoami";

function makeClient(whoami: ArborClient["whoami"]): ArborClient {
  return {
    executeAction: vi.fn(),
    getSheetSnapshot: vi.fn(),
    agentChat: vi.fn(),
    whoami,
  } as unknown as ArborClient;
}

describe("useWhoami", () => {
  it("starts loading, then resolves an authenticated real user", async () => {
    const whoami = vi.fn<() => Promise<Whoami>>().mockResolvedValue({
      user: "alice@example.com",
      real_user: null,
      impersonating: false,
      authenticated: true,
    });
    const { result } = renderHook(() => useWhoami(makeClient(whoami)));

    // Initial synchronous render: loading, not yet authenticated.
    expect(result.current.loading).toBe(true);
    expect(result.current.authenticated).toBe(false);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.authenticated).toBe(true);
    expect(result.current.user).toBe("alice@example.com");
    expect(result.current.impersonating).toBe(false);
    // whoami is invoked at least once on mount (StrictMode may double-invoke the
    // mount effect; the reqId guard makes the latest response authoritative).
    expect(whoami).toHaveBeenCalled();
  });

  it("surfaces impersonation (real_user present + impersonating true)", async () => {
    const whoami = vi.fn<() => Promise<Whoami>>().mockResolvedValue({
      user: "owner@example.com",
      real_user: "admin@example.com",
      impersonating: true,
      authenticated: true,
    });
    const { result } = renderHook(() => useWhoami(makeClient(whoami)));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toBe("owner@example.com");
    expect(result.current.real_user).toBe("admin@example.com");
    expect(result.current.impersonating).toBe(true);
  });

  it("reports unauthenticated (Guest) so the shell shows the login gate", async () => {
    const whoami = vi.fn<() => Promise<Whoami>>().mockResolvedValue({
      user: "Guest",
      authenticated: false,
    });
    const { result } = renderHook(() => useWhoami(makeClient(whoami)));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.authenticated).toBe(false);
  });

  it("treats a rejected whoami as unauthenticated (fail-closed) and clears loading", async () => {
    const whoami = vi.fn<() => Promise<Whoami>>().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useWhoami(makeClient(whoami)));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.authenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("refetch() re-runs whoami (e.g. after login / begin / end impersonation)", async () => {
    // The server's answer changes over time (Guest → signed in); the mock returns
    // the CURRENT answer each call, so StrictMode's double mount-effect can't
    // desync the sequence the way mockResolvedValueOnce chaining would.
    let current: Whoami = { user: "Guest", authenticated: false };
    const whoami = vi.fn<() => Promise<Whoami>>().mockImplementation(async () => current);
    // Build the client ONCE so its identity is stable across renders (a fresh
    // client per render would re-fire the mount effect on every state update).
    const client = makeClient(whoami);
    const { result } = renderHook(() => useWhoami(client));

    // Initial resolve → Guest / unauthenticated.
    await waitFor(() => expect(result.current.authenticated).toBe(false));

    // The server now recognizes a signed-in user; refetch picks it up.
    current = { user: "alice@example.com", impersonating: false, authenticated: true };
    const before = whoami.mock.calls.length;
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(result.current.authenticated).toBe(true));
    expect(result.current.user).toBe("alice@example.com");
    expect(whoami.mock.calls.length).toBeGreaterThan(before);
  });

  it("stays fail-closed when the client has no whoami method", async () => {
    const client = {
      executeAction: vi.fn(),
      getSheetSnapshot: vi.fn(),
      agentChat: vi.fn(),
    } as unknown as ArborClient;
    const { result } = renderHook(() => useWhoami(client));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.authenticated).toBe(false);
  });
});
