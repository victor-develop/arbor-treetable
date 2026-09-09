// PrincipalInput — the ONE control for entering an ACL principal. A principal is
// a concrete user (an email), a role reference `role:<key>` that the server
// expands to the role's current grantees at check time, or — where the slot
// allows it — a whole email domain `domain:<host>` (see arbor.core.acl).
//
// Domain mode is opt-in per slot (`allowDomain`) and NOT offered for owner or
// editor, because a domain has no enumerable membership: it can answer "is this
// actor in?" but can never be listed as an approver or notified. The server
// rejects one in those slots, and offering a control that always errors would
// be worse than not offering it.
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
  allowDomain = false,
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
  // Offer the Domain tab. Only slots whose check is a pure membership test may
  // set this — today that means a column's READERS.
  allowDomain?: boolean;
}): JSX.Element {
  const isRole = value.startsWith("role:");
  const isDomain = value.startsWith("domain:");
  const [mode, setMode] = useState<"user" | "role" | "domain">(
    isRole ? "role" : isDomain ? "domain" : "user",
  );

  // Switching kind clears the value so a stale email can't leak into a role or
  // domain slot (or vice versa); the caller sees the field go empty until
  // re-entered.
  const toUser = () => {
    setMode("user");
    if (isRole || isDomain) onChange("");
  };
  const toRole = () => {
    setMode("role");
    if (!isRole) onChange("");
  };
  const toDomain = () => {
    setMode("domain");
    if (!isDomain) onChange("");
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
        {allowDomain && (
          <button
            type="button"
            className={`arbor-principal-tab${mode === "domain" ? " is-active" : ""}`}
            aria-pressed={mode === "domain"}
            data-testid={testid ? `${testid}-mode-domain` : undefined}
            onClick={toDomain}
          >
            Domain
          </button>
        )}
      </div>
      {mode === "domain" ? (
        <input
          className="arbor-principal-input"
          data-testid={testid ? `${testid}-domain` : undefined}
          aria-label={ariaLabel}
          placeholder="example.com"
          value={isDomain ? value.slice("domain:".length) : ""}
          // The stored shape carries the prefix; the box shows only the host so
          // nobody has to know the convention (and cannot half-type it).
          onChange={(e) => {
            const host = e.target.value.trim();
            onChange(host ? `domain:${host}` : "");
          }}
        />
      ) : mode === "user" ? (
        <input
          className="arbor-principal-input"
          data-testid={testid}
          aria-label={ariaLabel}
          placeholder={placeholder}
          value={isRole || isDomain ? "" : value}
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
