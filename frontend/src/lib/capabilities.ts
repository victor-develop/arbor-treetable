// Capability ids mirror the server registry (CAPABILITIES.md). The UI never
// re-implements ACL or mutation — it only needs the stable action ids to call
// executeAction, and the LLM-exposure flag to render the "what can the agent do"
// affordance (WEB_UI-070). The server registry remains the single source of
// truth; this is a thin display/dispatch mirror, not a second registry.

export type CapabilityId =
  | "getSheetSnapshot"
  | "addNode"
  | "updateCell"
  | "moveNode"
  | "deleteNode"
  | "addColumn"
  | "updateColumn"
  | "deleteColumn"
  | "suggestChange"
  | "approveChange"
  | "rejectChange"
  | "withdrawChange"
  | "subscribe"
  | "unsubscribe"
  | "acknowledge"
  | "delegateBranch"
  | "revokeDelegation"
  | "grantColumn"
  | "internalReset"
  | "assignRole"
  | "revokeRole"
  | "applyForRole"
  | "approveRoleApplication"
  | "rejectRoleApplication"
  | "withdrawRoleApplication";

export type CapabilityMeta = {
  id: CapabilityId;
  name: string;
  // mirrors registry is_exposed_to_llm; only internalReset is false.
  is_exposed_to_llm: boolean;
};

export const CAPABILITIES: CapabilityMeta[] = [
  { id: "getSheetSnapshot", name: "Get sheet snapshot", is_exposed_to_llm: true },
  { id: "addNode", name: "Add node", is_exposed_to_llm: true },
  { id: "updateCell", name: "Update cell value", is_exposed_to_llm: true },
  { id: "moveNode", name: "Move node", is_exposed_to_llm: true },
  { id: "deleteNode", name: "Delete node", is_exposed_to_llm: true },
  { id: "addColumn", name: "Add column", is_exposed_to_llm: true },
  { id: "updateColumn", name: "Update column", is_exposed_to_llm: true },
  { id: "deleteColumn", name: "Delete column", is_exposed_to_llm: true },
  { id: "suggestChange", name: "Suggest change", is_exposed_to_llm: true },
  { id: "approveChange", name: "Approve change", is_exposed_to_llm: true },
  { id: "rejectChange", name: "Reject change", is_exposed_to_llm: true },
  { id: "withdrawChange", name: "Withdraw change", is_exposed_to_llm: true },
  { id: "subscribe", name: "Subscribe", is_exposed_to_llm: true },
  { id: "unsubscribe", name: "Unsubscribe", is_exposed_to_llm: true },
  { id: "acknowledge", name: "Acknowledge", is_exposed_to_llm: true },
  { id: "delegateBranch", name: "Delegate branch", is_exposed_to_llm: true },
  { id: "revokeDelegation", name: "Revoke delegation", is_exposed_to_llm: true },
  { id: "grantColumn", name: "Grant column", is_exposed_to_llm: true },
  { id: "internalReset", name: "Internal reset", is_exposed_to_llm: false },
  // role management (Feature: roles). Privilege-granting caps are hidden from the
  // agent (it must never self-escalate or approve a role); applyForRole +
  // withdrawRoleApplication stay exposed (they still require admin approval).
  { id: "assignRole", name: "Assign role", is_exposed_to_llm: false },
  { id: "revokeRole", name: "Revoke role", is_exposed_to_llm: false },
  { id: "applyForRole", name: "Apply for role", is_exposed_to_llm: true },
  { id: "approveRoleApplication", name: "Approve role application", is_exposed_to_llm: false },
  { id: "rejectRoleApplication", name: "Reject role application", is_exposed_to_llm: false },
  { id: "withdrawRoleApplication", name: "Withdraw role application", is_exposed_to_llm: true },
];

// The agent tool affordance (WEB_UI-070): excludes internalReset.
export function llmExposedCapabilities(): CapabilityMeta[] {
  return CAPABILITIES.filter((c) => c.is_exposed_to_llm);
}

const VALID_IDS = new Set(CAPABILITIES.map((c) => c.id));
export function isCapabilityId(id: string): id is CapabilityId {
  return VALID_IDS.has(id as CapabilityId);
}

// The closed set of column types the add-column form may offer (WEB_UI-053).
export const COLUMN_TYPES = [
  "text",
  "multiline-text",
  "number",
  "single-select-split",
  "multi-select-split",
] as const;
