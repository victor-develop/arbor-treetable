// Bench-free unit spec for the real ArborClient (`api`) — the impl over Frappe's
// /api/method/* surface. These tests exercise the WAVE 2/3 additions
// (impersonation + per-cell comments + process/SLA/inbox): they assert the fetch
// SHAPE each client method produces (URL, method, body / query string) and that
// the Frappe `{message: ...}` envelope is unwrapped. GET reads mirror the
// listActivity/listNotifications header+qs pattern; writes funnel through post();
// the governed capabilities (begin/end impersonation, define/enable/disable/
// startProcessRun) route via arbor.execute_action exactly like every mutation.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  setFetchImpl,
  setAuthHeaderProvider,
  type CellComment,
  type InboxItem,
  type ProcessDashboard,
  type ProcessDef,
  type ProcessRun,
  type Whoami,
} from "./api";

// A fetch double that records the last call and returns a Frappe-wrapped body.
// Frappe wraps every whitelisted return in { message: <value> }; the client's
// unwrap() must peel it so callers see the bare payload.
function mockFetch(payload: unknown, ok = true, status = 200) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const impl = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok,
      status,
      json: async () => ({ message: payload }),
    } as unknown as Response;
  });
  setFetchImpl(impl as unknown as typeof fetch);
  return { calls, impl };
}

function lastBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init?.body ?? "{}"));
}

afterEach(() => {
  // Restore defaults so tests don't leak the mocked fetch / auth header.
  setFetchImpl(((...a: unknown[]) =>
    (globalThis.fetch as (...x: unknown[]) => Promise<Response>)(...a)) as unknown as typeof fetch);
  setAuthHeaderProvider(async () => ({}));
  vi.restoreAllMocks();
});

// ---- impersonation ---------------------------------------------------------

describe("impersonation client", () => {
  it("whoami GETs arbor.auth.whoami and unwraps the envelope", async () => {
    const who: Whoami = {
      user: "owner@example.com",
      real_user: "admin@example.com",
      impersonating: true,
      authenticated: true,
    };
    const { calls } = mockFetch(who);
    const out = await api.whoami!();
    expect(out).toEqual(who);
    expect(calls[0].url).toBe("/api/method/arbor.auth.whoami");
    // read: no method override (defaults to GET)
    expect(calls[0].init?.method).toBeUndefined();
  });

  it("beginImpersonation routes through execute_action with the capability id", async () => {
    const { calls } = mockFetch({ kind: "executed", data: { impersonating: "owner@example.com" } });
    const out = await api.beginImpersonation!("owner@example.com", "cover shift");
    expect(out.kind).toBe("executed");
    expect((out.data as Record<string, unknown>).impersonating).toBe("owner@example.com");
    expect(calls[0].url).toBe("/api/method/arbor.execute_action");
    expect(calls[0].init?.method).toBe("POST");
    expect(lastBody(calls[0].init)).toEqual({
      action_id: "beginImpersonation",
      params: { impersonated_user: "owner@example.com", reason: "cover shift" },
    });
  });

  it("beginImpersonation omits reason when not supplied", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.beginImpersonation!("owner@example.com");
    expect(lastBody(calls[0].init)).toEqual({
      action_id: "beginImpersonation",
      params: { impersonated_user: "owner@example.com" },
    });
  });

  it("endImpersonation routes through execute_action with empty params", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.endImpersonation!();
    expect(lastBody(calls[0].init)).toEqual({ action_id: "endImpersonation", params: {} });
  });
});

// ---- per-cell comments -----------------------------------------------------

describe("comments client", () => {
  const cmt: CellComment = {
    name: "c1",
    thread_root: null,
    parent_comment: null,
    author: "alice@example.com",
    body: "why is this blank?",
    mentions: ["bob@example.com"],
    resolved: false,
    resolved_by: null,
    resolved_at: null,
    timestamp: "2026-07-02T10:00:00",
    can_resolve: true,
    can_delete: true,
  };

  it("listCellComments GETs with sheet/node/column query params", async () => {
    const { calls } = mockFetch([cmt]);
    const out = await api.listCellComments!("S1", "N1", "col1");
    expect(out).toEqual([cmt]);
    const u = new URL(calls[0].url, "http://x");
    expect(u.pathname).toBe("/api/method/arbor.list_cell_comments");
    expect(u.searchParams.get("sheet")).toBe("S1");
    expect(u.searchParams.get("node")).toBe("N1");
    expect(u.searchParams.get("column")).toBe("col1");
  });

  it("addCellComment posts the body + optional parent/mentions, dropping unset", async () => {
    const { calls } = mockFetch({ name: "c2", thread_root: "c1", mentions: [] });
    await api.addCellComment!("S1", "N1", "col1", "a reply", {
      parent_comment: "c1",
      mentions: ["bob@example.com"],
    });
    expect(calls[0].url).toBe("/api/method/arbor.add_cell_comment");
    expect(lastBody(calls[0].init)).toEqual({
      sheet: "S1",
      node: "N1",
      column: "col1",
      body: "a reply",
      parent_comment: "c1",
      mentions: ["bob@example.com"],
    });
  });

  it("addCellComment omits parent_comment and mentions when not given", async () => {
    const { calls } = mockFetch({ name: "c3", thread_root: null, mentions: [] });
    await api.addCellComment!("S1", "N1", "col1", "root comment");
    expect(lastBody(calls[0].init)).toEqual({
      sheet: "S1",
      node: "N1",
      column: "col1",
      body: "root comment",
    });
  });

  it("resolveCellComment posts resolved=true by default", async () => {
    const { calls } = mockFetch({ ok: true });
    await api.resolveCellComment!("c1");
    expect(calls[0].url).toBe("/api/method/arbor.resolve_cell_comment");
    expect(lastBody(calls[0].init)).toEqual({ comment: "c1", resolved: true });
  });

  it("reopenCellComment posts resolved=false through resolve_cell_comment", async () => {
    const { calls } = mockFetch({ ok: true });
    await api.reopenCellComment!("c1");
    expect(calls[0].url).toBe("/api/method/arbor.resolve_cell_comment");
    expect(lastBody(calls[0].init)).toEqual({ comment: "c1", resolved: false });
  });

  it("deleteCellComment posts the comment name", async () => {
    const { calls } = mockFetch({ ok: true });
    const out = await api.deleteCellComment!("c1");
    expect(out).toEqual({ ok: true });
    expect(calls[0].url).toBe("/api/method/arbor.delete_cell_comment");
    expect(lastBody(calls[0].init)).toEqual({ comment: "c1" });
  });
});

