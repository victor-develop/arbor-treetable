// Schema editor (WEB_UI-051..062). Add column (gated on sheet structural_owner
// via viewer.can_add_column), configure/delete a column (gated on the snapshot
// column's can_edit hint = Axis-2 approver), and reassign ownership (grantColumn).
// All dispatch through executeAction; the component never re-derives ACL.

import { useState } from "react";
import type { ColumnType, SnapshotColumn } from "../api";
import { COLUMN_TYPES } from "../lib/capabilities";

export function AddColumnForm({
  sheet,
  existingFields,
  canAdd,
  onSubmit,
}: {
  sheet: string;
  existingFields: string[];
  canAdd: boolean;
  onSubmit: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [field, setField] = useState("");
  const [label, setLabel] = useState("");
  const [type, setType] = useState<ColumnType>("text");
  const [columnOwner, setColumnOwner] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [optDraft, setOptDraft] = useState("");

  const isSplit = type === "single-select-split" || type === "multi-select-split";
  const duplicate = existingFields.includes(field.trim());
  const optionsValid = !isSplit || options.length > 0;
  const canSubmit =
    field.trim() !== "" && label.trim() !== "" && !duplicate && optionsValid;

  const submit = () => {
    if (!canSubmit) return;
    const params: Record<string, unknown> = {
      sheet,
      field: field.trim(),
      label: label.trim(),
      type,
      column_owner: columnOwner || undefined,
    };
    if (isSplit) params.options = { groups: [{ label, options }] };
    onSubmit(params);
  };

  return (
    <form
      className="arbor-add-column"
      data-testid="add-column-form"
      data-mode={canAdd ? "direct" : "suggest"}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        data-testid="ac-field"
        placeholder="field key"
        value={field}
        onChange={(e) => setField(e.target.value)}
      />
      {duplicate && (
        <span role="alert" data-testid="ac-duplicate">
          Field key already exists
        </span>
      )}
      <input
        data-testid="ac-label"
        placeholder="Label"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />
      <select
        data-testid="ac-type"
        value={type}
        onChange={(e) => setType(e.target.value as ColumnType)}
      >
        {COLUMN_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <input
        data-testid="ac-owner"
        placeholder="column owner"
        value={columnOwner}
        onChange={(e) => setColumnOwner(e.target.value)}
      />
      {isSplit && (
        <div data-testid="ac-options">
          <input
            data-testid="ac-option-draft"
            placeholder="add option"
            value={optDraft}
            onChange={(e) => setOptDraft(e.target.value)}
          />
          <button
            type="button"
            data-testid="ac-option-add"
            onClick={() => {
              if (optDraft.trim()) {
                setOptions((o) => [...o, optDraft.trim()]);
                setOptDraft("");
              }
            }}
          >
            + option
          </button>
          <ul>
            {options.map((o) => (
              <li key={o}>{o}</li>
            ))}
          </ul>
        </div>
      )}
      <button type="submit" data-testid="ac-submit" disabled={!canSubmit}>
        {canAdd ? "Add column" : "Suggest column"}
      </button>
    </form>
  );
}

export function ColumnSettings({
  sheet,
  column,
  canConfigure,
  canGrant,
  onUpdate,
  onDelete,
  onGrant,
}: {
  sheet: string;
  column: SnapshotColumn;
  canConfigure: boolean; // column.can_edit
  canGrant: boolean; // current owner or sheet owner
  onUpdate: (params: Record<string, unknown>) => void;
  onDelete: (params: Record<string, unknown>) => void;
  onGrant: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [label, setLabel] = useState(column.label);
  const [width, setWidth] = useState(column.width ?? 120);
  const [editors, setEditors] = useState<string[]>(column.editors);
  const [editorDraft, setEditorDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div className="arbor-column-settings" data-testid={`col-settings-${column.name}`}>
      <section className="arbor-cs-fields">
        <label className="arbor-field">
          <span className="arbor-field-label">Label</span>
          <input
            data-testid="cs-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="arbor-field arbor-field-narrow">
          <span className="arbor-field-label">Width</span>
          <input
            data-testid="cs-width"
            type="number"
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
          />
        </label>
      </section>

      {canGrant && (
        <section data-testid="cs-ownership" className="arbor-cs-ownership">
          <span className="arbor-field-label arbor-cs-section-label">Editors</span>
          <input
            data-testid="cs-editor-draft"
            placeholder="add editor"
            value={editorDraft}
            onChange={(e) => setEditorDraft(e.target.value)}
          />
          <button
            type="button"
            data-testid="cs-editor-add"
            onClick={() => {
              if (editorDraft.trim()) {
                setEditors((es) => [...es, editorDraft.trim()]);
                setEditorDraft("");
              }
            }}
          >
            + editor
          </button>
          <button
            type="button"
            data-testid="cs-grant-save"
            onClick={() =>
              onGrant({
                sheet,
                column: column.name,
                column_owner: column.column_owner,
                editors,
              })
            }
          >
            Update editors
          </button>
        </section>
      )}

      <section className="arbor-cs-danger">
        <span className="arbor-cs-danger-label">Danger zone</span>
        {column.is_label ? (
          <p data-testid="cs-label-guard" role="alert">
            This is the label column. Reassign the label before deleting.
          </p>
        ) : !confirmDelete ? (
          <button type="button" data-testid="cs-delete" onClick={() => setConfirmDelete(true)}>
            Delete column
          </button>
        ) : (
          <span className="arbor-cs-confirm">
            <button
              type="button"
              data-testid="cs-delete-confirm"
              data-mode={canConfigure ? "direct" : "suggest"}
              onClick={() => onDelete({ sheet, column: column.name })}
            >
              Confirm delete
            </button>
            <button type="button" className="arbor-cs-cancel" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
          </span>
        )}
      </section>

      <footer className="arbor-cs-footer">
        <button
          type="button"
          data-testid="cs-save"
          data-mode={canConfigure ? "direct" : "suggest"}
          onClick={() => onUpdate({ sheet, column: column.name, patch: { label, width } })}
        >
          {canConfigure ? "Save" : "Suggest change"}
        </button>
      </footer>
    </div>
  );
}
