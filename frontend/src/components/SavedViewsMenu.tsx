// Saved views picker (Feature: saved views) — the named-arrangement peer of
// <ViewMenu>. ViewMenu edits the live overlay; this lists the arrangements the
// server holds and applies one on click, so an arrangement survives a reload, a
// new browser, and a new machine.
//
// Ownership is HYBRID and the sections mirror it exactly: "My views" (the
// viewer's own, private unless published) and "Shared" (views someone published
// to this sheet). Publishing is always an explicit second step — a save is
// private.
//
// Three rules this component exists to keep:
//
// 1. NOTHING is auto-applied. The list loads on mount, but landing the user in
//    an arrangement they did not choose is worse than one extra click, so
//    applying is only ever a click. (There is also a "Default view" reset.)
// 2. Every failure is VISIBLE. A 403 (not your view), a 400 (bad/oversize
//    payload) and a 409 (name already taken) all land in the error line —
//    silently swallowing a refusal here has been a real bug in this codebase
//    twice, and a saved view that looks saved but isn't is exactly that bug.
// 3. Zero authority re-derivation and zero mutations: applying a view emits
//    onApply(view) and nothing else — no executeAction, no Tree Event, no
//    Change Request. Which rows offer publish/update/delete comes from the
//    server's `is_mine`, and the server still 403s a stranger's write.
//
// REVEAL-IMPOSSIBILITY is inherited, not re-implemented: an applied view is
// resolved by `resolveColumns` against the read-ACL-filtered snapshot columns,
// which never force-shows, and the server additionally redacts the names of
// unreadable columns from a shared view's payload.

import { useCallback, useEffect, useState } from "react";
import type { ArborClient, SavedViewView } from "../api";
import type { SheetView } from "../lib/view";

// The overlay meaning "no override" — what the reset affordance applies.
const DEFAULT_VIEW: SheetView = { v: 1, hidden: [], order: [] };

export type SavedViewsMenuProps = {
  sheet: string;
  // The live overlay, saved as-is by "Save current as…" / "Update".
  view: SheetView;
  onApply: (view: SheetView) => void;
  client: ArborClient;
};