// ---- process / SLA / inbox -------------------------------------------------

describe("process client", () => {
  it("defineProcess routes through execute_action, threading only set opts", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.defineProcess!("S1", [{ column: "a" }, { column: "b", sla_seconds: 3600 }], {
      title: "Fill order",
      row_scope: "root-children",
    });
    expect(calls[0].url).toBe("/api/method/arbor.execute_action");
    expect(lastBody(calls[0].init)).toEqual({
      action_id: "defineProcess",
      params: {
        sheet: "S1",
        stages: [{ column: "a" }, { column: "b", sla_seconds: 3600 }],
        title: "Fill order",
        row_scope: "root-children",
      },
    });
  });

  it("defineProcess sends only sheet+stages when no opts", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.defineProcess!("S1", [{ column: "a" }]);
    expect(lastBody(calls[0].init)).toEqual({
      action_id: "defineProcess",
      params: { sheet: "S1", stages: [{ column: "a" }] },
    });
  });

  it("enableProcess / disableProcess route through execute_action", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.enableProcess!("S1");
    await api.disableProcess!("S1");
    expect(lastBody(calls[0].init)).toEqual({ action_id: "enableProcess", params: { sheet: "S1" } });
    expect(lastBody(calls[1].init)).toEqual({ action_id: "disableProcess", params: { sheet: "S1" } });
  });

  it("startProcessRun routes through execute_action with sheet+node", async () => {
    const { calls } = mockFetch({ kind: "executed", data: {} });
    await api.startProcessRun!("S1", "N1");
    expect(lastBody(calls[0].init)).toEqual({
      action_id: "startProcessRun",
      params: { sheet: "S1", node: "N1" },
    });
  });

  it("getProcess GETs the definition for a sheet", async () => {
    const def: ProcessDef = {
      sheet: "S1",
      title: "Fill order",
      enabled: true,
      row_scope: "root-children",
      start_trigger: "node-created",
      stages: [{ idx: 0, column: "a", label: "A", sla_seconds: 0, current_owner: "alice@example.com" }],
    };
    const { calls } = mockFetch(def);
    const out = await api.getProcess!("S1");
    expect(out).toEqual(def);
    const u = new URL(calls[0].url, "http://x");
    expect(u.pathname).toBe("/api/method/arbor.get_process");
    expect(u.searchParams.get("sheet")).toBe("S1");
  });

  it("processDashboard GETs the aggregate for a sheet", async () => {
    const dash: ProcessDashboard = {
      stages: [
        { idx: 0, column: "a", label: "A", pending_count: 2, breached_count: 1, avg_enter_to_fill_seconds: 120 },
      ],
      total_active: 2,
      total_completed: 5,
      throughput: 5,
    };
    const { calls } = mockFetch(dash);
    const out = await api.processDashboard!("S1");
    expect(out).toEqual(dash);
    expect(new URL(calls[0].url, "http://x").pathname).toBe("/api/method/arbor.process_dashboard");
  });

  it("listProcessRuns GETs with sheet + optional stage_idx/status filters", async () => {
    const runs: ProcessRun[] = [
      {
        name: "r1",
        process: "P1",
        sheet: "S1",
        node: "N1",
        status: "active",
        current_stage_idx: 1,
        started_at: "2026-07-01T00:00:00",
        completed_at: null,
      },
    ];
    const { calls } = mockFetch(runs);
    await api.listProcessRuns!("S1", { stage_idx: 1, status: "active" });
    const u = new URL(calls[0].url, "http://x");
    expect(u.pathname).toBe("/api/method/arbor.list_process_runs");
    expect(u.searchParams.get("sheet")).toBe("S1");
    expect(u.searchParams.get("stage_idx")).toBe("1");
    expect(u.searchParams.get("status")).toBe("active");
  });

  it("listProcessRuns omits absent filters", async () => {
    const { calls } = mockFetch([]);
    await api.listProcessRuns!("S1");
    const u = new URL(calls[0].url, "http://x");
    expect(u.searchParams.get("sheet")).toBe("S1");
    expect(u.searchParams.has("stage_idx")).toBe(false);
    expect(u.searchParams.has("status")).toBe(false);
  });

  it("inbox GETs the cross-sheet notification feed", async () => {
    const items: InboxItem[] = [
      {
        name: "n1",
        sheet: "S1",
        event_type: "COMMENT_ADDED",
        message: "alice commented",
        requires_ack: false,
        acked: false,
        node: "N1",
      },
    ];
    const { calls } = mockFetch(items);
    const out = await api.inbox!();
    expect(out).toEqual(items);
    expect(new URL(calls[0].url, "http://x").pathname).toBe("/api/method/arbor.inbox");
  });
});
