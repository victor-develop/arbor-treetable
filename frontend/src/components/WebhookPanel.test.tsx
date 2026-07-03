// WebhookPanel (Feature: webhooks, Area 3, WS-A3c) — the sheet-admin webhook
// registration surface. These specs drive App at the integration boundary: the
// structural-owner sees a header "Webhooks" button that opens the modal; register
// funnels through client.registerWebhook and surfaces the write-once secret ONCE;
// the list renders registered endpoints with Test + Delete; a server rejection
// (SSRF / admin gate) surfaces in an aria-live error; a non-owner sees no button.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { loginAs, mockClient } from "../test/fixture";
import type { ArborClient, SheetDefinition, WebhookEndpointView } from "../api";

// A lean SheetDefinition so the unified Sheet Settings modal (which hosts the
// Webhooks tab) can seed. Columns/process are irrelevant to the webhook specs.
const DEF: SheetDefinition = {
  sheet: { name: "S", title: "S", structural_owner: "A", label_column: "col:name", settings: {} },
  columns: [],
  process: null,
};

// Open the Webhooks tab of the unified Sheet Settings modal (the former standalone
// "Webhooks" header button now lives inside Settings). Returns once the embedded
// register form is present.
async function openWebhooks() {
  fireEvent.click(await screen.findByTestId("sheet-settings-button"));
  fireEvent.click(await screen.findByTestId("settings-tab-webhooks"));
  await screen.findByTestId("webhook-register-form");
}

// A client with an in-memory webhook store so register/list/delete/test round-trip.
function webhookClient(opts?: { rejectRegister?: string }) {
  const base = mockClient({ snapshot: loginAs("A") }); // A owns sheet S -> canConfigProcess
  const store: WebhookEndpointView[] = [];
  let seq = 0;
  const calls: { method: string; args: unknown }[] = [];
  const client: ArborClient = {
    ...base.client,
    getSheetDefinition: async () => DEF,
    registerWebhook: async (params) => {
      calls.push({ method: "register", args: params });
      if (opts?.rejectRegister) throw new Error(opts.rejectRegister);
      const ep: WebhookEndpointView = {
        name: `WH-${++seq}`,
        label: params.label ?? null,
        url: params.url,
        active: true,
        sheet: params.sheet ?? null,
        owner_user: "A",
        scope: "sheet",
        target: params.sheet ?? null,
        event_types: params.event_types ?? [],
        notification_sources: params.notification_sources ?? [],
        secret: `secret-${seq}`, // returned ONCE
      };
      store.push({ ...ep });
      return ep;
    },
    listWebhooks: async () => store.map(({ secret: _s, ...rest }) => rest as WebhookEndpointView),
    deleteWebhook: async (name) => {
      calls.push({ method: "delete", args: name });
      const i = store.findIndex((e) => e.name === name);
      if (i >= 0) store.splice(i, 1);
      return { ok: true };
    },
    testWebhook: async (name) => {
      calls.push({ method: "test", args: name });
      return { delivery: "WD-1", status: "delivered" };
    },
  };
  return { client, calls, store };
}

describe("WebhookPanel — sheet-admin webhook registration surface", () => {
  it("reaches the Webhooks tab from the unified Sheet Settings button", async () => {
    const { client } = webhookClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const btn = await screen.findByTestId("sheet-settings-button");
    // The consolidated entry is a single gear "Settings" button (title carries
    // the full "Sheet Settings" name for the a11y/tooltip label).
    expect(btn).toHaveTextContent(/settings/i);
    expect(btn).toHaveAttribute("title", "Sheet Settings");
    // the webhook surface is not mounted until Settings opens on its Webhooks tab.
    expect(screen.queryByTestId("webhook-register-form")).toBeNull();

    await openWebhooks();
    const panel = screen.getByTestId("settings-webhooks");
    expect(within(panel).getByTestId("webhook-register-form")).toBeInTheDocument();
  });

  it("register funnels through registerWebhook and surfaces the write-once secret", async () => {
    const { client, calls } = webhookClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openWebhooks();

    fireEvent.change(screen.getByTestId("webhook-url"), {
      target: { value: "https://hooks.example.com/arbor" },
    });
    // default source is "process"; also select "comment"
    fireEvent.click(screen.getByTestId("webhook-source-comment"));
    fireEvent.click(screen.getByTestId("webhook-register"));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "register")?.args).toMatchObject({
        url: "https://hooks.example.com/arbor",
        sheet: "S",
        notification_sources: ["process", "comment"],
      }),
    );
    // the secret is shown ONCE after register
    const secret = await screen.findByTestId("webhook-secret");
    expect(secret).toHaveTextContent(/secret-1/);
    // and the endpoint now appears in the list
    await screen.findByTestId("webhook-row-WH-1");
  });

  it("Test and Delete on a row call the client and update the UI", async () => {
    const { client, calls } = webhookClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openWebhooks();

    fireEvent.change(screen.getByTestId("webhook-url"), {
      target: { value: "https://hooks.example.com/x" },
    });
    fireEvent.click(screen.getByTestId("webhook-register"));
    await screen.findByTestId("webhook-row-WH-1");

    fireEvent.click(screen.getByTestId("webhook-test-WH-1"));
    await waitFor(() => expect(screen.getByTestId("webhook-teststatus-WH-1")).toHaveTextContent("delivered"));
    expect(calls).toContainEqual({ method: "test", args: "WH-1" });

    fireEvent.click(screen.getByTestId("webhook-delete-WH-1"));
    await waitFor(() => expect(screen.queryByTestId("webhook-row-WH-1")).toBeNull());
    expect(calls).toContainEqual({ method: "delete", args: "WH-1" });
  });

  it("a server rejection (SSRF / admin gate) surfaces in an aria-live error", async () => {
    const { client } = webhookClient({
      rejectRegister: "register_webhook failed: 400",
    });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    await openWebhooks();

    fireEvent.change(screen.getByTestId("webhook-url"), {
      target: { value: "http://169.254.169.254/" },
    });
    fireEvent.click(screen.getByTestId("webhook-register"));

    const err = await screen.findByTestId("webhook-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(err).toHaveTextContent(/400/);
    // no row was added
    expect(screen.queryByTestId("webhook-list")?.querySelectorAll("li[data-testid^='webhook-row']").length ?? 0).toBe(0);
  });

  it("a non-owner, non-admin viewer sees no Sheet Settings button (so no Webhooks tab)", async () => {
    // persona B is neither structural owner of S nor admin.
    const base = mockClient({ snapshot: loginAs("B") });
    render(<App client={base.client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("sheet-settings-button")).toBeNull();
  });
});
