// "Request a role" control (Feature: roles) — the user self-application surface
// (applyForRole). A thin shell over executeAction: the picker lists ONLY roles
// the viewer may request (applicable && active && !viewer_holds &&
// !viewer_has_open_application — the server enforces this too), so a
// non-applicable role can never be offered here. Mirrors SubscriptionControl's
// placement as a compact header control available to every user.

import { useState } from "react";
import type { RoleView } from "../api";

export function RequestRoleControl({
  roles,
  onApply,
}: {
  // the full role catalog with per-viewer flags (arbor.list_roles)
  roles: RoleView[];
  onApply: (params: Record<string, unknown>) => void;
}): JSX.Element | null {
  const [role, setRole] = useState("");
  const [justification, setJustification] = useState("");

  // The requestable set: applicable, active, not already held, no open application.
  const requestable = roles.filter(
    (r) => r.applicable && r.active && !r.viewer_holds && !r.viewer_has_open_application,
  );
  const held = roles.filter((r) => r.viewer_holds);

  // Nothing to request AND no held roles to show -> render nothing (quiet).
  if (requestable.length === 0 && held.length === 0) return null;

  return (
    <details className="arbor-role-request" data-testid="request-role-control">
      <summary>
        My roles{held.length > 0 ? <span className="arbor-count">{held.length}</span> : null}
      </summary>
      <div className="arbor-role-request-body">
        {held.length > 0 && (
          <ul className="arbor-role-held" data-testid="my-roles">
            {held.map((r) => (
              <li key={r.role} data-testid={`held-${r.role}`} className="arbor-role-chip">
                {r.label}
              </li>
            ))}
          </ul>
        )}
        {requestable.length > 0 ? (
          <div className="arbor-role-request-form" data-testid="request-role-form">
            <label className="arbor-field">
              <span className="arbor-field-label">Request a role</span>
              <select
                data-testid="request-role-select"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="">Select a role…</option>
                {requestable.map((r) => (
                  <option key={r.role} value={r.role}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="arbor-field">
              <span className="arbor-field-label">Why (optional)</span>
              <input
                data-testid="request-role-justification"
                placeholder="justification"
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
              />
            </label>
            <button
              type="button"
              data-testid="request-role-submit"
              disabled={role === ""}
              onClick={() => {
                if (role === "") return;
                onApply({ role, justification: justification.trim() || undefined });
                setRole("");
                setJustification("");
              }}
            >
              Request
            </button>
          </div>
        ) : (
          <p className="arbor-role-request-none" data-testid="request-role-none">
            No roles available to request.
          </p>
        )}
      </div>
    </details>
  );
}
