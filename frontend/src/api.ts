// The thin client over Arbor's capability API. The React shell NEVER calls a
// mutation directly — everything funnels through executeAction / getSheetSnapshot
// / agentChat, mirroring the server's single executeAction path (ARCHITECTURE
// §4). Affordances (edit vs suggest) come from the snapshot's ACL hints.

export type OutcomeKind = "executed" | "suggested" | "read";

export type Outcome = {
  kind: OutcomeKind;
  // CR id present when the action was routed to an approver (suggested path).
  change_request?: string;
  // resolved approver + optional co-approvers (moveNode dual-end authority).
  resolved_approver?: string;
  co_approvers?: string[];
  event?: unknown;
  // optional server error code (e.g. VERSION_CONFLICT) surfaced without throwing.
  error?: string;
  result?: Record<string, unknown>;
  data?: Record<string, unknown>;
};

export type SnapshotColumn = {
  name: string;
  field: string;
  label: string;
  type: ColumnType;
  is_label: boolean;
  column_owner: string;
  editors: string[];
  // ACL hint from the server — the UI renders edit-vs-suggest from this, never
  // re-deriving ACL (ARCHITECTURE §4.3).
  can_edit: boolean;
  // schema config (optional; present for select-split columns)
  options?: SelectOptions | null;
  width?: number;
  editable?: boolean;
};

export type ColumnType =
  | "text"
  | "multiline-text"
  | "number"
  | "single-select-split"
  | "multi-select-split";

export type SelectOptions = {
  groups: { label: string; options: string[] }[];
};

export type SnapshotNode = {
  name: string;
  parent: string | null;
  lft: number;
  rgt: number;
  is_group?: boolean;
  idx?: number;
  label: string | null;
  values: Record<string, unknown>;
  // Feature 1 — per-cell stored version (parallel to values; 0 for an empty
  // cell). The FE threads versions[col] as the next write's base_version.
  versions?: Record<string, number>;
  // Per-cell pending suggestions — open Change Requests targeting this cell,
  // keyed by column name (sparse: only cells with >=1 pending appear). Server-
  // sourced, so the marker survives refresh AND is visible to every viewer who
  // can read the column (not just the session that filed the suggestion).
  pending?: Record<string, PendingMark[]>;
  // ACL hint: may the viewer change this node's structure (add/move/delete)?
  can_change_structure: boolean;
};

// One open suggestion (proposed Change Request) targeting a cell.
export type PendingMark = {
  change_request?: string;
  requester?: string;
  value?: unknown;
};

export type Snapshot = {
  sheet: {
    name: string;
    structural_owner: string;
    settings: Record<string, unknown>;
  };
  columns: SnapshotColumn[];
  nodes: SnapshotNode[];
  label_column: string | null;
  actor: string | null;
  // optional sheet-level affordances supplied by the server snapshot.
  viewer?: {
    can_add_column?: boolean;
    // the viewer's own sheet subscription state (for the subscribe/unsubscribe control)
    subscribed?: boolean;
    subscription?: string | null;
    // active branch delegations on this sheet (for the delegation control)
    branch_grants?: BranchGrantView[];
  };
};

// An active branch delegation as carried in the snapshot (delegation control).
export type BranchGrantView = {
  name: string;
  branch_root: string;
  grantee: string;
  granted_by: string;
  can_revoke: boolean;
};

// One streamed Re-Act frame from agent.chat (ARCHITECTURE §8). The sidebar is a
// thin shell: it renders frames in arrival order and owns zero mutation logic.
export type AgentFrame =
  | { type: "thought"; content: string }
  | {
      type: "action";
      tool: string;
      arguments: Record<string, unknown>;
    }
  | {
      type: "observation";
      outcome: OutcomeKind;
      change_request?: string;
      resolved_approver?: string;
    }
  | { type: "final"; content: string };

// Pluggable auth header hook. The open-source build returns {}; the employee
// SSO build overrides this with `Authorization: await getAuthorization()`
// (ARCHITECTURE §10 — isolation seam). Core never imports the SSO SDK.
export type AuthHeaderProvider = () => Promise<Record<string, string>>;
let authHeaderProvider: AuthHeaderProvider = async () => ({});
export function setAuthHeaderProvider(p: AuthHeaderProvider): void {
  authHeaderProvider = p;
}

// Pluggable fetch (overridable in tests). Defaults to the global fetch.
let fetchImpl: typeof fetch = (...args) => fetch(...args);
export function setFetchImpl(f: typeof fetch): void {
  fetchImpl = f;
}

async function post<T>(method: string, body: unknown): Promise<T> {
  const headers = {
    "Content-Type": "application/json",
    ...(await authHeaderProvider()),
  };
  const res = await fetchImpl(`/api/method/${method}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} failed: ${res.status}`);
  return unwrap<T>(await res.json());
}

