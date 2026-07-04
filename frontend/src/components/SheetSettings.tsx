// The ONE unified "Sheet Settings" surface (governance/schema consolidation).
//
// Before this, a sheet's configuration was scattered across four separate entries:
// the Process modal (ProcessConfigPanel), the Webhooks modal (WebhookPanel), the
// per-column gear (ColumnSettings), and the toolbar Add-column form (AddColumnForm).
// This surface folds all of them into ONE modal with tabs, seeded from a SINGLE
// read: `client.getSheetDefinition(sheet)` — the SAME capability the LLM agent
// reads. So the human panel and the agent can never diverge on the schema/governance
// they see, and opening Settings costs one cheap governance read (columns + process,
// NO rows) instead of a whole snapshot.
//
// The individual editor panels (ProcessConfigPanel / WebhookPanel / ColumnSettings /
// AddColumnForm) are UNCHANGED and simply re-hosted here; every write still funnels
// through the injected `client` / callbacks exactly as before.

import { useEffect, useMemo, useState } from "react";
import type {
  ArborClient,
  ProcessDef,
  ProcessRuleInput,
  SheetDefinition,
  SheetDefinitionColumn,
  SnapshotColumn,
} from "../api";
import { AddColumnForm, ColumnSettings } from "./ColumnConfig";
import { ProcessConfigPanel } from "./ProcessConfigPanel";
import { WebhookPanel } from "./WebhookPanel";

export type SheetSettingsTab = "columns" | "process" | "webhooks";

// Adapt a SheetDefinition column (schema/governance view) to the SnapshotColumn
// shape the existing sub-panels consume. The definition carries every field they
// need (name/field/label/type/owner/editors/is_label/options/can_edit) and NO cell
// values — the panels never read values, so this is a lossless projection.
function toSnapshotColumn(c: SheetDefinitionColumn): SnapshotColumn {
  return {
    name: c.name,
    field: c.field,
    label: c.label,
    type: c.type,
    is_label: c.is_label,
    column_owner: c.column_owner,
    editors: c.editors,
    can_edit: c.can_edit,
    options: c.options ?? null,
  };
}

// Project the definition's process block onto the ProcessDef shape ProcessConfigPanel
// hydrates from (it only reads sheet/title/enabled/row_scope/rules).
function toProcessDef(sheet: string, def: SheetDefinition): ProcessDef | null {
  if (!def.process) return null;
  return {
    sheet,
    title: null,
    enabled: def.process.enabled,
    row_scope: def.process.row_scope,
    rules: def.process.rules ?? [],
  };
}

