// Draft flow — the Draft Review modal. Lists the non-owner's staged cell drafts
// GROUPED by resolved approver (the target column's column_owner — who will get
// the suggestion), each row showing the field label + a node label + an old → new
// diff. Exposes a per-draft discard (×), a "Discard all", and a "Submit for
// approval" that files ONE multi-change CR. The modal owns only chrome + grouping
// presentation; every mutation funnels through the host callbacks (which call
// useSheet.discardDraft / discardAllDrafts / submitDrafts). Reuses the shared
// .arbor-modal backdrop+panel shell (like ColumnSettings / RolesModal).

// One resolved draft row the modal renders. The shell enriches each useSheet
// DraftView with the column label, the node label, the OLD (snapshot) value, and
// the resolved approver (column_owner) so the modal stays presentation-only.
export type DraftRow = {
  key: string;
  node: string;
  column: string;
  // human label of the target column (e.g. "Budget").
  columnLabel: string;
  // human label of the target node (e.g. "Task X"); falls back to the node id.
  nodeLabel: string;
  // the authoritative snapshot value before the draft (the "old" side).
  oldValue: unknown;
  // the proposed draft value (the "new" side).
  newValue: unknown;
  // who the suggestion will route to (the column's owner).
  approver: string;
};

export function DraftReviewModal({
  drafts,
  onClose,
  onSubmit,
  onDiscardOne,
  onDiscardAll,
}: {
  drafts: DraftRow[];
  onClose: () => void;
  // Submit the whole draft box as ONE multi-change CR.
  onSubmit: () => void;
  // Discard a single draft (by its (node, column)).
  onDiscardOne: (node: string, column: string) => void;
  // Discard every staged draft.
  onDiscardAll: () => void;
}): JSX.Element {
  // Group by resolved approver so the user sees who each batch goes to. Insertion
  // order of first appearance keeps the grouping stable across re-renders.
  const groups: { approver: string; rows: DraftRow[] }[] = [];
  for (const d of drafts) {
    let g = groups.find((x) => x.approver === d.approver);
    if (!g) {
      g = { approver: d.approver, rows: [] };
      groups.push(g);
    }
    g.rows.push(d);
  }
  const count = drafts.length;
  const noun = count === 1 ? "change" : "changes";

  return (
    <div
      className="arbor-modal-backdrop"
      data-testid="draft-modal"
      onClick={(e) => {
        // Backdrop click (outside the panel) closes — mirrors ColumnSettings.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arbor-modal arbor-draft-modal">
        <header className="arbor-modal-head">
          <span>
            Review {count} {noun}
          </span>
          <button type="button" data-testid="draft-modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="arbor-draft-modal-body">
          {count === 0 ? (
            <p data-testid="draft-modal-empty">No drafts to review.</p>
          ) : (
            groups.map((g) => (
              <section
                key={g.approver}
                className="arbor-draft-group"
                data-testid={`draft-group-${g.approver}`}
              >
                <h4 className="arbor-draft-group-head">
                  To approver: <strong>{g.approver}</strong>
                </h4>
                <ul className="arbor-draft-rows">
                  {g.rows.map((d) => (
                    <li
                      key={d.key}
                      className="arbor-draft-row"
                      data-testid={`draft-row-${d.node}-${d.column}`}
                    >
                      <div className="arbor-draft-row-target">
                        <span className="arbor-draft-row-field">{d.columnLabel}</span>
                        <span className="arbor-draft-row-node">{d.nodeLabel}</span>
                      </div>
                      <div className="arbor-draft-row-diff">
                        <span className="arbor-draft-old" data-testid="draft-old">
                          {fmt(d.oldValue)}
                        </span>
                        <span className="arbor-draft-arrow" aria-hidden>
                          →
                        </span>
                        <span className="arbor-draft-new" data-testid="draft-new">
                          {fmt(d.newValue)}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="arbor-draft-discard-one"
                        data-testid={`draft-discard-${d.node}-${d.column}`}
                        aria-label={`Discard draft for ${d.columnLabel} of ${d.nodeLabel}`}
                        title="Discard this draft"
                        onClick={() => onDiscardOne(d.node, d.column)}
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </div>

        <footer className="arbor-draft-modal-foot">
          <button
            type="button"
            className="arbor-draft-discard-all"
            data-testid="draft-discard-all"
            disabled={count === 0}
            onClick={onDiscardAll}
          >
            Discard all
          </button>
          <button
            type="button"
            className="arbor-draft-submit"
            data-testid="draft-submit"
            disabled={count === 0}
            onClick={onSubmit}
          >
            Submit for approval
          </button>
        </footer>
      </div>
    </div>
  );
}

// Render a cell value for the diff: arrays join, empty/nullish reads as "(empty)".
function fmt(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "(empty)";
  if (value === undefined || value === null || value === "") return "(empty)";
  return String(value);
}
