// Global Admin modal (IA fix, then platform-admin console). Role data is
// SITE-WIDE — listRoles / listRoleApplications / listRoleGrants take no sheet
// argument — so role admin no longer belongs in the per-sheet GovernancePanel
// (where every sheet, even a new empty one, showed the same global pending
// queue). It opens from a header button into a modal that reuses the
// `.arbor-modal` backdrop+panel shell (same as ColumnSettings) and now hosts
// THREE tabs (mirroring the SheetSettings tab strip):
//   * Roles   — the EXISTING RolesAdminPanel unchanged (applications inbox,
//               grants, assign form); every write still funnels through the
//               host's roleOp handlers (which refresh roles + the snapshot).
//   * Catalog — RoleCatalogPanel (create/edit the role definitions themselves)
//               via the standalone arbor.admin.create_role/update_role face.
//   * Users   — AdminUsersPanel (admin/enabled flags per account) via
//               arbor.admin.list_users/set_user.
// The Catalog/Users handlers + data are OPTIONAL props (default no-op/empty) so
// existing callers and tests keep working; this component owns only the modal
// chrome + tab switching, never fetching or authority.

import { useState } from "react";
import { AdminUsersPanel } from "./AdminUsersPanel";
import { RoleCatalogPanel } from "./RoleCatalogPanel";
import { RolesAdminPanel } from "./RolesAdminPanel";
import type { RoleApplicationView, RoleGrantView, RoleView, UserRow } from "../api";

export type AdminTab = "roles" | "catalog" | "users";

export function RolesModal({
  isAdmin,
  roles,
  grants,
  applications,
  users = [],
  selfEmail = null,
  onClose,
  onAssign,
  onRevoke,
  onApprove,
  onReject,
  onWithdraw,
  onCreateRole = () => {},
  onUpdateRole = () => {},
  onSetUser = () => {},
}: {
  isAdmin: boolean;
  roles: RoleView[];
  grants: RoleGrantView[];
  applications: RoleApplicationView[];
  users?: UserRow[];
  selfEmail?: string | null;
  onClose: () => void;
  onAssign: (params: Record<string, unknown>) => void;
  onRevoke: (params: Record<string, unknown>) => void;
  onApprove: (params: Record<string, unknown>) => void;
  onReject: (params: Record<string, unknown>) => void;
  onWithdraw: (params: Record<string, unknown>) => void;
  onCreateRole?: (params: {
    role: string;
    label: string;
    description?: string;
    applicable?: boolean;
  }) => void;
  onUpdateRole?: (params: {
    role: string;
    label?: string;
    applicable?: boolean;
    active?: boolean;
  }) => void;
  onSetUser?: (params: { email: string; is_admin?: boolean; enabled?: boolean }) => void;
}): JSX.Element {
  const [tab, setTab] = useState<AdminTab>("roles");
  // Catalog + Users are platform-admin faces; a non-admin (read-only "my
  // applications" view) gets no tab strip at all — just the Roles content.
  const tabs: AdminTab[] = isAdmin ? ["roles", "catalog", "users"] : ["roles"];
  const TAB_LABEL: Record<AdminTab, string> = { roles: "Roles", catalog: "Catalog", users: "Users" };

  return (
    <div
      className="arbor-modal-backdrop"
      data-testid="roles-modal"
      onClick={(e) => {
        // Backdrop click (outside the panel) closes the modal — mirrors ColumnSettings.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arbor-modal arbor-roles-modal">
        <header className="arbor-modal-head">
          <span>Admin</span>
          <button type="button" data-testid="roles-modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>
        {/* Tab strip — mirrors the SheetSettings idiom (underline-on-active). */}
        {tabs.length > 1 && (
          <nav className="arbor-settings-tabs" role="tablist" data-testid="admin-tabs">
            {tabs.map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                data-testid={`admin-tab-${t}`}
                className={`arbor-settings-tab${tab === t ? " is-active" : ""}`}
                onClick={() => setTab(t)}
              >
                {TAB_LABEL[t]}
              </button>
            ))}
          </nav>
        )}
        <div className="arbor-roles-modal-body">
          {tab === "roles" && (
            <RolesAdminPanel
              isAdmin={isAdmin}
              roles={roles}
              grants={grants}
              applications={applications}
              onAssign={onAssign}
              onRevoke={onRevoke}
              onApprove={onApprove}
              onReject={onReject}
              onWithdraw={onWithdraw}
            />
          )}
          {tab === "catalog" && (
            <RoleCatalogPanel roles={roles} onCreateRole={onCreateRole} onUpdateRole={onUpdateRole} />
          )}
          {tab === "users" && (
            <AdminUsersPanel users={users} selfEmail={selfEmail} onSetUser={onSetUser} />
          )}
        </div>
      </div>
    </div>
  );
}
