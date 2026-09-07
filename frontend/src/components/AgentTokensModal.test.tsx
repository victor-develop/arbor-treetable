// AgentTokensModal (Feature: agent tokens) — the in-app surface for the
// credential an external LLM agent carries. These specs drive App at the
// integration boundary (like the RolesModal / WebhookPanel specs): the header
// button opens the modal, the list renders the viewer's own token metadata, mint
// sends the least-privilege default payload, the plaintext secret + bootstrap
// prompt appear ONCE and are unrecoverable after dismissal, revoke needs a second
// click, and a server refusal is shown verbatim instead of swallowed.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { loginAs, mockClient } from "../test/fixture";
import type { AgentTokenMinted, AgentTokenView, ArborClient } from "../api";

const EXISTING: AgentTokenView[] = [
  {
    token_id: "AT-1",
    label: "reader bot",
    mode: "read",
    sheets: ["S"],
    expires_on: "2026-01-01",
    revoked: false,
    last_used_at: "2025-12-01",
  },
  {
    token_id: "AT-2",
    label: "old bot",
    mode: "write",
    sheets: null, // null => all sheets
    expires_on: null,
    revoked: true,
    last_used_at: null,
  },
];

// A client with an in-memory token store so mint / list / revoke round-trip. The
// store mirrors the server contract: the plaintext is returned ONLY by issue and
// never appears in a list row.
function tokenClient(opts?: { rejectIssue?: string; rejectList?: string }) {
  const base = mockClient({ snapshot: loginAs("A") });
  const store: AgentTokenView[] = EXISTING.map((t) => ({ ...t }));
  const calls: { method: string; args: unknown }[] = [];
  let seq = store.length;
  const client: ArborClient = {
    ...base.client,
    listAgentTokens: async () => {
      calls.push({ method: "list", args: null });
      if (opts?.rejectList) throw new Error(opts.rejectList);
      return store.map((t) => ({ ...t }));
    },
    issueAgentToken: async (params) => {
      calls.push({ method: "issue", args: params });
      if (opts?.rejectIssue) throw new Error(opts.rejectIssue);
      const id = `AT-${++seq}`;
      const minted: AgentTokenMinted = {
        token_id: id,
        token: "arbor_plaintext_secret_xyz",
        mode: params.mode,
        sheets: params.sheets ?? null,
        expires_on: "2026-06-01",
        bootstrap_prompt: "You can operate my Arbor data through its HTTP API. …",
      };
      store.push({
        token_id: id,
        label: params.label ?? "external agent",
        mode: params.mode,
        sheets: params.sheets ?? null,
        expires_on: minted.expires_on,
        revoked: false,
        last_used_at: null,
      });
      return minted;
    },
    revokeAgentToken: async (token_id) => {
      calls.push({ method: "revoke", args: token_id });
      const row = store.find((t) => t.token_id === token_id);
      if (row) row.revoked = true;
      return { token_id, revoked: true };
    },
  };
  return { client, calls, store };
}

// Open the modal from the sheet header button. Returns once the list has loaded.
async function openTokens() {
  fireEvent.click(await screen.findByTestId("agent-tokens-button"));
  await screen.findByTestId("agent-token-list");
}

// navigator.clipboard is UNDEFINED in jsdom — and on any non-secure origin, which
// is why the copy feedback has to be exercised in all three states rather than
// only in the one the test env happens to give us.
function stubClipboard(clipboard: unknown) {
  const had = Object.prototype.hasOwnProperty.call(navigator, "clipboard");
  const prev = (navigator as { clipboard?: unknown }).clipboard;
  Object.defineProperty(navigator, "clipboard", { value: clipboard, configurable: true });
  restoreClipboard = () => {
    if (had) Object.defineProperty(navigator, "clipboard", { value: prev, configurable: true });
    else delete (navigator as { clipboard?: unknown }).clipboard;
  };
}
let restoreClipboard: (() => void) | null = null;
afterEach(() => {
  restoreClipboard?.();
  restoreClipboard = null;
});

