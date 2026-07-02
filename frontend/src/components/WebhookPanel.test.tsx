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
import type { ArborClient, WebhookEndpointView } from "../api";

// A client with an in-memory webhook store so register/list/delete/test round-trip.
function webhookClient(opts?: { rejectRegister?: string }) {
  const base = mockClient({ snapshot: loginAs("A") }); // A owns sheet S -> canConfigProcess
  const store: WebhookEndpointView[] = [];
  let seq = 0;
  const calls: { method: string; args: unknown }[] = [];
  const client: ArborClient = {
    ...base.client,
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
  it("shows a header Webhooks button for the structural owner and opens the modal", async () => {
    const { client } = webhookClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const btn = await screen.findByTestId("webhook-config-button");
    expect(btn).toHaveTextContent(/webhooks/i);
    expect(screen.queryByTestId("webhook-modal")).toBeNull();

    fireEvent.click(btn);
    const modal = await screen.findByTestId("webhook-modal");
    expect(within(modal).getByTestId("webhook-register-form")).toBeInTheDocument();
  });

  it("register funnels through registerWebhook and surfaces the write-once secret", async () => {
    const { client, calls } = webhookClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    fireEvent.click(await screen.findByTestId("webhook-config-button"));
    await screen.findByTestId("webhook-modal");

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
    fireEvent.click(await screen.findByTestId("webhook-config-button"));
    await screen.findByTestId("webhook-modal");

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
    fireEvent.click(await screen.findByTestId("webhook-config-button"));
    await screen.findByTestId("webhook-modal");

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

  it("a non-owner, non-admin viewer sees no header Webhooks button", async () => {
    // persona B is neither structural owner of S nor admin.
    const base = mockClient({ snapshot: loginAs("B") });
    render(<App client={base.client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("webhook-config-button")).toBeNull();
  });
});
