// The Sheet List home page — what renders when no ?sheet= is present. A thin
// shell over the capability client: it fetches sheet summaries via listSheets(),
// sorts them by node_count DESC (so real sheets float above the many orphan empty
// test sheets), shows each sheet's node_count, and offers a client-side text
// filter so the list stays usable even with thousands of rows. Each sheet is a
// link to ?sheet=<name>, which loads <App> (index.tsx). Re-derives nothing; the
// server supplies the catalog.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api as defaultClient, type ArborClient, type SheetSummary } from "../api";
import { AgentDock } from "./AgentDock";

export function SheetList({
  client,
  onNavigate,
}: {
  client?: ArborClient;
  // Navigate to a sheet after creating it. Optional; defaults to setting
  // window.location to ?sheet=<name> (so the home page is a thin shell with no
  // router). Tests pass a spy instead of touching jsdom navigation.
  onNavigate?: (sheet: string) => void;
} = {}): JSX.Element {
  const c = client ?? defaultClient;
  const [sheets, setSheets] = useState<SheetSummary[] | null>(null);
  const [filter, setFilter] = useState("");
  // New-sheet form state: the draft name, an in-flight guard, and the last
  // error (e.g. a duplicate name → 409) shown inline.
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // The last sheet the workspace agent created this session, surfaced as an
  // "open <sheet>" CTA. Cleared when the user opens it.
  const [createdSheet, setCreatedSheet] = useState<string | null>(null);

  const navigate =
    onNavigate ??
    ((sheet: string) => {
      window.location.search = `?sheet=${encodeURIComponent(sheet)}`;
    });

  const createSheet = () => {
    const name = newName.trim();
    if (!name || creating || !c.createSheet) return;
    setCreating(true);
    setCreateError(null);
    c.createSheet(name)
      .then((res) => navigate(res.sheet))
      .catch(() => {
        // A duplicate name (or any server error) surfaces inline instead of
        // navigating — the user can rename and retry.
        setCreateError(`Could not create "${name}" — that name may already be taken.`);
      })
      .finally(() => setCreating(false));
  };

  // Refetch the catalog. Stable across renders so the agent-created handler can
  // trigger a refresh without re-subscribing the mount effect.
  const refresh = useCallback(() => {
    if (!c.listSheets) {
      setSheets([]);
      return;
    }
    c.listSheets()
      .then((rows) => setSheets(rows))
      .catch(() => setSheets([]));
  }, [c]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // When the workspace agent reports a created sheet, refresh the list AND raise
  // an "open <sheet>" CTA so the user can jump straight into it.
  const onSheetCreated = useCallback(
    (name: string) => {
      setCreatedSheet(name);
      refresh();
    },
    [refresh],
  );

  // Sort by node_count desc (real sheets first), then apply the case-insensitive
  // substring filter on the name. Memoized so typing in the filter is cheap even
  // with thousands of sheets.
  const visible = useMemo(() => {
    const rows = [...(sheets ?? [])].sort((a, b) => b.node_count - a.node_count);
    const q = filter.trim().toLowerCase();
    return q ? rows.filter((s) => s.name.toLowerCase().includes(q)) : rows;
  }, [sheets, filter]);

  return (
    <main className="arbor-app arbor-sheet-list-page">
      <header className="arbor-header">
        <div className="arbor-header-titles">
          <h1>Arbor</h1>
          <div className="arbor-header-meta">
            <span>Governed, API-first, agent-native tree tables.</span>
          </div>
        </div>
      </header>

      <section className="arbor-sheet-list-zone">
        <div className="arbor-sheet-list-toolbar">
          <input
            type="search"
            className="arbor-sheet-filter"
            data-testid="sheet-filter"
            placeholder="Filter sheets…"
            aria-label="Filter sheets"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {/* New-sheet form: any authenticated user may create one (the server
              makes them its structural_owner). On success we navigate to the new
              sheet; a duplicate name surfaces inline. */}
          <form
            className="arbor-new-sheet"
            data-testid="new-sheet-form"
            onSubmit={(e) => {
              e.preventDefault();
              createSheet();
            }}
          >
            <input
              type="text"
              className="arbor-new-sheet-name"
              data-testid="new-sheet-name"
              placeholder="New sheet name…"
              aria-label="New sheet name"
              value={newName}
              onChange={(e) => {
                setNewName(e.target.value);
                if (createError) setCreateError(null);
              }}
            />
            <button
              type="submit"
              className="arbor-new-sheet-create"
              data-testid="new-sheet-create"
              disabled={creating || newName.trim() === ""}
            >
              {creating ? "Creating…" : "Create"}
            </button>
          </form>
        </div>
        {createError && (
          <p role="alert" className="arbor-new-sheet-error" data-testid="new-sheet-error">
            {createError}
          </p>
        )}
        {createdSheet && (
          <p className="arbor-agent-created" data-testid="agent-created">
            The agent created a sheet.{" "}
            <button
              type="button"
              className="arbor-agent-created-open"
              data-testid="open-created-sheet"
              onClick={() => navigate(createdSheet)}
            >
              Open {createdSheet}
            </button>
          </p>
        )}

        {sheets === null ? (
          <p data-testid="sheet-list-loading">Loading…</p>
        ) : visible.length === 0 ? (
          <p data-testid="sheet-list-empty">
            {sheets.length === 0 ? "No sheets yet." : "No sheets match your filter."}
          </p>
        ) : (
          <ul className="arbor-sheet-list" data-testid="sheet-list">
            {visible.map((s) => (
              <li
                key={s.name}
                className="arbor-sheet-row"
                data-testid={`sheet-row-${s.name}`}
                data-name={s.name}
              >
                <a
                  className="arbor-sheet-link"
                  data-testid={`sheet-link-${s.name}`}
                  href={`?sheet=${encodeURIComponent(s.name)}`}
                >
                  <span className="arbor-sheet-name">{s.name}</span>
                  <span className="arbor-sheet-owner">{s.structural_owner}</span>
                  <span className="arbor-sheet-count" data-testid={`sheet-count-${s.name}`}>
                    {s.node_count} nodes
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* The floating workspace agent — the SAME dock App mounts, but sheet-less.
          Talk to it in natural language to build a sheet end to end; when it
          creates one we refresh the list and raise the open CTA above. */}
      <AgentDock client={c} sheet={null} onSheetCreated={onSheetCreated} />
    </main>
  );
}
