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
  // Feature (comments) — per-cell comment summary keyed by column name (sparse:
  // only cells with >=1 comment appear). Read-ACL filtered server-side, so a
  // column the viewer can't read never surfaces here. Powers the cell glyph's
  // count / unread dot with zero extra round-trips (parallel to `pending`).
  comments?: Record<string, CellCommentSummary>;
  // ACL hint: may the viewer change this node's structure (add/move/delete)?
  can_change_structure: boolean;
};

// The per-cell comment rollup carried in the snapshot (never the bodies). `open`
// / `resolved` are thread counts on the cell; `unread` is comments newer than
// the viewer's last-seen marker (session-local in v1).
export type CellCommentSummary = {
  open: number;
  resolved: number;
  unread: number;
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
    // platform-admin hint — the ONLY gate for the admin Roles panel (Feature: roles)
    is_admin?: boolean;
    // the viewer's own sheet subscription state (for the subscribe/unsubscribe control)
    subscribed?: boolean;
    subscription?: string | null;
    // active branch delegations on this sheet (for the delegation control)
    branch_grants?: BranchGrantView[];
    // Impersonation hints (Feature: act-as). `impersonating` is true when the
    // real (admin) session is currently acting AS another user; `effective_user`
    // is the identity ACL runs against (== snapshot.actor), `real_user` the truly
    // -authenticated admin. The ImpersonationBar renders the "acting as … — Stop"
    // banner off these; the server never re-derives ACL from them.
    impersonating?: boolean;
    real_user?: string | null;
    effective_user?: string | null;
  };
};

