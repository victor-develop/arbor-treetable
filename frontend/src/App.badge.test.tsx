// WS-B2 — App process badge. The Dashboard nav link carries a live pending /
// breached count aggregated off the edge dashboard (client.processDashboard),
// replacing the old per-row current-stage wording. The badge only appears when
// there is work (pending or breached > 0); breached emphasis flips data-breached.
// A client with no processDashboard surface renders no badge (fails closed).

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { loginAs, mockClient } from "./test/fixture";
import type { ArborClient, ProcessDashboard as Dash } from "./api";

afterEach(() => {
  window.history.replaceState({}, "", "http://localhost/");
  vi.restoreAllMocks();
});

function withDashboard(dash: Dash): { client: ArborClient; spy: ReturnType<typeof vi.fn> } {
  const { client } = mockClient({ snapshot: loginAs("A") });
  const spy = vi.fn().mockResolvedValue(dash);
  return { client: { ...client, processDashboard: spy } as ArborClient, spy };
}

function dash(over: Partial<Dash> = {}): Dash {
  return { edges: [], total_active: 0, total_completed: 0, throughput: 0, ...over };
}

function edge(over: Partial<Dash["edges"][number]>): Dash["edges"][number] {
  return {
    rule_key: "r0",
    from_kind: "row",
    from_column: null,
    from_label: null,
    to_column: "owner_c",
    to_label: "Owner",
    join: "any",
    within_seconds: 0,
    pending_count: 0,
    breached_count: 0,
    satisfied_count: 0,
    avg_open_to_satisfy_seconds: null,
    ...over,
  };
}

describe("App — process dashboard badge", () => {
  it("shows aggregated pending + breached counts on the Dashboard nav link", async () => {
    const { client } = withDashboard(
      dash({
        edges: [
          edge({ rule_key: "r0", pending_count: 3, breached_count: 1 }),
          edge({ rule_key: "r1", pending_count: 2, breached_count: 2 }),
        ],
      }),
    );
    render(<App client={client} sheetName="S" />);

    const badge = await screen.findByTestId("process-badge");
    expect(within(badge).getByTestId("process-badge-pending")).toHaveTextContent("5");
    expect(within(badge).getByTestId("process-badge-breached")).toHaveTextContent("3");
    // Breach present -> emphasized.
    expect(badge).toHaveAttribute("data-breached", "true");
  });

  it("omits the breached sub-count and de-emphasizes when nothing is out of SLA", async () => {
    const { client } = withDashboard(dash({ edges: [edge({ pending_count: 4, breached_count: 0 })] }));
    render(<App client={client} sheetName="S" />);

    const badge = await screen.findByTestId("process-badge");
    expect(within(badge).getByTestId("process-badge-pending")).toHaveTextContent("4");
    expect(screen.queryByTestId("process-badge-breached")).toBeNull();
    expect(badge).toHaveAttribute("data-breached", "false");
  });

  it("renders NO badge when there is no process work (no edges)", async () => {
    const { client } = withDashboard(dash({ edges: [] }));
    render(<App client={client} sheetName="S" />);

    // The Dashboard link is present but carries no badge.
    await screen.findByTestId("nav-dashboard");
    await waitFor(() => expect(screen.queryByTestId("process-badge")).toBeNull());
  });

  it("renders NO badge when the client lacks a processDashboard surface", async () => {
    const { client } = mockClient({ snapshot: loginAs("A") });
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("nav-dashboard");
    await waitFor(() => expect(screen.queryByTestId("process-badge")).toBeNull());
  });
});
