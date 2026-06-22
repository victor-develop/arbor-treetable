// The cell dispatcher: picks the editor by column type, gates edit-vs-suggest
// from the snapshot's per-column `can_edit` hint (never re-deriving ACL), and
// emits a single normalized commit value. The no-op guard (commit == snapshot)
// and Escape-cancel live here so every cell type inherits them
// (WEB_UI-011..024, -026..035).

import { useEffect, useRef, useState } from "react";
import type { SnapshotColumn } from "../../api";
import {
  isCellInteractive,
  isValidForType,
  normalizeValue,
  valuesEqual,
} from "../../lib/cells";
import { SelectSplitCell } from "./SelectSplitCell";
import { PencilIcon } from "../icons";

export function Cell({
  column,
  value,
  pending,
  pendingTitle,
  pendingCount,
  onCommit,
}: {
  column: SnapshotColumn;
  value: unknown;
  pending?: boolean;
  // Tooltip for the pending marker (e.g. "2 pending · dev@… → x; mkt@… → y").
  pendingTitle?: string;
  // How many open suggestions target this cell (shown in the marker badge).
  pendingCount?: number;
  // Called only when the committed value differs from `value` (no-op guard).
  onCommit: (next: unknown) => void;
}): JSX.Element {
  const canEdit = column.can_edit;
  const interactive = isCellInteractive(column);

  const commitIfChanged = (next: unknown) => {
    const normalized = normalizeValue(column.type, next);
    if (valuesEqual(normalized, value)) return; // WEB_UI-018
    onCommit(normalized);
  };

  if (!interactive) {
    return (
      <div className="arbor-cell is-readonly" data-testid="cell" data-mode="readonly">
        {renderStatic(value) || <span className="arbor-cell-empty">—</span>}
      </div>
    );
  }

  if (column.type === "single-select-split" || column.type === "multi-select-split") {
    return (
      <div
        className="arbor-cell"
        data-testid="cell"
        data-mode={canEdit ? "edit" : "suggest"}
        data-pending={pending ? "true" : undefined}
      >
        <SelectSplitCell
          type={column.type}
          value={value}
          options={column.options}
          canEdit={canEdit}
          onCommit={(arr) => commitIfChanged(arr)}
        />
      </div>
    );
  }

  return (
    <TextLikeCell
      column={column}
      value={value}
      canEdit={canEdit}
      pending={pending}
      pendingTitle={pendingTitle}
      pendingCount={pendingCount}
      onCommit={commitIfChanged}
    />
  );
}

function renderStatic(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  return value == null ? "" : String(value);
}

function TextLikeCell({
  column,
  value,
  canEdit,
  pending,
  pendingTitle,
  pendingCount,
  onCommit,
}: {
  column: SnapshotColumn;
  value: unknown;
  canEdit: boolean;
  pending?: boolean;
  pendingTitle?: string;
  pendingCount?: number;
  onCommit: (next: unknown) => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (editing) ref.current?.focus();
  }, [editing]);

  const start = () => {
    setDraft(value == null ? "" : String(value));
    setInvalid(false);
    setEditing(true);
  };

  const commit = () => {
    if (!isValidForType(column.type, draft)) {
      setInvalid(true);
      return; // WEB_UI-019: don't dispatch an invalid value
    }
    setEditing(false);
    onCommit(draft);
  };

  const cancel = () => {
    setEditing(false); // WEB_UI-017: revert, no dispatch
  };

  if (!editing) {
    return (
      <div
        className={`arbor-cell ${canEdit ? "is-editable" : "is-suggest"}${
          column.type === "multiline-text" ? " is-longtext" : ""
        }`}
        data-testid="cell"
        data-mode={canEdit ? "edit" : "suggest"}
        data-pending={pending ? "true" : undefined}
        onDoubleClick={start}
        title={canEdit ? "Double-click to edit" : "Double-click to suggest a change"}
      >
        <span className="arbor-cell-value">
          {renderStatic(value) || <span className="arbor-cell-empty">—</span>}
        </span>
        {canEdit ? (
          <span className="arbor-edit-hint" aria-hidden title="Double-click to edit">
            <PencilIcon size={13} />
          </span>
        ) : (
          <span className="arbor-suggest-hint" aria-hidden title="Double-click to suggest a change">
            <PencilIcon size={13} />
          </span>
        )}
        {pending && (
          <span
            className="arbor-pending"
            data-testid="pending-marker"
            data-count={pendingCount && pendingCount > 0 ? pendingCount : undefined}
            title={pendingTitle ?? "Suggestion pending"}
          >
            {pendingCount && pendingCount > 1 ? pendingCount : "•"}
          </span>
        )}
      </div>
    );
  }

  const onKey = (e: { key: string; preventDefault: () => void }) => {
    if (e.key === "Enter" && column.type !== "multiline-text") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  };

  const common = {
    value: draft,
    "data-testid": "cell-input",
    onChange: (e: { target: { value: string } }) => {
      setDraft(e.target.value);
      setInvalid(false);
    },
    onKeyDown: onKey,
    onBlur: commit,
  };

  return (
    <div className="arbor-cell is-editing" data-testid="cell" data-mode={canEdit ? "edit" : "suggest"}>
      {column.type === "multiline-text" ? (
        <textarea ref={ref as React.RefObject<HTMLTextAreaElement>} {...common} />
      ) : (
        <input
          ref={ref as React.RefObject<HTMLInputElement>}
          inputMode={column.type === "number" ? "decimal" : undefined}
          {...common}
        />
      )}
      {!canEdit && <span className="arbor-suggest-label">Suggest a change</span>}
      {invalid && (
        <span className="arbor-invalid" data-testid="invalid-hint" role="alert">
          Not a valid number
        </span>
      )}
    </div>
  );
}
