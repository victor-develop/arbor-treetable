# Arbor — User Journey (captured screenshots)

Captured by browser automation against the live stack (Vite ↔ Frappe ↔ z.ai)
on the canonical seed. View the PNGs in order:

01. **The governed tree table (sheet S): hierarchical rows, per-column owners, AI agent sidebar** — `01-governed-tree-table.png`
02. **B owns col:notes → edit commits directly (Saved)** — `02-owner-edit-executed.png`
03. **E can't edit col:budget → a Change Request is filed to the owner (c@arbor.example) + CR id** — `03-non-owner-suggested-cr.png`
04. **Schema co-design: the structural owner adds a 'Priority' column** — `04-schema-add-column.png`
05. **D owns the P2 branch → drags Z under Y (structural change commits)** — `05-drag-reparent-executed.png`
06. **A moves X into D's branch → suggested to d@ with co-approver a@ (delegated structural authority)** — `06-move-suggested-co-approver.png`
07. **G subscribes to the sheet (notification + acknowledgement ledger)** — `07-subscribe-to-changes.png`
08. **AI agent (real z.ai): reads the sheet, sets X status to done — Thought/Action/Observation/Final transcript** — `08-agent-executes.png`
09. **The agent acts under its user's authority: lacking budget rights, it files a Change Request (no privileged bypass)** — `09-agent-files-change-request.png`
10. **Import into a fresh sheet S2: the governed replay plan is previewed before any write** — `10-import-preview.png`
11. **S2 reconstructed via governed capabilities (structure, owners, split-column) — Import completed** — `11-import-result.png`
12. **The Change Request review screen: the approver (C) sees A's proposed budget edit in the inbox and approves it — the capability replays and the value applies** — `12-change-request-review.png`
13. **The column schema editor: the owner opens settings from the header gear to rename/resize, reassign ownership (grantColumn), or delete a column** — `13-column-settings.png`
14. **Per-row delete: a branch owner (D) deletes a node via the row control, gated on structural authority with a two-step confirm (deleteNode)** — `14-delete-node.png`
15. **The notification inbox: a subscriber (G) sees a requires_ack notification and acknowledges it; the same view also surfaces their Change Requests and branch delegations** — `15-notification-inbox.png`
16. **The branch delegation control: the sheet owner (A) sees the active P2→D grant with Revoke, and delegates another branch via the form (delegateBranch / revokeDelegation)** — `16-delegation.png`