// Frappe wraps every whitelisted-method return in `{ "message": <value> }`.
// Unwrap it so callers get the capability's actual payload. (Test mocks return
// the bare value, which passes through unchanged.)
function unwrap<T>(json: unknown): T {
  if (json && typeof json === "object" && "message" in (json as Record<string, unknown>)) {
    return (json as { message: T }).message;
  }
  return json as T;
}

// One change within a (multi-change) Change Request.
export type ChangeRequestItem = {
  action: string;
  target_kind?: string;
  operation?: string;
  payload?: Record<string, unknown>;
  resolved_approver?: string;
  item_approved?: boolean;
};

// A Change Request as returned by arbor.list_change_requests (review inbox).
export type ChangeRequestView = {
  name: string;
  requester: string;
  resolved_approver: string;
  status: "proposed" | "approved" | "rejected" | "withdrawn";
  target_kind?: string;
  operation?: string;
  payload?: Record<string, unknown>;
  changes?: ChangeRequestItem[];
  viewer_is_approver?: boolean;
};

// An in-app notification as returned by arbor.list_notifications.
export type NotificationView = {
  name: string;
  event_type: string;
  message: string;
  requires_ack: boolean;
  acked: boolean;
};

export type ArborClient = {
  executeAction: (actionId: string, params: Record<string, unknown>) => Promise<Outcome>;
  getSheetSnapshot: (sheet: string) => Promise<Snapshot>;
  // The sheet's Change Requests (default proposed) for the review inbox.
  // Optional so test/mocked clients need not implement it.
  listChangeRequests?: (sheet: string) => Promise<ChangeRequestView[]>;
  // The viewer's in-app notifications for the sheet. Optional (mocked clients).
  listNotifications?: (sheet: string) => Promise<NotificationView[]>;
  // Streams Re-Act frames; onFrame is invoked per parsed frame. Resolves when
  // the stream completes (final frame). The default reads an NDJSON body.
  agentChat: (
    sheet: string,
    message: string,
    onFrame: (frame: AgentFrame) => void,
  ) => Promise<void>;
};

export const api: ArborClient = {
  executeAction: (actionId, params) =>
    post<Outcome>("arbor.execute_action", { action_id: actionId, params }),

  listChangeRequests: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.list_change_requests?${qs}`, { headers });
    if (!res.ok) throw new Error(`list_change_requests failed: ${res.status}`);
    return unwrap<ChangeRequestView[]>(await res.json());
  },

  listNotifications: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.list_notifications?${qs}`, { headers });
    if (!res.ok) throw new Error(`list_notifications failed: ${res.status}`);
    return unwrap<NotificationView[]>(await res.json());
  },

  getSheetSnapshot: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.get_sheet_snapshot?${qs}`, { headers });
    if (!res.ok) throw new Error(`snapshot failed: ${res.status}`);
    return unwrap<Snapshot>(await res.json());
  },

  agentChat: async (sheet, message, onFrame) => {
    const headers = {
      "Content-Type": "application/json",
      ...(await authHeaderProvider()),
    };
    const res = await fetchImpl("/api/method/arbor.agent.chat", {
      method: "POST",
      headers,
      body: JSON.stringify({ sheet, message }),
    });
    if (!res.ok) throw new Error(`agent.chat failed: ${res.status}`);
    // The Frappe endpoint returns the whole Re-Act session as one JSON document
    // ({final_message, transcript[], ...}); replay its ordered transcript as the
    // Thought/Action/Observation/Final frames the sidebar renders.
    const session = unwrap<{
      transcript?: Array<Record<string, unknown>>;
      final_message?: string;
    }>(await res.json());
    let sawFinal = false;
    for (const e of session.transcript ?? []) {
      switch (e.kind) {
        case "thought":
          onFrame({ type: "thought", content: String(e.content ?? "") });
          break;
        case "action":
          onFrame({ type: "action", tool: String(e.tool ?? ""), arguments: (e.arguments ?? {}) as Record<string, unknown> });
          break;
        case "observation": {
          const obs = (e.observation ?? {}) as { kind?: OutcomeKind; change_request?: string };
          onFrame({ type: "observation", outcome: obs.kind ?? "read", change_request: obs.change_request });
          break;
        }
        case "final":
          sawFinal = true;
          // Some models end with a tool call and no closing prose; never render an
          // empty final (an empty node is invisible) — fall back to a summary.
          onFrame({
            type: "final",
            content: String(e.content || session.final_message || "Done."),
          });
          break;
      }
    }
    if (!sawFinal) onFrame({ type: "final", content: session.final_message || "Done." });
  },
};
