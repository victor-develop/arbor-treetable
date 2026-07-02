// ImpersonationBar (Feature: act-as) — the admin "act as…" control + the
// persistent "acting as …" banner. It drives ENTIRELY off whoami / snapshot
// viewer hints (isAdmin, impersonating, effectiveUser, realUser) supplied by the
// host; it NEVER re-derives auth. Three states:
//   * not impersonating & not admin → render nothing (quiet for ordinary users)
//   * not impersonating & admin     → an "Act as…" picker → onBegin(user, reason?)
//   * impersonating                 → a high-contrast banner naming the effective
//                                     + real identity with a Stop → onStop()
// The banner shows regardless of the (impersonated) admin flag so Stop is always
// reachable even when acting as a non-admin. Callbacks own the client call + the
// whoami/snapshot refetch; this component owns only the affordance + local input.
//
// OSS-clean: zero auth-vendor strings — generic username/password impersonation.

import { useState } from "react";

export function ImpersonationBar({
  isAdmin,
  impersonating,
  effectiveUser,
  realUser,
  onBegin,
  onStop,
}: {
  // The REAL viewer's admin hint (snapshot.viewer.is_admin / whoami). While
  // impersonating this reflects the IMPERSONATED user and is not used to gate Stop.
  isAdmin: boolean;
  impersonating: boolean;
  // The identity ACL runs against (== snapshot.actor / whoami.user).
  effectiveUser: string | null;
  // The truly-authenticated admin, present only under impersonation.
  realUser: string | null;
  onBegin: (user: string, reason?: string) => void;
  onStop: () => void;
}): JSX.Element | null {
  const [user, setUser] = useState("");
  const [reason, setReason] = useState("");

  // Impersonating: the banner takes priority over everything (always reachable Stop).
  if (impersonating) {
    return (
      <div className="arbor-impersonation" data-testid="impersonation-bar" data-impersonating="true">
        <div
          className="arbor-impersonation-banner"
          data-testid="impersonation-banner"
          role="status"
        >
          <span className="arbor-impersonation-msg">
            Acting as <strong>{effectiveUser}</strong>
            {realUser ? (
              <>
                {" "}
                (as <strong>{realUser}</strong>)
              </>
            ) : null}
          </span>
          <button
            type="button"
            className="arbor-impersonation-stop"
            data-testid="impersonation-stop"
            onClick={() => onStop()}
          >
            Stop
          </button>
        </div>
      </div>
    );
  }

  // Not impersonating and not admin → nothing to show.
  if (!isAdmin) return null;

  // Admin, not impersonating → the "Act as…" picker.
  return (
    <div className="arbor-impersonation" data-testid="impersonation-bar" data-impersonating="false">
      <div className="arbor-impersonation-picker" data-testid="impersonation-picker">
        <label className="arbor-field">
          <span className="arbor-field-label">Act as…</span>
          <input
            data-testid="impersonation-user"
            type="text"
            placeholder="user@example.com"
            value={user}
            onChange={(e) => setUser(e.target.value)}
          />
        </label>
        <label className="arbor-field">
          <span className="arbor-field-label">Reason (optional)</span>
          <input
            data-testid="impersonation-reason"
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="arbor-impersonation-begin"
          data-testid="impersonation-begin"
          disabled={user.trim() === ""}
          onClick={() => {
            if (user.trim() === "") return;
            onBegin(user.trim(), reason.trim() || undefined);
            setUser("");
            setReason("");
          }}
        >
          Act as
        </button>
      </div>
    </div>
  );
}
