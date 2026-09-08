// Arbor Agent Tokens modal (Feature: agent tokens) — the in-app surface for the
// credential an EXTERNAL LLM agent uses. Header-launched next to Roles and built
// on the same `.arbor-modal` backdrop+panel shell (like RolesModal / WebhookPanel);
// it owns its own token list (client.listAgentTokens on mount) and funnels every
// write through the injected client, re-deriving no authority — the server
// session-gates issue (a request authenticated BY a token gets 403) and
// owner-gates revoke.
//
// The invariant this UI exists to protect: a token is BOTH identity and scope.
// One `X-Arbor-Agent-Token` header authenticates as the issuing user AND caps the
// request (read vs write, plus an optional sheet allow-list), so the mint form
// defaults to the LEAST privilege that is still useful — read mode, scoped to
// just the sheet the user is looking at. Widening is an explicit choice.
//
// The plaintext secret exists client-side exactly once, in `minted`. There is no
// reveal-again and no regenerate: the server stores only a keyed hash, the list
// endpoint never returns a secret, and dismissing the panel drops the only copy.
// Nothing here writes it to storage or to the token list.

import { useCallback, useEffect, useState } from "react";
import type { AgentTokenMinted, AgentTokenMode, AgentTokenView, ArborClient } from "../api";

// Token scope choices. "sheet" = only the sheet in view (the default); "all" =
// every sheet the issuer can reach (the server's absent-`sheets` meaning).
type TokenScope = "sheet" | "all";

