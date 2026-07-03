// Notification webhook admin panel (Feature: webhooks, Area 3, WS-A3c). A
// sheet-admin / structural-owner surface — the HOST gates its mount on the same
// structural-owner / admin hint the Process button uses (canConfigProcess); this
// shell re-derives no authority (the server admin-gates + SSRF-validates every
// write). It owns its OWN webhook list state (fetched via client.listWebhooks on
// mount) and funnels every write through the injected client methods.
//
// Register: pick a URL + notification sources (comment/process/sla/change_request)
// for the current sheet; the server generates the signing secret and returns it
// ONCE — we surface it inline so the admin can copy it (it is NEVER shown again).
// Each row shows the endpoint + a Test (fire a signed ping) and Delete action.
//
// Renders as a MODAL reusing the shared `.arbor-modal` shell (like RolesModal), so
// it is reachable from a header button without new global chrome.

import { useCallback, useEffect, useState } from "react";
import type { ArborClient, WebhookEndpointView } from "../api";

const SOURCES = ["comment", "process", "sla", "change_request"] as const;

export function WebhookPanel({
  sheet,
  client,
  onClose,
  embedded = false,
}: {
  sheet: string;
  client: ArborClient;
  onClose: () => void;
  // When true, render only the body (no backdrop/modal/header chrome) so the
  // panel can be HOSTED inside the unified Sheet Settings modal's Webhooks tab.
  // Default false preserves the standalone-modal behavior every existing caller
  // relies on.
  embedded?: boolean;
}): JSX.Element {
  const [endpoints, setEndpoints] = useState<WebhookEndpointView[]>([]);
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [sources, setSources] = useState<string[]>(["process"]);
  const [error, setError] = useState<string | null>(null);
  // The write-once secret from the most recent register (shown once, then cleared).
  const [freshSecret, setFreshSecret] = useState<{ name: string; secret: string } | null>(null);
  const [testStatus, setTestStatus] = useState<Record<string, string>>({});

  const refresh = useCallback(() => {
    if (!client.listWebhooks) return;
    void client
      .listWebhooks(sheet)
      .then(setEndpoints)
      .catch(() => setEndpoints([]));
  }, [client, sheet]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggleSource = (s: string) =>
    setSources((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  const onRegister = useCallback(() => {
    if (!client.registerWebhook) return;
    setError(null);
    void client
      .registerWebhook({ url: url.trim(), sheet, label: label.trim() || undefined, notification_sources: sources })
      .then((ep) => {
        if (ep.secret) setFreshSecret({ name: ep.name, secret: ep.secret });
        setUrl("");
        setLabel("");
        refresh();
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Register failed"));
  }, [client, url, sheet, label, sources, refresh]);

  const onDelete = useCallback(
    (name: string) => {
      if (!client.deleteWebhook) return;
      void client.deleteWebhook(name).then(() => {
        if (freshSecret?.name === name) setFreshSecret(null);
        refresh();
      });
    },
    [client, refresh, freshSecret],
  );

  const onTest = useCallback(
    (name: string) => {
      if (!client.testWebhook) return;
      void client
        .testWebhook(name)
        .then((r) => setTestStatus((cur) => ({ ...cur, [name]: r.status ?? "sent" })))
        .catch(() => setTestStatus((cur) => ({ ...cur, [name]: "error" })));
    },
    [client],
  );

  const body = (
    <div className="arbor-webhook-body">
          {/* Register form */}
          <form
            className="arbor-webhook-register"
            data-testid="webhook-register-form"
            onSubmit={(e) => {
              e.preventDefault();
              onRegister();
            }}
          >
            <label>
              Endpoint URL
              <input
                type="url"
                data-testid="webhook-url"
                placeholder="https://hooks.example.com/arbor"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </label>
            <label>
              Label (optional)
              <input
                type="text"
                data-testid="webhook-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <fieldset className="arbor-webhook-sources">
              <legend>Notification sources</legend>
              {SOURCES.map((s) => (
                <label key={s}>
                  <input
                    type="checkbox"
                    data-testid={`webhook-source-${s}`}
                    checked={sources.includes(s)}
                    onChange={() => toggleSource(s)}
                  />
                  {s}
                </label>
              ))}
            </fieldset>
            <button type="submit" data-testid="webhook-register" disabled={!url.trim()}>
              Register webhook
            </button>
          </form>

          {/* SSRF / admin-gate errors surface here (aria-live so a screen reader
              announces a rejected URL). */}
          {error && (
            <p className="arbor-webhook-error" role="alert" data-testid="webhook-error">
              {error}
            </p>
          )}

          {/* The write-once signing secret, shown ONCE right after register. */}
          {freshSecret && (
            <p className="arbor-webhook-secret" data-testid="webhook-secret" role="status">
              Signing secret (shown once — copy it now):{" "}
              <code>{freshSecret.secret}</code>
            </p>
          )}

          {/* Registered endpoints */}
          <ul className="arbor-webhook-list" data-testid="webhook-list">
            {endpoints.length === 0 && (
              <li className="arbor-webhook-empty" data-testid="webhook-empty">
                No webhooks registered for this sheet.
              </li>
            )}
            {endpoints.map((ep) => (
              <li key={ep.name} data-testid={`webhook-row-${ep.name}`}>
                <span className="arbor-webhook-url">{ep.label || ep.url}</span>
                <span className="arbor-webhook-sources-tags">
                  {ep.notification_sources.join(", ") || "—"}
                </span>
                {testStatus[ep.name] && (
                  <span className="arbor-webhook-teststatus" data-testid={`webhook-teststatus-${ep.name}`}>
                    {testStatus[ep.name]}
                  </span>
                )}
                <button type="button" data-testid={`webhook-test-${ep.name}`} onClick={() => onTest(ep.name)}>
                  Test
                </button>
                <button
                  type="button"
                  data-testid={`webhook-delete-${ep.name}`}
                  onClick={() => onDelete(ep.name)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
    </div>
  );

  // Embedded (inside Sheet Settings): just the body — the host owns the chrome.
  if (embedded) return body;

  // Standalone modal (every existing caller): backdrop + modal + header + body.
  return (
    <div
      className="arbor-modal-backdrop"
      data-testid="webhook-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arbor-modal arbor-webhook-modal">
        <header className="arbor-modal-head">
          <span>Webhooks</span>
          <button type="button" data-testid="webhook-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>
        {body}
      </div>
    </div>
  );
}
