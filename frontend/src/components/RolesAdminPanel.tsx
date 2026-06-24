// Roles governance tab body (Feature: roles). Parallel to DelegationControl:
//   * ADMIN (snapshot.viewer.is_admin): assign a role to a user, revoke active
//     grants, and approve/reject pending applications.
//   * NON-ADMIN: read-only view of their own applications, with withdraw on the
//     still-open (proposed) ones.
// A thin shell over executeAction — it re-derives no authority (assign/revoke/
// approve/reject are gated server-side on admin; can_revoke/viewer_is_approver
// are server-supplied hints). No raw writes.

import { useState } from "react";
import type { RoleApplicationView, RoleGrantView, RoleView } from "../api";

const STATUS_LABEL: Record<string, string> = {
  proposed: "pending",
  approved: "approved",
  rejected: "rejected",
  withdrawn: "withdrawn",
};

export function RolesAdminPanel({
  isAdmin,
  roles,
  grants,
  applications,
  onAssign,
  onRevoke,
  onApprove,
  onReject,
  onWithdraw,
}: {
  isAdmin: boolean;
  roles: RoleView[];
  grants: RoleGrantView[];
  applications: RoleApplicationView[];
  onAssign: (params: Record<string, unknown>) => void;
  onRevoke: (params: Record<string, unknown>) => void;
  onApprove: (params: Record<string, unknown>) => void;
  onReject: (params: Record<string, unknown>) => void;
  onWithdraw: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [role, setRole] = useState("");
  const [grantee, setGrantee] = useState("");

  const assignable = roles.filter((r) => r.active);
  const pending = applications.filter((a) => a.status === "proposed");
  const canAssign = role !== "" && grantee.trim() !== "";
  const roleLabel = (key: string) => roles.find((r) => r.role === key)?.label ?? key;

  if (!isAdmin) {
    // Non-admin: their own applications (read-only + withdraw on open ones).
    return (
      <section className="arbor-roles" data-testid="roles-panel" data-admin="false">
        <h2>My role applications</h2>
        {applications.length === 0 ? (
          <p data-testid="roles-apps-empty">No role applications.</p>
        ) : (
          <ul className="arbor-role-apps" data-testid="role-applications">
            {applications.map((a) => (
              <li key={a.name} className="arbor-role-app" data-testid={`role-app-${a.name}`}>
                <span className="arbor-role-app-subject">
                  {roleLabel(a.role)}{" "}
                  <span className={`arbor-role-status is-${a.status}`}>
                    {STATUS_LABEL[a.status] ?? a.status}
                  </span>
                </span>
                {a.status === "proposed" && (
                  <button
                    type="button"
                    data-testid={`role-app-withdraw-${a.name}`}
                    onClick={() => onWithdraw({ role_application: a.name })}
                  >
                    Withdraw
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  return (
    <section className="arbor-roles" data-testid="roles-panel" data-admin="true">
      {/* Pending applications inbox */}
      <h2>
        Role applications <span className="arbor-count">{pending.length}</span>
      </h2>
      {pending.length === 0 ? (
        <p data-testid="role-apps-empty">No pending applications.</p>
      ) : (
        <ul className="arbor-role-apps" data-testid="role-applications">
          {pending.map((a) => (
            <li key={a.name} className="arbor-role-app" data-testid={`role-app-${a.name}`}>
              <span className="arbor-role-app-subject">
                <span className="arbor-role-app-who">{a.requester}</span>
                <span className="arbor-grant-arrow"> → </span>
                <span className="arbor-role-app-role">{roleLabel(a.role)}</span>
                {a.justification ? (
                  <span className="arbor-role-app-why"> · {a.justification}</span>
                ) : null}
              </span>
              <span className="arbor-role-app-actions">
                <button
                  type="button"
                  data-testid={`role-app-approve-${a.name}`}
                  onClick={() => onApprove({ role_application: a.name })}
                >
                  Approve
                </button>
                <button
                  type="button"
                  data-testid={`role-app-reject-${a.name}`}
                  onClick={() => onReject({ role_application: a.name })}
                >
                  Reject
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Active grants (revoke) */}
      <h2>
        Role grants <span className="arbor-count">{grants.length}</span>
      </h2>
      {grants.length > 0 && (
        <ul className="arbor-grants" data-testid="role-grants">
          {grants.map((g) => (
            <li key={g.name} className="arbor-grant" data-testid={`role-grant-${g.name}`}>
              <span className="arbor-grant-subject">
                <span className="arbor-grant-grantee">{g.grantee}</span>
                <span className="arbor-grant-arrow"> · </span>
                <span className="arbor-grant-branch">{roleLabel(g.role)}</span>
                {g.source === "application" ? (
                  <span className="arbor-role-source"> (applied)</span>
                ) : null}
              </span>
              {g.can_revoke && (
                <button
                  type="button"
                  data-testid={`role-grant-revoke-${g.name}`}
                  onClick={() => onRevoke({ role: g.role, grantee: g.grantee })}
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Assign form */}
      <div className="arbor-delegate-form" data-testid="assign-role-form">
        <label className="arbor-field">
          <span className="arbor-field-label">Role</span>
          <select
            data-testid="assign-role-select"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="">Select a role…</option>
            {assignable.map((r) => (
              <option key={r.role} value={r.role}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="arbor-field">
          <span className="arbor-field-label">User</span>
          <input
            data-testid="assign-role-grantee"
            placeholder="grantee (user)"
            value={grantee}
            onChange={(e) => setGrantee(e.target.value)}
          />
        </label>
        <button
          type="button"
          data-testid="assign-role-submit"
          disabled={!canAssign}
          onClick={() => {
            if (!canAssign) return;
            onAssign({ role, grantee: grantee.trim() });
            setRole("");
            setGrantee("");
          }}
        >
          Assign
        </button>
      </div>
    </section>
  );
}