describe("AgentTokensModal — the viewer's own agent credentials", () => {
  it("renders the viewer's tokens with mode, scope, expiry, last use and state", async () => {
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    const active = screen.getByTestId("agent-token-row-AT-1");
    expect(active).toHaveTextContent("reader bot");
    expect(active).toHaveTextContent("read");
    expect(active).toHaveTextContent("S"); // sheet-scoped
    expect(active).toHaveTextContent("2026-01-01");
    expect(active).toHaveTextContent("2025-12-01");
    expect(screen.getByTestId("agent-token-state-AT-1")).toHaveTextContent("active");

    // A token with no sheet allow-list reads as "all sheets"; a revoked one says so
    // and offers no revoke button (it is already dead).
    const revoked = screen.getByTestId("agent-token-row-AT-2");
    expect(revoked).toHaveTextContent("all sheets");
    expect(screen.getByTestId("agent-token-state-AT-2")).toHaveTextContent("revoked");
    expect(screen.queryByTestId("agent-token-revoke-AT-2")).toBeNull();

    // The list NEVER carries a secret — that is the whole point of the surface.
    expect(screen.queryByTestId("agent-token-secret")).toBeNull();
  });

  it("mint sends the least-privilege default payload (read + JUST the current sheet)", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.change(screen.getByTestId("agent-token-label"), { target: { value: "my agent" } });
    fireEvent.click(screen.getByTestId("agent-token-mint"));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "issue")?.args).toEqual({
        label: "my agent",
        mode: "read",
        sheets: ["S"],
        ttl_days: 30,
      }),
    );
  });

  it("choosing write + all sheets widens the payload (sheets omitted entirely)", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.change(screen.getByTestId("agent-token-mode"), { target: { value: "write" } });
    fireEvent.change(screen.getByTestId("agent-token-scope"), { target: { value: "all" } });
    fireEvent.change(screen.getByTestId("agent-token-ttl"), { target: { value: "7" } });
    fireEvent.click(screen.getByTestId("agent-token-mint"));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "issue")?.args).toEqual({
        mode: "write",
        ttl_days: 7,
      }),
    );
  });

  it("an emptied TTL blocks the mint (ttl_days=0 would mean 'never expires')", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.change(screen.getByTestId("agent-token-ttl"), { target: { value: "" } });
    expect(screen.getByTestId("agent-token-mint")).toBeDisabled();
    fireEvent.click(screen.getByTestId("agent-token-mint"));
    expect(calls.some((c) => c.method === "issue")).toBe(false);
  });

  it("reveals the plaintext token + bootstrap prompt ONCE, with a copy affordance each", async () => {
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));

    const reveal = await screen.findByTestId("agent-token-reveal");
    expect(within(reveal).getByTestId("agent-token-secret")).toHaveTextContent(
      "arbor_plaintext_secret_xyz",
    );
    expect(within(reveal).getByTestId("agent-token-bootstrap")).toHaveValue(
      "You can operate my Arbor data through its HTTP API. …",
    );
    // The "you only get this once" warning has to be unmistakable.
    expect(reveal).toHaveTextContent(/shown once/i);
    expect(within(reveal).getByTestId("agent-token-copy-secret")).toBeInTheDocument();
    expect(within(reveal).getByTestId("agent-token-copy-prompt")).toBeInTheDocument();
  });

  it("the secret is unrecoverable after the reveal panel is dismissed", async () => {
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");
    // the new token is in the list (metadata only)
    await screen.findByTestId("agent-token-row-AT-3");

    fireEvent.click(screen.getByTestId("agent-token-reveal-dismiss"));

    expect(screen.queryByTestId("agent-token-reveal")).toBeNull();
    expect(screen.queryByTestId("agent-token-secret")).toBeNull();
    expect(screen.queryByTestId("agent-token-bootstrap")).toBeNull();
    // Nothing in the row re-exposes it, and there is no reveal-again control.
    expect(screen.getByTestId("agent-token-row-AT-3")).not.toHaveTextContent(
      "arbor_plaintext_secret_xyz",
    );
    // Reopening the modal cannot bring it back either (the list has no secret).
    fireEvent.click(screen.getByTestId("agent-tokens-close"));
    await openTokens();
    expect(screen.queryByTestId("agent-token-secret")).toBeNull();
  });

  it("revoke needs the second (confirm) click", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-revoke-AT-1"));
    // First click only arms the confirm — nothing has been revoked yet.
    expect(calls.some((c) => c.method === "revoke")).toBe(false);

    fireEvent.click(screen.getByTestId("agent-token-revoke-confirm-AT-1"));
    await waitFor(() => expect(calls).toContainEqual({ method: "revoke", args: "AT-1" }));
    await waitFor(() =>
      expect(screen.getByTestId("agent-token-state-AT-1")).toHaveTextContent("revoked"),
    );
  });

  it("cancelling the confirm leaves the token alone", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-revoke-AT-1"));
    fireEvent.click(screen.getByTestId("agent-token-revoke-cancel-AT-1"));
    expect(calls.some((c) => c.method === "revoke")).toBe(false);
    expect(screen.getByTestId("agent-token-revoke-AT-1")).toBeInTheDocument();
    expect(screen.getByTestId("agent-token-state-AT-1")).toHaveTextContent("active");
  });

  it("a server refusal on mint is shown verbatim, not swallowed", async () => {
    // e.g. minting FROM an agent-token session: "An agent token cannot mint tokens".
    const { client } = tokenClient({ rejectIssue: "An agent token cannot mint tokens (403)" });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));

    const err = await screen.findByTestId("agent-token-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(err).toHaveTextContent("An agent token cannot mint tokens (403)");
    // and no reveal panel appeared
    expect(screen.queryByTestId("agent-token-reveal")).toBeNull();
  });

  it("a failed list surfaces the reason instead of reading as 'no tokens'", async () => {
    const { client } = tokenClient({ rejectList: "Not permitted (401)" });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(await screen.findByTestId("agent-tokens-button"));

    const err = await screen.findByTestId("agent-token-error");
    expect(err).toHaveTextContent("Not permitted (401)");
  });

  it("says COPY BLOCKED when there is no clipboard API at all (never a false 'Copied')", async () => {
    // The failure this pins: an optional chain (`navigator.clipboard?.writeText`)
    // resolves to undefined WITHOUT throwing, so a missing API used to report
    // success — the user reads "Copied", clicks Done, and the only copy of the
    // secret is gone. jsdom has no clipboard, and neither does a non-secure origin.
    stubClipboard(undefined);
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");

    fireEvent.click(screen.getByTestId("agent-token-copy-secret"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-token-copy-secret")).toHaveTextContent(/copy blocked/i),
    );
    // and the secret is still on screen to select by hand
    expect(screen.getByTestId("agent-token-secret")).toHaveTextContent(
      "arbor_plaintext_secret_xyz",
    );
  });

  it("says COPY BLOCKED when writeText rejects (permission denied)", async () => {
    stubClipboard({ writeText: vi.fn(async () => Promise.reject(new Error("denied"))) });
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");

    fireEvent.click(screen.getByTestId("agent-token-copy-prompt"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-token-copy-prompt")).toHaveTextContent(/copy blocked/i),
    );
  });

  it("says Copied only when the clipboard actually took the text", async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard({ writeText });
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");

    fireEvent.click(screen.getByTestId("agent-token-copy-secret"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-token-copy-secret")).toHaveTextContent("Copied"),
    );
    expect(writeText).toHaveBeenCalledWith("arbor_plaintext_secret_xyz");
  });

  it("a backdrop click while the secret is on screen does NOT destroy it", async () => {
    // Selecting the token by hand (what the copy-blocked message asks for) ends
    // its click on the backdrop, and any stray click lands there too. Closing
    // here would drop the only copy of the plaintext with no confirmation.
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");

    fireEvent.click(screen.getByTestId("agent-tokens-modal"));

    expect(screen.getByTestId("agent-tokens-modal")).toBeInTheDocument();
    expect(screen.getByTestId("agent-token-secret")).toHaveTextContent(
      "arbor_plaintext_secret_xyz",
    );
  });

  it("the ✕ is withheld while the secret is on screen — Done is the only exit", async () => {
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-token-mint"));
    await screen.findByTestId("agent-token-reveal");
    expect(screen.queryByTestId("agent-tokens-close")).toBeNull();

    // Acknowledging the reveal hands the ✕ back, and it closes as usual.
    fireEvent.click(screen.getByTestId("agent-token-reveal-dismiss"));
    fireEvent.click(screen.getByTestId("agent-tokens-close"));
    expect(screen.queryByTestId("agent-tokens-modal")).toBeNull();
  });

  it("an absurd TTL blocks the mint (the issuer's date arithmetic would overflow)", async () => {
    const { client, calls } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.change(screen.getByTestId("agent-token-ttl"), { target: { value: "999999999" } });
    expect(screen.getByTestId("agent-token-mint")).toBeDisabled();
    fireEvent.click(screen.getByTestId("agent-token-mint"));
    expect(calls.some((c) => c.method === "issue")).toBe(false);
  });

  it("the close button dismisses the modal", async () => {
    const { client } = tokenClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openTokens();

    fireEvent.click(screen.getByTestId("agent-tokens-close"));
    expect(screen.queryByTestId("agent-tokens-modal")).toBeNull();
  });
});
