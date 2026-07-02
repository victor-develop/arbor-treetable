// The cell dispatcher: picks the editor by column type, gates edit-vs-suggest
// from the snapshot's per-column `can_edit` hint (never re-deriving ACL), and
// emits a single normalized commit value. The no-op guard (commit == snapshot)
// and Escape-cancel live here so every cell type inherits them
// (WEB_UI-011..024, -026..035).

import { useEffect, useRef, useState } from "react";
import type { CellCommentSummary, SnapshotColumn } from "../../api";
import { MessageIcon } from "../icons";
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
  draft,
  preview,
  proposed,
  startEditing,
  comments,
  onOpenComments,
  onCommit,
}: {
  column: SnapshotColumn;
  value: unknown;
  pending?: boolean;
  // Tooltip for the pending marker (e.g. "2 pending · dev@… → x; mkt@… → y").
  pendingTitle?: string;
  // How many open suggestions target this cell (shown in the marker badge).
  pendingCount?: number;
  // Draft flow — this cell carries an UNSUBMITTED local draft (the non-owner
  // draft box). When true the cell renders the (already-overlaid) draft value
  // with a DISTINCT "unsaved draft" treatment (amber dashed underline + a small
  // "draft" marker), clearly different from the pending-approval dot. The cell
  // still edits on click — a re-edit just rewrites the draft.
  draft?: boolean;
  // Proposed-view preview mode — the whole sheet is a READ-ONLY hypothetical.
  // The cell renders STATIC (no editor, no click-to-edit, no edit/suggest hint):
  // the user is previewing a "what if every proposal landed" state, not editing.
  preview?: boolean;
  // Within a preview, this cell's value is a PROPOSED value (from a pending
  // suggestion) rather than the real one — add a distinct "proposed" treatment
  // (a blue/accent underline + chip) clearly different from the amber draft.
  proposed?: boolean;
  // External edit trigger: a monotonically-incrementing signal. Each time it
  // increases to a truthy value the (interactive text-like) cell enters edit
  // mode and focuses — this is how the row's edit-pencil opens the label cell's
  // inline editor without the row reaching into the cell's internals.
  startEditing?: number;
  // Per-cell comment rollup from the snapshot (open/resolved/unread counts).
  // Sparse: only present when the cell has >=1 comment. Drives the comment glyph.
  comments?: CellCommentSummary;
  // Open this cell's comment thread (drawer). When supplied AND the cell has a
  // comment summary (or the viewer may start a thread), a small glyph shows.
  onOpenComments?: () => void;
  // Called only when the committed value differs from `value` (no-op guard).
  onCommit: (next: unknown) => void;
}): JSX.Element {
  const canEdit = column.can_edit;
  const interactive = isCellInteractive(column);
  // The comment affordance: shown only OUTSIDE preview (inert in Proposed), only
  // when the host wired onOpenComments AND the cell carries a comment summary.
  const glyph =
    !preview && onOpenComments && comments ? (
      <CommentGlyph summary={comments} onOpen={onOpenComments} />
    ) : null;

  const commitIfChanged = (next: unknown) => {
    const normalized = normalizeValue(column.type, next);
    if (valuesEqual(normalized, value)) return; // WEB_UI-018
    onCommit(normalized);
  };

  // Proposed-view preview: render every cell STATIC. No editor, no click-to-edit,
  // no edit/suggest hint. A proposed cell gets a distinct treatment (data-proposed
  // + accent underline chip); the pending marker still shows so the dot survives.
  if (preview) {
    return (
      <div
        className={`arbor-cell is-readonly is-preview${proposed ? " is-proposed" : ""}`}
        data-testid="cell"
        data-mode="preview"
        data-proposed={proposed ? "true" : undefined}
        data-pending={pending ? "true" : undefined}
        title={proposed ? "Proposed value" : undefined}
      >
        <span className="arbor-cell-value">
          {renderStatic(value) || <span className="arbor-cell-empty">—</span>}
        </span>
        {proposed && <ProposedMarker />}
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
        className={`arbor-cell${draft ? " is-draft" : ""}`}
        data-testid="cell"
        data-mode={canEdit ? "edit" : "suggest"}
        data-pending={pending ? "true" : undefined}
        data-draft={draft ? "true" : undefined}
      >
        <SelectSplitCell
          type={column.type}
          value={value}
          options={column.options}
          canEdit={canEdit}
          onCommit={(arr) => commitIfChanged(arr)}
        />
        {draft && <DraftMarker />}
        {glyph}
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
      isDraft={draft}
      startEditing={startEditing}
      commentGlyph={glyph}
      onCommit={commitIfChanged}
    />
  );
}

// The per-cell comment glyph: a small message icon with an open-count badge and
// an unread dot. Clicking opens the drawer WITHOUT entering the cell editor
// (stopPropagation), so it never fights the edit/draft/pending affordances.
function CommentGlyph({
  summary,
  onOpen,
}: {
  summary: CellCommentSummary;
  onOpen: () => void;
}): JSX.Element {
  const { open, resolved, unread } = summary;
  const total = open + resolved;
  const title =
    unread > 0
      ? `${unread} unread comment${unread === 1 ? "" : "s"}`
      : open > 0
        ? `${open} open comment${open === 1 ? "" : "s"}`
        : "Comments";
  return (
    <button
      type="button"
      className="arbor-comment-glyph"
      data-testid="comment-glyph"
      data-unread={unread > 0 ? "true" : undefined}
      data-count={open > 0 ? open : undefined}
      aria-label={title}
      title={title}
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
    >
      <MessageIcon size={13} />
      {open > 0 && <span className="arbor-comment-glyph-count">{open}</span>}
      {open === 0 && total > 0 && <span className="arbor-comment-glyph-dot" aria-hidden />}
    </button>
  );
}

// Draft flow — the "unsaved draft" marker: a small amber "draft" chip rendered
// on a cell carrying an unsubmitted local draft. Deliberately a WORD (not the
// pending-approval dot) so the two states never read as the same thing.
function DraftMarker(): JSX.Element {
  return (
    <span
      className="arbor-draft-marker"
      data-testid="draft-marker"
      title="Unsaved draft — submit for approval to send it"
    >
      draft
    </span>
  );
}

// Proposed-view marker: a small accent "proposed" chip on a cell whose value is
// a proposed suggestion (in the read-only Proposed preview). Deliberately a WORD
// with a distinct accent color so it never reads as the amber draft chip.
function ProposedMarker(): JSX.Element {
  return (
    <span
      className="arbor-proposed-marker"
      data-testid="proposed-marker"
      title="Proposed value — from an open suggestion"
    >
      proposed
    </span>
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
  isDraft,
  startEditing,
  commentGlyph,
  onCommit,
}: {
  column: SnapshotColumn;
  value: unknown;
  canEdit: boolean;
  pending?: boolean;
  pendingTitle?: string;
  pendingCount?: number;
  isDraft?: boolean;
  startEditing?: number;
  // The (already gated) comment glyph node — rendered in the non-editing view
  // beside the pending/draft markers. Undefined when there are no comments.
  commentGlyph?: JSX.Element | null;
  onCommit: (next: unknown) => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (editing) {
      ref.current?.focus();
      // Select-all on entry so the first keystroke replaces the value — single
      // click puts you straight into a "type to overwrite" edit (was double).
      ref.current?.select();
    }
  }, [editing]);

  const start = () => {
    setDraft(value == null ? "" : String(value));
    setInvalid(false);
    setEditing(true);
  };

  // External edit trigger (the row's edit-pencil): every increase of the
  // `startEditing` signal to a truthy value opens the editor seeded from the
  // current value. The previous-signal ref makes only a *change* fire it, so a
  // re-render with the same signal never re-opens a cell the user just closed.
  const lastSignal = useRef(0);
  useEffect(() => {
    if (startEditing && startEditing > lastSignal.current) {
      lastSignal.current = startEditing;
      start();
    }
    // `start` reads `value` at call time; we intentionally key only on the
    // signal so editing is driven by the trigger, not by value churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startEditing]);

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
        }${isDraft ? " is-draft" : ""}`}
        data-testid="cell"
        data-mode={canEdit ? "edit" : "suggest"}
        data-pending={pending ? "true" : undefined}
        data-draft={isDraft ? "true" : undefined}
        onClick={start}
        title={
          isDraft
            ? "Unsaved draft — click to edit"
            : canEdit
              ? "Click to edit"
              : "Click to suggest a change"
        }
      >
        <span className="arbor-cell-value">
          {renderStatic(value) || <span className="arbor-cell-empty">—</span>}
        </span>
        {isDraft && <DraftMarker />}
        {canEdit ? (
          <span className="arbor-edit-hint" aria-hidden title="Click to edit">
            <PencilIcon size={13} />
          </span>
        ) : (
          <span className="arbor-suggest-hint" aria-hidden title="Click to suggest a change">
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
        {commentGlyph}
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