export function SavedViewsMenu({
  sheet,
  view,
  onApply,
  client,
}: SavedViewsMenuProps): JSX.Element {
  // null = not fetched yet (distinguishes "no saved views" from "still loading").
  const [rows, setRows] = useState<SavedViewView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  // Two-step delete, mirroring the column-delete confirm in ColumnConfig: the
  // first click arms one row, the second actually deletes.
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(() => {
    const list = client.listSheetViews;
    if (!list) {
      setRows([]);
      return;
    }
    void Promise.resolve()
      .then(() => list(sheet))
      .then((r) => {
        // A successful list clears a previous failure's line — otherwise a
        // transient network blip leaves the red text sitting under a picker
        // that is now showing correct rows.
        setRows(r);
        setError(null);
      })
      .catch((e: unknown) => {
        // Surface the server's reason rather than an empty list — an empty
        // picker reads as "you have no saved views", which is a different fact.
        setRows([]);
        setError(e instanceof Error ? e.message : "Could not load saved views");
      });
  }, [client, sheet]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // One write path for save / update / publish / unpublish: they differ only in
  // which fields they send, and all three must report their refusal the same way.
  const write = useCallback(
    (params: Parameters<NonNullable<ArborClient["saveSheetView"]>>[0], onDone?: () => void) => {
      const save = client.saveSheetView;
      if (!save || saving) return;
      setError(null);
      setSaving(true);
      void Promise.resolve()
        .then(() => save(params))
        .then(() => {
          onDone?.();
          refresh();
        })
        .catch((e: unknown) =>
          setError(e instanceof Error ? e.message : "Could not save the view"),
        )
        .finally(() => setSaving(false));
    },
    [client, refresh, saving],
  );

  const onSaveNew = useCallback(() => {
    const name = label.trim();
    if (!name) return;
    write({ sheet, label: name, view }, () => setLabel(""));
  }, [label, sheet, view, write]);

  const onDelete = useCallback(
    (name: string) => {
      const del = client.deleteSheetView;
      if (!del) return;
      setError(null);
      setConfirmDelete(null);
      void Promise.resolve()
        .then(() => del(name))
        .then(() => refresh())
        .catch((e: unknown) =>
          setError(e instanceof Error ? e.message : "Could not delete the view"),
        );
    },
    [client, refresh],
  );

  const mine = (rows ?? []).filter((r) => r.is_mine);
  const shared = (rows ?? []).filter((r) => !r.is_mine);

  const row = (r: SavedViewView): JSX.Element => (
    <li className="arbor-saved-view" data-testid={`saved-view-${r.name}`} key={r.name}>
      <button
        type="button"
        className="arbor-saved-view-apply"
        data-testid={`saved-view-apply-${r.name}`}
        title={`Apply ${r.label}`}
        onClick={() => onApply(r.view)}
      >
        <span className="arbor-saved-view-label">{r.label}</span>
        {r.visibility === "sheet" && (
          <span className="arbor-saved-view-pill" data-testid={`saved-view-shared-${r.name}`}>
            shared
          </span>
        )}
        {!r.is_mine && <span className="arbor-saved-view-author">by {r.author}</span>}
      </button>
      {r.is_mine && (
        <>
          {/* Overwrite this view with the arrangement currently on screen. */}
          <button
            type="button"
            data-testid={`saved-view-update-${r.name}`}
            aria-label={`Update ${r.label} to the current arrangement`}
            disabled={saving}
            onClick={() => write({ name: r.name, view })}
          >
            Update
          </button>
          <button
            type="button"
            data-testid={`saved-view-publish-${r.name}`}
            aria-label={
              r.visibility === "sheet"
                ? `Unpublish ${r.label} from this sheet`
                : `Publish ${r.label} to this sheet`
            }
            disabled={saving}
            // Publish/unpublish sends ONLY the visibility: the stored
            // arrangement must not be silently replaced by whatever the viewer
            // happens to be looking at right now.
            onClick={() =>
              write({
                name: r.name,
                visibility: r.visibility === "sheet" ? "private" : "sheet",
              })
            }
          >
            {r.visibility === "sheet" ? "Unpublish" : "Publish"}
          </button>
          {confirmDelete === r.name ? (
            <span className="arbor-saved-view-confirm">
              <button
                type="button"
                data-testid={`saved-view-delete-confirm-${r.name}`}
                onClick={() => onDelete(r.name)}
              >
                Confirm delete
              </button>
              <button type="button" onClick={() => setConfirmDelete(null)}>
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              data-testid={`saved-view-delete-${r.name}`}
              aria-label={`Delete ${r.label}`}
              onClick={() => setConfirmDelete(r.name)}
            >
              Delete
            </button>
          )}
        </>
      )}
    </li>
  );

  return (
    <div className="arbor-saved-views" data-testid="saved-views-menu">
      {/* Server refusals land here verbatim, aria-live so the reason is
          announced instead of the write looking like a no-op. */}
      {error && (
        <p className="arbor-saved-views-error" role="alert" data-testid="saved-views-error">
          {error}
        </p>
      )}

      <section className="arbor-saved-views-section">
        <span className="arbor-field-label">My views</span>
        {rows === null ? (
          <p data-testid="saved-views-loading">Loading…</p>
        ) : mine.length === 0 ? (
          <p data-testid="saved-views-mine-empty">No saved views yet.</p>
        ) : (
          <ul className="arbor-saved-view-list">{mine.map(row)}</ul>
        )}
      </section>

      {shared.length > 0 && (
        <section className="arbor-saved-views-section">
          <span className="arbor-field-label">Shared</span>
          <ul className="arbor-saved-view-list">{shared.map(row)}</ul>
        </section>
      )}

      <section className="arbor-saved-views-save">
        <label className="arbor-field">
          <span className="arbor-field-label">Save current as…</span>
          <input
            data-testid="saved-views-name"
            placeholder="view name"
            value={label}
            onChange={(e) => setLabel(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSaveNew();
            }}
          />
        </label>
        <button
          type="button"
          data-testid="saved-views-save"
          disabled={saving || !label.trim()}
          onClick={onSaveNew}
        >
          Save view
        </button>
        <p className="arbor-saved-views-note">Saved views are private until you publish them.</p>
      </section>

      {/* Back to no override. Deliberately NOT a saved row: the default view is
          the absence of one. */}
      <button
        type="button"
        className="arbor-saved-views-reset"
        data-testid="saved-views-reset"
        onClick={() => onApply(DEFAULT_VIEW)}
      >
        Default view
      </button>
    </div>
  );
}