export function SheetSettings({
  sheet,
  client,
  canConfigProcess,
  initialTab = "columns",
  processDef: processDefProp,
  onClose,
  onDefineProcess,
  onEnableProcess,
  onDisableProcess,
  onAddColumn,
  onUpdateColumn,
  onDeleteColumn,
  onGrantColumn,
}: {
  sheet: string;
  client: ArborClient;
  // Structural-owner / admin gate (same as the old Process + Webhooks buttons).
  // When false, only the Columns tab shows (a reader can still see the schema; the
  // per-column editor already degrades writes to suggestions server-side).
  canConfigProcess: boolean;
  initialTab?: SheetSettingsTab;
  // Optional pre-loaded ProcessDef (e.g. the host already fetched getProcess to get
  // the process TITLE, which the definition's lean process block omits). When
  // provided it seeds the Process tab; otherwise the definition's process block is
  // projected onto a ProcessDef (title null).
  processDef?: ProcessDef | null;
  onClose: () => void;
  onDefineProcess: (rules: ProcessRuleInput[], opts?: { title?: string; row_scope?: string }) => void;
  onEnableProcess: () => void;
  onDisableProcess: () => void;
  onAddColumn: (params: Record<string, unknown>) => void;
  onUpdateColumn: (params: Record<string, unknown>) => void;
  onDeleteColumn: (params: Record<string, unknown>) => void;
  onGrantColumn: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [tab, setTab] = useState<SheetSettingsTab>(initialTab);
  const [def, setDef] = useState<SheetDefinition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingColumn, setEditingColumn] = useState<string | null>(null);

  // ONE governance read seeds every tab (columns + process). No snapshot needed.
  useEffect(() => {
    let live = true;
    if (!client.getSheetDefinition) return;
    void client
      .getSheetDefinition(sheet)
      .then((d) => {
        if (live) setDef(d);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [client, sheet]);

  const columns = useMemo(
    () => (def?.columns ?? []).map(toSnapshotColumn),
    [def],
  );
  const processDef = useMemo(
    () => processDefProp ?? (def ? toProcessDef(sheet, def) : null),
    [processDefProp, sheet, def],
  );
  const isOwner = def?.sheet.structural_owner != null && canConfigProcess;

  const tabs: SheetSettingsTab[] = canConfigProcess
    ? ["columns", "process", "webhooks"]
    : ["columns"];

  return (
    <div
      className="arbor-modal-backdrop"
      data-testid="sheet-settings-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arbor-modal arbor-sheet-settings">
        <header className="arbor-modal-head">
          <span>Sheet Settings</span>
          <button type="button" data-testid="settings-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        {/* Tab strip — the ONE place to reach every sheet-config surface. */}
        <nav className="arbor-settings-tabs" role="tablist" data-testid="settings-tabs">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              data-testid={`settings-tab-${t}`}
              className={`arbor-settings-tab${tab === t ? " is-active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "columns" ? "Columns" : t === "process" ? "Flow" : "Delivery"}
            </button>
          ))}
        </nav>

        {error && (
          <p className="arbor-banner is-error" role="alert" data-testid="settings-error">
            {error}
          </p>
        )}
        {!def && !error && (
          <p className="arbor-settings-loading" data-testid="settings-loading">
            Loading sheet definition…
          </p>
        )}

        {def && tab === "columns" && (
          <section className="arbor-settings-panel" data-testid="settings-columns">
            {/* Add-column (the old toolbar form) now lives inside Settings. */}
            <AddColumnForm
              sheet={sheet}
              existingFields={def.columns.map((c) => c.field)}
              canAdd={isOwner}
              onSubmit={onAddColumn}
            />
            <ul className="arbor-settings-column-list">
              {columns.map((col) => (
                <li key={col.name} data-testid={`settings-column-${col.name}`}>
                  <button
                    type="button"
                    className="arbor-settings-column-row"
                    data-testid={`settings-column-toggle-${col.name}`}
                    aria-expanded={editingColumn === col.name}
                    onClick={() =>
                      setEditingColumn((cur) => (cur === col.name ? null : col.name))
                    }
                  >
                    <span className="arbor-settings-column-label">{col.label}</span>
                    <span className="arbor-settings-column-owner">{col.column_owner}</span>
                    {col.is_label && <span className="arbor-settings-column-badge">label</span>}
                  </button>
                  {editingColumn === col.name && (
                    <ColumnSettings
                      sheet={sheet}
                      column={col}
                      canConfigure={col.can_edit}
                      // The current column owner OR the sheet's structural owner may re-grant.
                      canGrant={col.can_edit || isOwner}
                      onUpdate={onUpdateColumn}
                      onDelete={onDeleteColumn}
                      onGrant={onGrantColumn}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {def && tab === "process" && canConfigProcess && (
          <section className="arbor-settings-panel" data-testid="settings-process">
            <ProcessConfigPanel
              // Re-seed the canvas once the definition's rules are known.
              key={processDef?.rules ? `loaded:${processDef.rules.length}` : "new"}
              sheet={sheet}
              columns={columns}
              process={processDef}
              onDefine={onDefineProcess}
              onEnable={onEnableProcess}
              onDisable={onDisableProcess}
            />
          </section>
        )}

        {def && tab === "webhooks" && canConfigProcess && (
          <section className="arbor-settings-panel" data-testid="settings-webhooks">
            {/* WebhookPanel renders its own modal chrome; here it is embedded, so we
                pass a no-op onClose (the outer Settings modal owns dismissal). */}
            <WebhookPanel sheet={sheet} client={client} onClose={onClose} embedded />
          </section>
        )}
      </div>
    </div>
  );
}
