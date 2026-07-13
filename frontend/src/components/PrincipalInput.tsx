// PrincipalInput — the ONE control for entering an ACL principal (a column owner
// or editor). A principal is either a concrete user (an email) OR a role
// reference of the form `role:<key>` that the server expands to the role's
// current grantees at check time (see arbor.core.acl `_expand_principals`).
//
// Before this, both spots were a bare text box and the `role:` prefix was an
// undiscoverable convention. This surfaces the choice explicitly: a User / Role
// segmented toggle. In User mode you type an email; in Role mode you pick from
// the site's Arbor Role catalog and the value becomes `role:<key>`. The emitted
// value is still just the principal string, so callers/capabilities are unchanged.

import { useState } from "react";
import type { RoleView } from "../api";

export function PrincipalInput({
  value,
  onChange,
  roles = [],
  testid,
  ariaLabel,
  placeholder = "name@example.com",
}: {
  value: string;
  onChange: (value: string) => void;
  // The site-wide Arbor Role catalog (from client.listRoles). Empty is fine —
  // Role mode then just offers no options and the user stays in User mode.
  roles?: RoleView[];
  // Base test id: the User-mode <input> carries it verbatim (so existing specs
  // that target e.g. "ac-owner" keep working); the Role <select> gets "-role".
  testid?: string;
  // Mirrored onto whichever control is active so getByLabelText / a11y resolve.
  ariaLabel?: string;
  placeholder?: string;
}): JSX.Element {
  const isRole = value.startsWith("role:");
  const [mode, setMode] = useState<"user" | "role">(isRole ? "role" : "user");

  // Switching kind clears the value so a stale email can't leak into a role slot
  // (or vice versa); the caller sees the field go empty until re-entered.
  const toUser = () => {
    setMode("user");
    if (isRole) onChange("");
  };
  const toRole = () => {
    setMode("role");
    if (!isRole) onChange("");
  };

  return (
    <div className="arbor-principal" data-testid={testid ? `${testid}-principal` : undefined}>
      <div className="arbor-principal-toggle" role="group" aria-label={ariaLabel ? `${ariaLabel} kind` : "principal kind"}>
        <button
          type="button"
          className={`arbor-principal-tab${mode === "user" ? " is-active" : ""}`}
          aria-pressed={mode === "user"}
          data-testid={testid ? `${testid}-mode-user` : undefined}
          onClick={toUser}
        >
          User
        </button>
        <button
          type="button"
          className={`arbor-principal-tab${mode === "role" ? " is-active" : ""}`}
          aria-pressed={mode === "role"}
          data-testid={testid ? `${testid}-mode-role` : undefined}
          onClick={toRole}
        >
          Role
        </button>
      </div>
      {mode === "user" ? (
        <input
          className="arbor-principal-input"
          data-testid={testid}
          aria-label={ariaLabel}
          placeholder={placeholder}
          value={isRole ? "" : value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <select
          className="arbor-principal-select"
          data-testid={testid ? `${testid}-role` : undefined}
          aria-label={ariaLabel}
          value={isRole ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">Select a role…</option>
          {roles.map((r) => (
            <option key={r.role} value={`role:${r.role}`}>
              {r.label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