// Draft flow — a single server-persisted cell draft from the actor's personal
// draft box (Phase 1 endpoints). A non-owner edit writes a draft (local value
// visible immediately, surviving reload / device change) instead of instantly
// filing a Change Request; the draft box is later submitted as ONE multi-change
// CR. `value` is the proposed new value; `base_version` is the optimistic-
// concurrency base captured at save time (parallels updateCell's base_version).
export type CellDraft = {
  name: string;
  node: string;
  column: string;
  value: unknown;
  base_version?: number;
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
      // Raw outcome payload (outcome.data) when present — additive/back-compat.
      // The workspace agent's createSheet returns {sheet: <id>} here, which the
      // list-page dock reads to refresh the catalog + surface an "open" CTA.
      data?: Record<string, unknown>;
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

// ---- role management (Feature: roles) -------------------------------------
// An Arbor Role from arbor.list_roles, with per-viewer flags. The "request a
// role" picker filters to applicable && active && !viewer_holds &&
// !viewer_has_open_application (the server enforces this too).
export type RoleView = {
  role: string;
  label: string;
  description?: string | null;
  applicable: boolean;
  active: boolean;
  viewer_holds: boolean;
  viewer_has_open_application: boolean;
};

// One user account row from arbor.admin.list_users (the Admin modal's Users
// tab — a platform-admin-only surface). `creation` is the signup timestamp
// string; `is_admin`/`enabled` are the two flags set_user toggles.
export type UserRow = {
  email: string;
  full_name: string;
  is_admin: boolean;
  enabled: boolean;
  creation: string;
};

// An active role grant from arbor.list_role_grants (admin panel).
export type RoleGrantView = {
  name: string;
  role: string;
  grantee: string;
  granted_by: string;
  source: string;
  can_revoke: boolean;
};

// A role application from arbor.list_role_applications (admin inbox / my apps).
export type RoleApplicationView = {
  name: string;
  role: string;
  requester: string;
  status: "proposed" | "approved" | "rejected" | "withdrawn";
  justification?: string | null;
  decided_by?: string | null;
  viewer_is_approver?: boolean;
};

// One entry in the Activity / change-history feed from arbor.list_activity
// (newest-first). The feed is "what happened", never the data: it NEVER carries
// raw cell values, and a column the viewer cannot read is omitted (server-side
// read-ACL) — `column` is the readable column LABEL or null, `node` the visible
// node LABEL or null, and `summary` is the server-built human one-liner.
export type ActivityEvent = {
  event_id: string;
  // one of the 11 server EventType values (e.g. CELL_UPDATED, CHANGE_PROPOSED).
  type: string;
  actor: string;
  actor_type: string;
  // ISO/creation timestamp string.
  timestamp: string;
  // present when the event targets/produced a Change Request, else null.
  change_request: string | null;
  // visible node label (null when the event isn't node-scoped or is hidden).
  node: string | null;
  // readable column label (null when no column, or the viewer can't read it).
  column: string | null;
  // human one-liner, e.g. "alice updated the Stage of SSO Federation".
  summary: string;
  // The truly-authenticated principal when this event was produced under
  // impersonation (else null/absent). When present and != actor, the Activity
  // feed renders a subtle "via <real_user>" affix so the audit trail is legible.
  real_user?: string | null;
};

// The paged result of arbor.list_activity (NEW object shape; was a bare list).
// `events` is the newest-first page; `next_cursor` is an OPAQUE keyset token to
// pass back as `before` for the page of strictly-OLDER events, or null when no
// older events remain (so the UI hides "Load older").
export type ActivityPage = {
  events: ActivityEvent[];
  next_cursor: string | null;
};

// A sheet summary row from arbor.list_sheets — the home-page Sheet List. Sorted
// (client-side) by node_count desc so real sheets float above the many orphan
// empty test sheets; the count is shown per row.
export type SheetSummary = {
  name: string;
  structural_owner: string;
  node_count: number;
};

// ---- impersonation (Feature: act-as) --------------------------------------
// The auth-gate + banner signal from arbor.auth.whoami. `user` is the EFFECTIVE
// identity (the impersonated user while acting-as, else the real session user);
// `real_user` is the truly-authenticated principal (present + != user only under
// impersonation); `authenticated` is false for a Guest (drives the login gate).
export type Whoami = {
  user: string;
  real_user?: string | null;
  impersonating?: boolean;
  authenticated: boolean;
  redirect_to?: string | null;
};

// ---- per-cell comments (Feature: comments) --------------------------------
// One comment in a cell's thread, as returned by arbor.list_cell_comments
// (ordered creation-asc; grouped by thread_root client-side). `thread_root` is
// null on a root comment, else the root's name; `parent_comment` is the direct
// reply target. `can_resolve`/`can_delete` are DISPLAY-only server hints (the
// shim re-enforces authority) — never re-derive ACL from them.
export type CellComment = {
  name: string;
  thread_root: string | null;
  parent_comment: string | null;
  author: string;
  body: string;
  mentions: string[];
  resolved: boolean;
  resolved_by: string | null;
  resolved_at: string | null;
  timestamp: string;
  can_resolve: boolean;
  can_delete: boolean;
};

// ---- process DAG / SLA / inbox (Feature: process) --------------------------
// The trigger kind of a rule. 'row' = a node create/update (the canvas START
// node); 'column' = a specific trigger_column create/update.
export type ProcessTriggerKind = "row" | "column";
// When a trigger fires: on first fill, on any later change, or either.
export type ProcessTriggerOp = "created" | "updated" | "created-or-updated";

// One trigger->expectation rule in a process definition (from arbor.get_process).
// "On <trigger>: expect (expected_columns — an 'and' set) filled within
// within_seconds". `rule_key` is the stable id an Expectation/edge points back to
// (NOT the presentation `idx`). `trigger_column` is set iff trigger_kind='column'.
// `expected_owners` maps expected column -> LIVE-resolved owner (server-side; null
// when the viewer can't read that column). `*_label` are readable labels (null =>
// redacted for a column the viewer cannot read; never leak the field key).
// How a column trigger's SET fires: 'any' (fire when ANY named trigger column is
// updated — today's behavior / a single trigger) or 'all' (an AND-join / fan-in:
// fire ONCE when EVERY trigger column is filled). `trigger_column` remains a
// back-compat alias == `trigger_columns[0]`.
export type ProcessTriggerJoin = "any" | "all";

export type ProcessRuleView = {
  rule_key: string;
  idx: number;
  trigger_kind: ProcessTriggerKind;
  trigger_column: string | null;
  trigger_column_label: string | null;
  // The trigger SET (1+); `*_labels` null => redacted for an unreadable column.
  trigger_columns: (string | null)[];
  trigger_labels: (string | null)[];
  trigger_join: ProcessTriggerJoin;
  trigger_op: ProcessTriggerOp;
  expected_columns: string[];
  expected_labels: (string | null)[];
  within_seconds: number;
  notify_on_expect: boolean;
  label: string | null;
  expected_owners?: Record<string, string | null>;
};

// One trigger->expectation rule in the write payload (defineProcess). Only
// `trigger_kind`, `trigger_op`, and a non-empty `expected_columns` are required;
// `trigger_column` is required iff trigger_kind='column'. `rule_key` is optional
// (the server mints a stable one when omitted). `within_seconds` 0/absent = no SLA.
export type ProcessRuleInput = {
  rule_key?: string;
  trigger_kind: ProcessTriggerKind;
  // Either the single `trigger_column` (back-compat) OR the full `trigger_columns`
  // SET. `trigger_join` defaults to 'any' (server normalizes a single column to a
  // one-entry set). Set `trigger_join='all'` for an AND-join / fan-in trigger.
  trigger_column?: string | null;
  trigger_columns?: string[];
  trigger_join?: ProcessTriggerJoin;
  trigger_op: ProcessTriggerOp;
  expected_columns: string[];
  within_seconds?: number;
  notify_on_expect?: boolean;
  label?: string;
};

// A process definition + enabled state for a sheet (arbor.get_process).
export type ProcessDef = {
  sheet: string;
  title: string | null;
  enabled: boolean;
  row_scope: string;
  rules: ProcessRuleView[];
};

// One column entry in a SheetDefinition (arbor.get_sheet_definition). This is the
// SCHEMA/governance view of a column — id + label + type + owner + editors, plus
// the viewer's `can_edit` ACL hint — and carries NO cell values. `name` is the
// column ID (what defineProcess / updateColumn take); `field` is the storage key.
// Read-ACL filtered server-side: a column the viewer cannot read is omitted.
export type SheetDefinitionColumn = {
  name: string;
  field: string;
  label: string;
  type: ColumnType;
  column_owner: string;
  editors: string[];
  is_label: boolean;
  options?: SelectOptions | null;
  // ACL hint — is the viewer one of this column's approvers (owner or editor)?
  can_edit: boolean;
};

// The cheap schema/config (governance) READ for a sheet (arbor.get_sheet_definition
// → the `getSheetDefinition` capability). NO row/cell data — columns + process only.
// The SINGLE source both the unified Sheet Settings panel and the LLM agent read,
// so the human surface and the agent can never diverge. Prefer this over
// getSheetSnapshot when you only need the schema/governance (e.g. mapping a column
// LABEL to its ID). The `process` block reuses the get_process rule-view shape
// (read-ACL-redacted labels + live-resolved owners), or is null.
export type SheetDefinition = {
  sheet: {
    name: string;
    title: string;
    structural_owner: string;
    label_column: string | null;
    settings: Record<string, unknown>;
  };
  columns: SheetDefinitionColumn[];
  process: {
    enabled: boolean;
    row_scope: string;
    rules: ProcessRuleView[];
  } | null;
};

// One aggregate edge (trigger -> expected column) for the flow dashboard
// (process_dashboard). Each rule with N expected columns contributes N edges that
// share `rule_key`. `from_column` is null for a row (START) trigger. `*_label`
// null => redacted for a column the viewer cannot read.
export type ProcessDashboardEdge = {
  rule_key: string;
  from_kind: ProcessTriggerKind;
  from_column: string | null;
  from_label: string | null;
  to_column: string;
  to_label: string | null;
  // 'all' => this edge is one leg of an AND-join (fan-in); edges sharing a
  // `rule_key` with join='all' group into one join node on the canvas. 'any' =>
  // a plain single/any trigger edge.
  join: ProcessTriggerJoin;
  within_seconds: number;
  pending_count: number;
  breached_count: number;
  satisfied_count: number;
  avg_open_to_satisfy_seconds: number | null;
};

// The dashboard aggregate over Process Run + Expectation rows (process_dashboard).
export type ProcessDashboard = {
  edges: ProcessDashboardEdge[];
  total_active: number;
  total_completed: number;
  throughput: number;
};

// One open/closed expectation under a run (arbor.list_process_runs) — one per
// (rule_key, expected_column). Run rows never carry cell VALUES (structural
// position only). `to_label` null => redacted for an unreadable column.
export type ProcessRunExpectation = {
  rule_key: string;
  expected_column: string;
  to_label: string | null;
  opened_at: string | null;
  satisfied_at: string | null;
  due_at: string | null;
  breached: boolean;
};

// One per-row process run (arbor.list_process_runs) — a node's position in the
// process DAG, carrying its expectation ledger.
export type ProcessRun = {
  name: string;
  process: string;
  sheet: string;
  node: string;
  status: "active" | "completed" | "abandoned";
  started_at: string;
  completed_at: string | null;
  expectations: ProcessRunExpectation[];
};

// One row in the viewer's cross-sheet Inbox (arbor.inbox) — a superset of a
// per-sheet NotificationView carrying the source sheet + an optional node deep
// link. `event_type` may be a display string (e.g. "COMMENT_ADDED") that is NOT
// one of the 11 server EventTypes; the UI renders it via NotificationItem.
export type InboxItem = {
  name: string;
  sheet: string;
  event_type: string;
  message: string;
  requires_ack: boolean;
  acked: boolean;
  node?: string | null;
};

// ---- notification webhooks (Feature: webhooks) -----------------------------
// A sheet-admin-registered Webhook Endpoint (from the webhook registration
// surface). The signing `secret` is WRITE-ONLY: it is returned ONCE at register/
// rotate time and NEVER included in a list read (hence optional here). `scope` +
// `target` mirror the Tree Event matcher; `notification_sources` subscribes the
// endpoint to non-tree-event fan-out sources (comment/process/sla/change_request).
export type WebhookEndpointView = {
  name: string;
  label: string | null;
  url: string;
  active: boolean;
  sheet: string | null;
  owner_user: string | null;
  scope: "sheet" | "branch" | "column";
  target: string | null;
  event_types: string[];
  notification_sources: string[];
  secret?: string;
};

// One Webhook Delivery attempt-log row (from the delivery audit read). `source`
// discriminates the payload origin; exactly one of `tree_event`/`notification` is
// set accordingly. `event_id` is the per-(endpoint, event_id) idempotency key.
export type WebhookDeliveryView = {
  name: string;
  endpoint: string;
  source: "tree_event" | "comment" | "process" | "sla" | "change_request";
  tree_event: string | null;
  notification: string | null;
  event_id: string | null;
  status: "pending" | "delivered" | "failed" | "exhausted";
  attempts: number;
  signature: string | null;
  next_retry_at: string | null;
  last_response: string | null;
};

export type ArborClient = {
  executeAction: (actionId: string, params: Record<string, unknown>) => Promise<Outcome>;
  getSheetSnapshot: (sheet: string) => Promise<Snapshot>;
  // The catalog of sheets (name + owner + node_count) for the home page.
  // Optional so test/mocked clients need not implement it.
  listSheets?: () => Promise<SheetSummary[]>;
  // Create a new sheet (a standalone whitelisted mutation, NOT a registry
  // capability — a sheet has no per-sheet ACL yet). Any authenticated non-Guest
  // user may create one; the creator becomes its structural_owner. Resolves to
  // the created sheet's name; rejects on a duplicate (409). Optional so mocked
  // clients can omit it.
  createSheet?: (name: string, title?: string) => Promise<{ sheet: string }>;
  // The sheet's Change Requests (default proposed) for the review inbox.
  // Optional so test/mocked clients need not implement it.
  listChangeRequests?: (sheet: string) => Promise<ChangeRequestView[]>;
  // The viewer's in-app notifications for the sheet. Optional (mocked clients).
  listNotifications?: (sheet: string) => Promise<NotificationView[]>;
  // The sheet's Activity / change-history feed (newest-first), paged via keyset.
  // Returns { events, next_cursor }; pass next_cursor back as `before` for the
  // older page. Optional `type`/`actor` filters AND-combine with the sheet scope.
  // Optional so mocked clients can omit it.
  listActivity?: (
    sheet: string,
    opts?: { limit?: number; before?: string; type?: string; actor?: string },
  ) => Promise<ActivityPage>;
  // Role management reads (Feature: roles). Optional so mocked clients can omit.
  listRoles?: () => Promise<RoleView[]>;
  listRoleGrants?: (role?: string, grantee?: string) => Promise<RoleGrantView[]>;
  listRoleApplications?: (status?: string, requester?: string) => Promise<RoleApplicationView[]>;
  // Platform-admin console (Admin modal). These hit the STANDALONE arbor.admin.*
  // endpoints — a platform-admin face (like internalReset), NEVER registry
  // capabilities, so the LLM agent cannot reach them. The server admin-gates
  // every one; set_user additionally self-guards (an admin cannot demote or
  // disable their own account). All optional so mocked clients can omit them.
  createRole?: (params: {
    role: string;
    label: string;
    description?: string;
    applicable?: boolean;
  }) => Promise<RoleView>;
  updateRole?: (params: {
    role: string;
    label?: string;
    description?: string;
    applicable?: boolean;
    active?: boolean;
  }) => Promise<RoleView>;
  listUsers?: () => Promise<UserRow[]>;
  setUser?: (params: { email: string; is_admin?: boolean; enabled?: boolean }) => Promise<UserRow>;
  // Draft flow (Phase 1 server-persisted draft box). All scoped to the actor's
  // OWN drafts; the server enforces the actor scope. Optional so test/mocked
  // clients can implement only the subset they exercise.
  //  * listCellDrafts   — the actor's open drafts for a sheet (hydrate on mount /
  //    refetch; drafts SURVIVE a refetch — only a submit clears them).
  //  * saveCellDraft    — upsert ONE cell's draft (idempotent per (node,column));
  //    resolves to the draft's name.
  //  * discardCellDraft — drop a single cell's draft.
  //  * discardCellDrafts— drop ALL of the sheet's drafts (returns the count).
  //  * submitCellDrafts — convert the whole draft box into ONE multi-change
  //    suggestChanges CR (Outcome envelope; empty box → {kind:"read"}); the
  //    server deletes the submitted drafts.
  listCellDrafts?: (sheet: string) => Promise<CellDraft[]>;
  saveCellDraft?: (
    sheet: string,
    node: string,
    column: string,
    value: unknown,
    base_version?: number,
  ) => Promise<{ name: string }>;
  discardCellDraft?: (sheet: string, node: string, column: string) => Promise<{ ok: boolean }>;
  discardCellDrafts?: (sheet: string) => Promise<{ discarded: number }>;
  submitCellDrafts?: (sheet: string) => Promise<Outcome>;
  // Impersonation (Feature: act-as). `whoami` powers BOTH the login gate and the
  // banner; begin/end are governed capabilities routed through execute_action
  // (admin-gated server-side). Optional so mocked clients can omit them.
  whoami?: () => Promise<Whoami>;
  beginImpersonation?: (user: string, reason?: string) => Promise<Outcome>;
  endImpersonation?: () => Promise<Outcome>;
  // Per-cell comments (Feature: comments). Read (list) is a GET; the writes
  // funnel through post(). `reopen` is resolve with resolved=false. All optional
  // so a mocked client implements only the subset it exercises.
  listCellComments?: (sheet: string, node: string, column: string) => Promise<CellComment[]>;
  addCellComment?: (
    sheet: string,
    node: string,
    column: string,
    body: string,
    opts?: { parent_comment?: string; mentions?: string[] },
  ) => Promise<{ name: string; thread_root: string | null; mentions: string[] }>;
  resolveCellComment?: (comment: string) => Promise<{ ok: boolean }>;
  reopenCellComment?: (comment: string) => Promise<{ ok: boolean }>;
  deleteCellComment?: (comment: string) => Promise<{ ok: boolean }>;
  // Process / SLA / inbox (Feature: process). define/enable/disable/start are
  // governed capabilities via execute_action (structural-owner gated; a non-owner
  // define auto-routes to a Change Request); the rest are read GET shims. All
  // optional so mocked clients implement only what they use.
  defineProcess?: (
    sheet: string,
    rules: ProcessRuleInput[],
    opts?: { title?: string; row_scope?: string },
  ) => Promise<Outcome>;
  enableProcess?: (sheet: string) => Promise<Outcome>;
  disableProcess?: (sheet: string) => Promise<Outcome>;
  startProcessRun?: (sheet: string, node: string) => Promise<Outcome>;
  getProcess?: (sheet: string) => Promise<ProcessDef>;
  // The cheap schema/config (governance) read — columns + process, NO rows. The
  // unified Sheet Settings surface reads this ONE endpoint (same one the agent
  // uses) to render the column list + current process without a whole snapshot.
  // Optional so mocked clients implement only what they exercise.
  getSheetDefinition?: (sheet: string) => Promise<SheetDefinition>;
  processDashboard?: (sheet: string) => Promise<ProcessDashboard>;
  listProcessRuns?: (
    sheet: string,
    opts?: { rule_key?: string; column?: string; status?: string },
  ) => Promise<ProcessRun[]>;
  // The viewer's cross-sheet in-app notifications (the Inbox page). Self-scoped
  // server-side to the actor.
  inbox?: () => Promise<InboxItem[]>;
  // Notification webhooks (Feature: webhooks) — a sheet-admin/structural-owner
  // surface. register returns the endpoint view + the signing `secret` ONCE (it is
  // never echoed by list); update never rotates the secret; test fires a signed
  // ping through the real delivery engine. All optional so mocked clients
  // implement only what they exercise; the server SSRF-validates + admin-gates.
  registerWebhook?: (params: {
    url: string;
    sheet?: string | null;
    label?: string;
    notification_sources?: string[];
    event_types?: string[];
    scope?: "sheet" | "branch" | "column";
    target?: string | null;
  }) => Promise<WebhookEndpointView>;
  listWebhooks?: (sheet?: string) => Promise<WebhookEndpointView[]>;
  updateWebhook?: (
    endpoint: string,
    patch: {
      url?: string;
      label?: string;
      active?: boolean;
      notification_sources?: string[];
      event_types?: string[];
    },
  ) => Promise<WebhookEndpointView>;
  deleteWebhook?: (endpoint: string) => Promise<{ ok: boolean }>;
  testWebhook?: (endpoint: string) => Promise<{ delivery: string | null; status: string | null }>;
  // Streams Re-Act frames; onFrame is invoked per parsed frame. Resolves when
  // the stream completes (final frame). The default reads an NDJSON body.
  // `sheet` is nullable: a falsy sheet is a WORKSPACE session (the sheet-less
  // list-page agent) — the backend treats an absent sheet as workspace mode.
  agentChat: (
    sheet: string | null | undefined,
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

  listActivity: async (sheet, opts) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet, limit: String(opts?.limit ?? 50) });
    // Opaque keyset cursor for the older page; filters AND-combine with the scope.
    if (opts?.before) qs.set("before", opts.before);
    if (opts?.type) qs.set("type", opts.type);
    if (opts?.actor) qs.set("actor", opts.actor);
    const res = await fetchImpl(`/api/method/arbor.list_activity?${qs.toString()}`, { headers });
    if (!res.ok) throw new Error(`list_activity failed: ${res.status}`);
    return unwrap<ActivityPage>(await res.json());
  },

  listRoles: async () => {
    const headers = await authHeaderProvider();
    const res = await fetchImpl(`/api/method/arbor.list_roles`, { headers });
    if (!res.ok) throw new Error(`list_roles failed: ${res.status}`);
    return unwrap<RoleView[]>(await res.json());
  },

  listRoleGrants: async (role, grantee) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams();
    if (role) qs.set("role", role);
    if (grantee) qs.set("grantee", grantee);
    const res = await fetchImpl(`/api/method/arbor.list_role_grants?${qs.toString()}`, { headers });
    if (!res.ok) throw new Error(`list_role_grants failed: ${res.status}`);
    return unwrap<RoleGrantView[]>(await res.json());
  },

  listRoleApplications: async (status, requester) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams();
    if (status !== undefined) qs.set("status", status);
    if (requester) qs.set("requester", requester);
    const res = await fetchImpl(`/api/method/arbor.list_role_applications?${qs.toString()}`, { headers });
    if (!res.ok) throw new Error(`list_role_applications failed: ${res.status}`);
    return unwrap<RoleApplicationView[]>(await res.json());
  },

  // Platform-admin console (Admin modal) — the standalone arbor.admin.* face.
  // Mutations funnel through post() (throws with status like every write);
  // list_users is a GET mirroring the listRoles header pattern.
  createRole: (params) => post<RoleView>("arbor.admin.create_role", params),

  updateRole: (params) => post<RoleView>("arbor.admin.update_role", params),

  listUsers: async () => {
    const headers = await authHeaderProvider();
    const res = await fetchImpl(`/api/method/arbor.admin.list_users`, { headers });
    if (!res.ok) throw new Error(`admin.list_users failed: ${res.status}`);
    return unwrap<UserRow[]>(await res.json());
  },

  setUser: (params) => post<UserRow>("arbor.admin.set_user", params),

  listSheets: async () => {
    const headers = await authHeaderProvider();
    const res = await fetchImpl(`/api/method/arbor.list_sheets`, { headers });
    if (!res.ok) throw new Error(`list_sheets failed: ${res.status}`);
    return unwrap<SheetSummary[]>(await res.json());
  },

  // Standalone whitelisted mutation (NOT execute_action): the server creates the
  // Tree Sheet + a default LABEL column and makes the caller its structural_owner.
  // A duplicate name yields a 409/ValidationError → post() throws, which the
  // SheetList form catches to show a graceful message.
  createSheet: (name, title) =>
    post<{ sheet: string }>("arbor.create_sheet", { name, title }),

  getSheetSnapshot: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.get_sheet_snapshot?${qs}`, { headers });
    if (!res.ok) throw new Error(`snapshot failed: ${res.status}`);
    return unwrap<Snapshot>(await res.json());
  },

  // Draft flow — listCellDrafts is a GET (mirrors listChangeRequests' headers +
  // ?qs pattern); the mutations funnel through `post` like every other write.
  listCellDrafts: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.list_cell_drafts?${qs}`, { headers });
    if (!res.ok) throw new Error(`list_cell_drafts failed: ${res.status}`);
    return unwrap<CellDraft[]>(await res.json());
  },

  saveCellDraft: (sheet, node, column, value, base_version) =>
    post<{ name: string }>("arbor.save_cell_draft", {
      sheet,
      node,
      column,
      value,
      // Only thread base_version when the caller captured one (an empty cell or a
      // server that doesn't track versions yet sends none).
      ...(base_version === undefined ? {} : { base_version }),
    }),

  discardCellDraft: (sheet, node, column) =>
    post<{ ok: boolean }>("arbor.discard_cell_draft", { sheet, node, column }),

  discardCellDrafts: (sheet) =>
    post<{ discarded: number }>("arbor.discard_cell_drafts", { sheet }),

  submitCellDrafts: (sheet) => post<Outcome>("arbor.submit_cell_drafts", { sheet }),

  // Impersonation — whoami is a GET (mirrors the listNotifications header pattern
  // but with no query); begin/end funnel through execute_action like every
  // governed mutation (the server admin-gates them off the REAL user).
  whoami: async () => {
    const headers = await authHeaderProvider();
    const res = await fetchImpl(`/api/method/arbor.auth.whoami`, { headers });
    if (!res.ok) throw new Error(`whoami failed: ${res.status}`);
    return unwrap<Whoami>(await res.json());
  },

  // Use the DEDICATED shims (not generic execute_action): they authorize as the
  // REAL session user, so `end` works while impersonating (the generic path builds
  // the EFFECTIVE — impersonated, non-admin — actor and the executor would reject).
  beginImpersonation: (user, reason) =>
    post<Outcome>("arbor.begin_impersonation", {
      impersonated_user: user,
      ...(reason === undefined ? {} : { reason }),
    }),

  endImpersonation: () => post<Outcome>("arbor.end_impersonation", {}),

  // Comments — list is a GET (sheet/node/column qs, mirroring list_cell_drafts);
  // the writes funnel through post(). reopen is resolve with resolved=false.
  listCellComments: async (sheet, node, column) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet, node, column }).toString();
    const res = await fetchImpl(`/api/method/arbor.list_cell_comments?${qs}`, { headers });
    if (!res.ok) throw new Error(`list_cell_comments failed: ${res.status}`);
    return unwrap<CellComment[]>(await res.json());
  },

  addCellComment: (sheet, node, column, body, opts) =>
    post<{ name: string; thread_root: string | null; mentions: string[] }>(
      "arbor.add_cell_comment",
      {
        sheet,
        node,
        column,
        body,
        // Only thread parent/mentions when supplied — a root FYI comment sends
        // neither (the server derives thread_root + parses @mentions from body).
        ...(opts?.parent_comment === undefined ? {} : { parent_comment: opts.parent_comment }),
        ...(opts?.mentions === undefined ? {} : { mentions: opts.mentions }),
      },
    ),

  resolveCellComment: (comment) =>
    post<{ ok: boolean }>("arbor.resolve_cell_comment", { comment, resolved: true }),

  reopenCellComment: (comment) =>
    post<{ ok: boolean }>("arbor.resolve_cell_comment", { comment, resolved: false }),

  deleteCellComment: (comment) =>
    post<{ ok: boolean }>("arbor.delete_cell_comment", { comment }),

  // Process — define/enable/disable/start are governed caps via execute_action;
  // the read shims mirror the listActivity/listNotifications GET+qs pattern.
  defineProcess: (sheet, rules, opts) =>
    post<Outcome>("arbor.execute_action", {
      action_id: "defineProcess",
      params: {
        sheet,
        rules,
        ...(opts?.title === undefined ? {} : { title: opts.title }),
        ...(opts?.row_scope === undefined ? {} : { row_scope: opts.row_scope }),
      },
    }),

  enableProcess: (sheet) =>
    post<Outcome>("arbor.execute_action", { action_id: "enableProcess", params: { sheet } }),

  disableProcess: (sheet) =>
    post<Outcome>("arbor.execute_action", { action_id: "disableProcess", params: { sheet } }),

  startProcessRun: (sheet, node) =>
    post<Outcome>("arbor.execute_action", {
      action_id: "startProcessRun",
      params: { sheet, node },
    }),

  getProcess: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.get_process?${qs}`, { headers });
    if (!res.ok) throw new Error(`get_process failed: ${res.status}`);
    return unwrap<ProcessDef>(await res.json());
  },

  // getSheetDefinition is a registry READ capability, so the endpoint returns the
  // standard Outcome envelope ({kind:"read", data:<definition>}); unwrap .data.
  getSheetDefinition: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.get_sheet_definition?${qs}`, { headers });
    if (!res.ok) throw new Error(`get_sheet_definition failed: ${res.status}`);
    return unwrap<{ kind: string; data: SheetDefinition }>(await res.json()).data;
  },

  processDashboard: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet }).toString();
    const res = await fetchImpl(`/api/method/arbor.process_dashboard?${qs}`, { headers });
    if (!res.ok) throw new Error(`process_dashboard failed: ${res.status}`);
    return unwrap<ProcessDashboard>(await res.json());
  },

  listProcessRuns: async (sheet, opts) => {
    const headers = await authHeaderProvider();
    const qs = new URLSearchParams({ sheet });
    if (opts?.rule_key !== undefined) qs.set("rule_key", opts.rule_key);
    if (opts?.column !== undefined) qs.set("column", opts.column);
    if (opts?.status !== undefined) qs.set("status", opts.status);
    const res = await fetchImpl(`/api/method/arbor.list_process_runs?${qs.toString()}`, { headers });
    if (!res.ok) throw new Error(`list_process_runs failed: ${res.status}`);
    return unwrap<ProcessRun[]>(await res.json());
  },

  inbox: async () => {
    const headers = await authHeaderProvider();
    const res = await fetchImpl(`/api/method/arbor.inbox`, { headers });
    if (!res.ok) throw new Error(`inbox failed: ${res.status}`);
    return unwrap<InboxItem[]>(await res.json());
  },

  // Webhook registration surface — register/update/test/delete funnel through
  // post(); list is a GET (mirrors listNotifications' header + ?qs pattern). The
  // server admin-gates + SSRF-validates; a 4xx surfaces as a thrown Error.
  registerWebhook: (params) => post<WebhookEndpointView>("arbor.register_webhook", params),
  listWebhooks: async (sheet) => {
    const headers = await authHeaderProvider();
    const qs = sheet ? `?${new URLSearchParams({ sheet }).toString()}` : "";
    const res = await fetchImpl(`/api/method/arbor.list_webhooks${qs}`, { headers });
    if (!res.ok) throw new Error(`list_webhooks failed: ${res.status}`);
    return unwrap<WebhookEndpointView[]>(await res.json());
  },
  updateWebhook: (endpoint, patch) =>
    post<WebhookEndpointView>("arbor.update_webhook", { endpoint, ...patch }),
  deleteWebhook: (endpoint) => post<{ ok: boolean }>("arbor.delete_webhook", { endpoint }),
  testWebhook: (endpoint) =>
    post<{ delivery: string | null; status: string | null }>("arbor.test_webhook", { endpoint }),

  agentChat: async (sheet, message, onFrame) => {
    const headers = {
      "Content-Type": "application/json",
      ...(await authHeaderProvider()),
    };
    const res = await fetchImpl("/api/method/arbor.agent.chat", {
      method: "POST",
      headers,
      // A falsy sheet is sent as null → the backend selects the workspace agent.
      body: JSON.stringify({ sheet: sheet || null, message }),
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
          const obs = (e.observation ?? {}) as {
            kind?: OutcomeKind;
            change_request?: string;
            data?: Record<string, unknown>;
          };
          onFrame({
            type: "observation",
            outcome: obs.kind ?? "read",
            change_request: obs.change_request,
            data: obs.data,
          });
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