// Copy without assuming a clipboard, and FAIL CLOSED when there isn't one:
// jsdom and any non-secure origin (a self-hosted instance on plain http, the
// frappe dev site) leave navigator.clipboard undefined. An optional chain here
// would make that case indistinguishable from a real copy — `await undefined`
// does not throw — so the button would say "Copied" while the only copy of the
// secret was never taken anywhere. Absent API => false, same as a rejection.
async function copyText(text: string): Promise<boolean> {
  const clipboard = navigator.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function scopeLabel(sheets: string[] | null): string {
  return sheets && sheets.length > 0 ? sheets.join(", ") : "all sheets";
}

// The two adapters stamp timestamps differently (an ISO instant with
// microseconds vs a plain date); a token's expiry / last use only ever needs the
// day, so trim anything that starts with one. Anything else passes through
// untouched rather than being guessed at.
function dayOf(ts: string | null): string {
  if (!ts) return "never";
  return /^\d{4}-\d{2}-\d{2}/.test(ts) ? ts.slice(0, 10) : ts;
}

// Ten years. Upper bound only so the guard is symmetric: the issuer computes
// now + timedelta(days=N), which overflows `datetime` for an absurd N and comes
// back as a bare 500 rather than a refusal the user can read.
const TTL_MAX_DAYS = 3650;

// A whole positive number of days, in range. The server treats ttl_days=0 as
// "never expires", so an emptied field (Number("") === 0) must not be able to
// mint a non-expiring credential by accident.
function ttlValid(days: number): boolean {
  return Number.isInteger(days) && days >= 1 && days <= TTL_MAX_DAYS;
}

export function AgentTokensModal({
  sheet,
  client,
  onClose,
}: {
  // The sheet in view — seeds the least-privilege default scope.
  sheet: string;
  client: ArborClient;
  onClose: () => void;
}): JSX.Element {
  // null = still loading (distinguishes "no tokens yet" from "not fetched").
  const [tokens, setTokens] = useState<AgentTokenView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<AgentTokenMode>("read");
  const [scope, setScope] = useState<TokenScope>("sheet");
  const [ttlDays, setTtlDays] = useState(30);
  // The one-time reveal. Held ONLY here; dismissing it is irreversible.
  const [minted, setMinted] = useState<AgentTokenMinted | null>(null);
  // Which field was last copied and whether the clipboard actually took it — a
  // copy button that silently does nothing is the worst outcome here.
  const [copied, setCopied] = useState<{ key: string; ok: boolean } | null>(null);
  // In flight — a double-click must not mint two credentials.
  const [minting, setMinting] = useState(false);
  // Two-step revoke (mirrors the column-delete confirm in ColumnConfig): the
  // first click arms the row, the second actually revokes.
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const refresh = useCallback(() => {
    const list = client.listAgentTokens;
    if (!list) {
      setTokens([]);
      return;
    }
    void Promise.resolve()
      .then(() => list())
      .then((rows) => setTokens(rows))
      .catch((e: unknown) => {
        // Surface the server's reason (401/403) rather than an empty list — a
        // silently swallowed failure here reads as "you have no tokens".
        setTokens([]);
        setError(e instanceof Error ? e.message : "Could not load your tokens");
      });
  }, [client]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onMint = useCallback(() => {
    const issue = client.issueAgentToken;
    if (!issue || minting || !ttlValid(ttlDays)) return;
    setError(null);
    setMinting(true);
    void Promise.resolve()
      .then(() =>
        issue({
          ...(label.trim() ? { label: label.trim() } : {}),
          mode,
          // "all sheets" omits `sheets` entirely (the server's own encoding);
          // the default sends just the sheet in view.
          ...(scope === "sheet" ? { sheets: [sheet] } : {}),
          ttl_days: ttlDays,
        }),
      )
      .then((m) => {
        setMinted(m);
        setLabel("");
        refresh();
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not mint a token"))
      .finally(() => setMinting(false));
  }, [client, label, minting, mode, scope, sheet, ttlDays, refresh]);

  const onRevoke = useCallback(
    (tokenId: string) => {
      const revoke = client.revokeAgentToken;
      if (!revoke) return;
      setError(null);
      setConfirmRevoke(null);
      void Promise.resolve()
        .then(() => revoke(tokenId))
        .then(() => refresh())
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not revoke"));
    },
    [client, refresh],
  );

  const copy = (key: string, text: string) => {
    void copyText(text).then((ok) => setCopied({ key, ok }));
  };

  // The copy button's own label doubles as its feedback: a browser that refuses
  // clipboard access says so, so the user knows to select the text by hand
  // instead of walking away with nothing.
  const copyLabel = (key: string, idle: string): string => {
    if (copied?.key !== key) return idle;
    return copied.ok ? "Copied" : "Copy blocked — select it manually";
  };

  return (
    <div
      className="arbor-modal-backdrop"
      data-testid="agent-tokens-modal"
      onClick={(e) => {
        // Backdrop click (outside the panel) closes the modal — mirrors RolesModal.
        // EXCEPT while the one-time reveal is up: closing drops the only copy of
        // the plaintext, and a backdrop click is far too easy to make by accident.
        // A selection drag that ends outside the panel lands its `click` on the
        // nearest common ancestor — this backdrop — so without this guard the
        // very gesture the copy-blocked message asks for would destroy the secret.
        if (e.target === e.currentTarget && !minted) onClose();
      }}
    >
      <div className="arbor-modal arbor-agent-tokens-modal">
        <header className="arbor-modal-head">
          <span>Agent tokens</span>
          {/* Same reason: while the secret is on screen the ONLY exit is the
              explicit "Done — I have copied it" in the reveal panel. */}
          {!minted && (
            <button
              type="button"
              data-testid="agent-tokens-close"
              aria-label="Close"
              onClick={onClose}
            >
              ✕
            </button>
          )}
        </header>
        <div className="arbor-agent-tokens-body">
          {/* Server refusals (401/403/404) land here verbatim, aria-live so the
              reason is announced instead of the write looking like a no-op. */}
          {error && (
            <p className="arbor-agent-token-error" role="alert" data-testid="agent-token-error">
              {error}
            </p>
          )}

          {/* The one-time reveal. Rendered ABOVE the list so it cannot be missed,
              and it stays until explicitly dismissed. */}
          {minted && (
            <section className="arbor-agent-token-reveal" data-testid="agent-token-reveal">
              <p className="arbor-agent-token-warn" role="status">
                Shown once — copy it now. Arbor keeps only a hash, so this token
                can never be shown again.
              </p>
              {/* A div, not a label: there is no form control to label here —
                  the secret is display-only text. */}
              <div className="arbor-field">
                <span className="arbor-field-label">Token</span>
                <code data-testid="agent-token-secret">{minted.token}</code>
              </div>
              <button
                type="button"
                data-testid="agent-token-copy-secret"
                onClick={() => copy("secret", minted.token)}
              >
                {copyLabel("secret", "Copy token")}
              </button>
              <label className="arbor-field">
                <span className="arbor-field-label">Bootstrap prompt</span>
                <textarea
                  readOnly
                  rows={6}
                  data-testid="agent-token-bootstrap"
                  value={minted.bootstrap_prompt}
                />
              </label>
              <button
                type="button"
                data-testid="agent-token-copy-prompt"
                onClick={() => copy("prompt", minted.bootstrap_prompt)}
              >
                {copyLabel("prompt", "Copy prompt")}
              </button>
              <button
                type="button"
                className="arbor-agent-token-dismiss"
                data-testid="agent-token-reveal-dismiss"
                onClick={() => {
                  setMinted(null);
                  setCopied(null);
                }}
              >
                Done — I have copied it
              </button>
            </section>
          )}

          {/* Existing tokens (metadata only — the secret is never here). */}
          {tokens === null ? (
            <p className="arbor-agent-token-empty" data-testid="agent-tokens-loading">
              Loading…
            </p>
          ) : (
            <ul className="arbor-agent-token-list" data-testid="agent-token-list">
              {tokens.length === 0 && (
                <li className="arbor-agent-token-empty" data-testid="agent-token-empty">
                  No agent tokens yet.
                </li>
              )}
              {tokens.map((t) => (
                <li key={t.token_id} data-testid={`agent-token-row-${t.token_id}`}>
                  <span className="arbor-agent-token-label">{t.label || t.token_id}</span>
                  <span className="arbor-agent-token-meta">
                    {t.mode} · {scopeLabel(t.sheets)}
                  </span>
                  <span className="arbor-agent-token-meta">
                    expires {dayOf(t.expires_on)} · last used {dayOf(t.last_used_at)}
                  </span>
                  <span
                    className="arbor-agent-token-state"
                    data-state={t.revoked ? "revoked" : "active"}
                    data-testid={`agent-token-state-${t.token_id}`}
                  >
                    {t.revoked ? "revoked" : "active"}
                  </span>
                  {/* A revoked token is already dead — no second kill to offer. */}
                  {!t.revoked &&
                    (confirmRevoke === t.token_id ? (
                      <span className="arbor-agent-token-confirm">
                        <button
                          type="button"
                          data-testid={`agent-token-revoke-confirm-${t.token_id}`}
                          onClick={() => onRevoke(t.token_id)}
                        >
                          Confirm revoke
                        </button>
                        <button
                          type="button"
                          className="arbor-agent-token-cancel"
                          data-testid={`agent-token-revoke-cancel-${t.token_id}`}
                          onClick={() => setConfirmRevoke(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        data-testid={`agent-token-revoke-${t.token_id}`}
                        onClick={() => setConfirmRevoke(t.token_id)}
                      >
                        Revoke
                      </button>
                    ))}
                </li>
              ))}
            </ul>
          )}

          {/* Mint form — least privilege by default (read, this sheet only). */}
          <form
            className="arbor-agent-token-mint"
            data-testid="agent-token-mint-form"
            onSubmit={(e) => {
              e.preventDefault();
              onMint();
            }}
          >
            <p className="arbor-agent-token-hint">
              New token — least privilege by default: read-only, this sheet only.
            </p>
            <label className="arbor-field">
              <span className="arbor-field-label">Label</span>
              <input
                type="text"
                data-testid="agent-token-label"
                placeholder="external agent"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <label className="arbor-field">
              <span className="arbor-field-label">Mode</span>
              <select
                data-testid="agent-token-mode"
                value={mode}
                onChange={(e) => setMode(e.target.value as AgentTokenMode)}
              >
                <option value="read">read</option>
                <option value="write">write</option>
              </select>
            </label>
            <label className="arbor-field">
              <span className="arbor-field-label">Scope</span>
              <select
                data-testid="agent-token-scope"
                value={scope}
                onChange={(e) => setScope(e.target.value as TokenScope)}
              >
                <option value="sheet">this sheet only ({sheet})</option>
                <option value="all">all sheets</option>
              </select>
            </label>
            <label className="arbor-field arbor-field-narrow">
              <span className="arbor-field-label">Expires in (days)</span>
              <input
                type="number"
                min={1}
                max={TTL_MAX_DAYS}
                data-testid="agent-token-ttl"
                value={ttlDays}
                onChange={(e) => setTtlDays(Number(e.target.value))}
              />
            </label>
            <button
              type="submit"
              data-testid="agent-token-mint"
              disabled={minting || !ttlValid(ttlDays) || !client.issueAgentToken}
            >
              {minting ? "Minting…" : "Mint token"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
