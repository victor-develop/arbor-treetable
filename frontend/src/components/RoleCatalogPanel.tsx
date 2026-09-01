// Admin modal — Catalog tab (platform-admin console). Manages the ROLE CATALOG
// itself (which roles exist), complementing the Roles tab (who holds them):
//   * the existing roles (the same RoleView[] the app already loads), each
//     expandable into an inline edit form (label / applicable / active) that
//     dispatches onUpdateRole (→ arbor.admin.update_role);
//   * a create form (key / label / description / applicable) that dispatches
//     onCreateRole (→ arbor.admin.create_role).
// Presentation only: no fetching, no authority re-derivation (the standalone
// endpoints admin-gate server-side); the host refreshes roles after each write.

import { useState } from "react";
import type { RoleView } from "../api";

export function RoleCatalogPanel({
  roles,
  onCreateRole,
  onUpdateRole,
}: {
  roles: RoleView[];
  onCreateRole: (params: {
    role: string;
    label: string;
    description?: string;
    applicable?: boolean;
  }) => void;
  onUpdateRole: (params: {
    role: string;
    label?: string;
    applicable?: boolean;
    active?: boolean;
  }) => void;
}): JSX.Element {
  // Create-form fields.
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [applicable, setApplicable] = useState(true);
  // Which role's inline editor is open (one at a time) + its draft fields.
  const [editing, setEditing] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editApplicable, setEditApplicable] = useState(true);
  const [editActive, setEditActive] = useState(true);

  const canCreate = key.trim() !== "" && label.trim() !== "";

  const openEditor = (r: RoleView) => {
    setEditing(r.role);
    setEditLabel(r.label);
    setEditApplicable(r.applicable);
    setEditActive(r.active);
  };

  return (
    <section className="arbor-role-catalog" data-testid="role-catalog-panel">
      <h2>
        Role catalog <span className="arbor-count">{roles.length}</span>
      </h2>
      {roles.length === 0 ? (
        <p data-testid="role-catalog-empty">No roles defined.</p>
      ) : (
        <ul className="arbor-role-catalog-list" data-testid="role-catalog-list">
          {roles.map((r) => (
            <li key={r.role} className="arbor-role-catalog-row" data-testid={`role-catalog-row-${r.role}`}>
              <span className="arbor-role-catalog-subject">
                <span className="arbor-role-catalog-label">{r.label}</span>
                <span className="arbor-role-catalog-key"> · {r.role}</span>
                {!r.active && <span className="arbor-role-status is-withdrawn"> inactive</span>}
                {!r.applicable && (
                  <span className="arbor-role-catalog-flag"> (not self-applicable)</span>
                )}
              </span>
              <button
                type="button"
                data-testid={`role-catalog-edit-${r.role}`}
                onClick={() => (editing === r.role ? setEditing(null) : openEditor(r))}
              >
                {editing === r.role ? "Cancel" : "Edit"}
              </button>
              {editing === r.role && (
                <div className="arbor-delegate-form" data-testid="role-catalog-edit-form">
                  <label className="arbor-field">
                    <span className="arbor-field-label">Label</span>
                    <input
                      data-testid="role-catalog-edit-label"
                      value={editLabel}
                      onChange={(e) => setEditLabel(e.target.value)}
                    />
                  </label>
                  <label className="arbor-field arbor-field-check">
                    <input
                      type="checkbox"
                      data-testid="role-catalog-edit-applicable"
                      checked={editApplicable}
                      onChange={(e) => setEditApplicable(e.target.checked)}
                    />
                    <span className="arbor-field-label">Self-applicable</span>
                  </label>
                  <label className="arbor-field arbor-field-check">
                    <input
                      type="checkbox"
                      data-testid="role-catalog-edit-active"
                      checked={editActive}
                      onChange={(e) => setEditActive(e.target.checked)}
                    />
                    <span className="arbor-field-label">Active</span>
                  </label>
                  <button
                    type="button"
                    data-testid="role-catalog-edit-save"
                    disabled={editLabel.trim() === ""}
                    onClick={() => {
                      onUpdateRole({
                        role: r.role,
                        label: editLabel.trim(),
                        applicable: editApplicable,
                        active: editActive,
                      });
                      setEditing(null);
                    }}
                  >
                    Save
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Create form — key + label required; description/applicable optional. */}
      <h2>New role</h2>
      <div className="arbor-delegate-form" data-testid="role-catalog-create-form">
        <label className="arbor-field">
          <span className="arbor-field-label">Key</span>
          <input
            data-testid="role-catalog-key"
            placeholder="e.g. auditor"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </label>
        <label className="arbor-field">
          <span className="arbor-field-label">Label</span>
          <input
            data-testid="role-catalog-label"
            placeholder="e.g. Auditor"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="arbor-field">
          <span className="arbor-field-label">Description</span>
          <input
            data-testid="role-catalog-description"
            placeholder="optional"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="arbor-field arbor-field-check">
          <input
            type="checkbox"
            data-testid="role-catalog-applicable"
            checked={applicable}
            onChange={(e) => setApplicable(e.target.checked)}
          />
          <span className="arbor-field-label">Self-applicable</span>
        </label>
        <button
          type="button"
          data-testid="role-catalog-create-submit"
          disabled={!canCreate}
          onClick={() => {
            if (!canCreate) return;
            onCreateRole({
              role: key.trim(),
              label: label.trim(),
              ...(description.trim() === "" ? {} : { description: description.trim() }),
              applicable,
            });
            setKey("");
            setLabel("");
            setDescription("");
            setApplicable(true);
          }}
        >
          Create
        </button>
      </div>
    </section>
  );
}
