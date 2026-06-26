// Global role-administration modal (IA fix). Role data is SITE-WIDE — listRoles /
// listRoleApplications / listRoleGrants take no sheet argument — so role admin no
// longer belongs in the per-sheet GovernancePanel (where every sheet, even a new
// empty one, showed the same global pending queue). It now opens from a header
// button into a modal that reuses the `.arbor-modal` backdrop+panel shell (same
// as ColumnSettings) and hosts the EXISTING RolesAdminPanel content unchanged:
// the applications inbox (Approve/Reject), active grants (Revoke), and the assign
// form. The stakeholder ask "approve is a popup, not an inline tab" falls out for
// free. This component owns only the modal chrome; every write still funnels
// through the host's roleOp handlers (which refresh roles + the snapshot).

import { RolesAdminPanel } from "./RolesAdminPanel";
import type { RoleApplicationView, RoleGrantView, RoleView } from "../api";

export function RolesModal({
  isAdmin,
  roles,
  grants,
  applications,
  onClose,
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
  onClose: () => void;
  onAssign: (params: Record<string, unknown>) => void;
  onRevoke: (params: Record<string, unknown>) => void;
  onApprove: (params: Record<string, unknown>) => void;
  onReject: (params: Record<string, unknown>) => void;
  onWithdraw: (params: Record<string, unknown>) => void;
}): JSX.Element {
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
          <span>Roles</span>
          <button type="button" data-testid="roles-modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="arbor-roles-modal-body">
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
        </div>
      </div>
    </div>
  );
}
