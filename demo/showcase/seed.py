"""Arbor COMPREHENSIVE showcase seed — sheet ``ACME`` ("ACME Platform Roadmap").

A single, cohesive product-org roadmap built to exercise EVERY Arbor capability
area so a human can experience each one end-to-end (see
``docs/DEMO-JOURNEYS.md``). Unlike the WIDE/ECOM demos (which are about
information density), ACME is about GOVERNANCE: it ships a live inbox of open
Change Requests, open role applications, branch delegation, two-axis column
authority, role-as-ACL addressing, read-ACL visibility tiers, and a watcher
subscription — all created through the REAL executor so the demo reflects
production behavior (CRs actually route, events actually emit).

Run it with the env-python invocation (no ``bench`` CLI on this box)::

    cd $BENCH/sites && ../env/bin/python \
        ../apps/arbor/demo/showcase/seed.py

or from anywhere by pointing PYTHONPATH/site at the bench (the bottom of this
file initializes frappe itself).

IDEMPOTENT: each run first wipes ONLY the ACME sheet and the showcase demo
users' role applications + non-catalog role grants. It NEVER touches ECOM, WIDE,
the role catalog rows, or the admin account. Re-running produces the identical
state with no duplicates and no errors.

Surface parity: every governed mutation goes through
``arbor.core.executor.execute_action`` with a concrete ``Actor`` — exactly the
path Web / REST / the agent use. Raw frappe doc writes are used ONLY for catalog
scaffolding (the Tree Sheet shell, columns, users, the one extra role row) where
no capability exists.
"""

from __future__ import annotations

import os
import sys

import frappe

# --- bench bootstrap (works whether run as a script or via bench execute) ----
if not getattr(frappe.local, "site", None):
    # Allow `../env/bin/python .../seed.py` from sites/ OR an absolute path.
    sites_path = os.environ.get("ARBOR_SITES_PATH", ".")
    frappe.init(site="arbor.test", sites_path=sites_path)
    frappe.connect()

# Make the app importable in the dev-repo layout (arbor/arbor/...).
for p in ("apps/arbor", os.path.join(os.path.dirname(__file__), "..", "..")):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

frappe.set_user("Administrator")

from arbor.arbor.adapter.repository import FrappeRepository, FrappeEventSink
from arbor.core.executor import execute_action
from arbor.core.types import Actor, ActorType

# ---------------------------------------------------------------------------
# Cast of characters (all on the OSS-clean @arbor.example test domain)
# ---------------------------------------------------------------------------
SHEET = "ACME"
SHEET_TITLE = "ACME Platform Roadmap"

PM = "pm@arbor.example"                     # sheet structural owner + pm role
DEV = "dev@arbor.example"                   # a developer column owner
MKT = "marketing@arbor.example"             # marketing column owner

# Showcase-only demo users (pattern <name>.demo@arbor.example). alice/bob/carol
# already exist; we add a couple more so every inbox has variety.
ALICE = "alice.demo@arbor.example"          # a PM-track applicant + CR requester
BOB = "bob.demo@arbor.example"              # an eng-track applicant + CR requester
CAROL = "carol.demo@arbor.example"          # a watcher / acknowledger
DANA = "dana.demo@arbor.example"            # delegated sub-branch owner
ERIN = "erin.demo@arbor.example"            # marketing applicant + batch requester

DEMO_USERS = (ALICE, BOB, CAROL, DANA, ERIN)
PERSONA_USERS = (PM, DEV, MKT)

# Roles we use. pm/developer/marketing/finance already exist in the catalog. We
# add ONE extra applicable role ("design") to show the catalog is extensible.
EXTRA_ROLE = ("design", "Design", 1)  # (role, label, applicable)


def resolve_admin() -> str:
    """The System Manager who plays the demo driver. Never hard-coded to a
    site-specific identity in source: taken from ``ARBOR_DEMO_ADMIN`` if set,
    else the first real (non-Administrator) System Manager on the site, else
    ``Administrator``. The executor's admin-gated calls in this seed pass
    ``is_admin=True`` explicitly, so the seed works regardless; this value is
    just whose name appears as the approver/grantor on the audit trail."""
    env = os.environ.get("ARBOR_DEMO_ADMIN")
    if env and frappe.db.exists("User", env):
        return env
    repo = FrappeRepository()
    admins = [a for a in repo.list_admins() if a not in ("Administrator", "Guest")]
    return admins[0] if admins else "Administrator"


ADMIN = resolve_admin()  # System Manager — the demo driver


# Actors — admin flag drives the role-admin + internalReset gates in the executor.
def actor(user: str, *, admin: bool = False, agent: bool = False) -> Actor:
    return Actor(
        user=user,
        actor_type=ActorType.AGENT if agent else ActorType.HUMAN,
        is_admin=admin,
    )


# ---------------------------------------------------------------------------
# Tree shape — a realistic product org / roadmap. (label, parent_label)
# ---------------------------------------------------------------------------
TREE = [
    ("ACME Platform", None),
    # Branch 1: Core Platform (PM owns structure)
    ("Core Platform", "ACME Platform"),
    ("Identity & Access", "Core Platform"),
    ("SSO Federation", "Identity & Access"),
    ("Passkeys", "Identity & Access"),
    ("Billing Engine", "Core Platform"),
    ("Usage Metering", "Billing Engine"),
    ("Invoicing", "Billing Engine"),
    # Branch 2: Growth (DELEGATED to Dana — she structurally owns this subtree)
    ("Growth", "ACME Platform"),
    ("Onboarding", "Growth"),
    ("Guided Setup", "Onboarding"),
    ("Sample Data", "Onboarding"),
    ("Lifecycle Messaging", "Growth"),
    ("Win-back Campaign", "Lifecycle Messaging"),
    # Branch 3: Data & AI
    ("Data & AI", "ACME Platform"),
    ("Insights Dashboard", "Data & AI"),
    ("Copilot", "Data & AI"),
]

# Which node is delegated, and to whom (Axis 1 — structural delegation).
DELEGATE_BRANCH = "Growth"
DELEGATE_TO = DANA

# ---------------------------------------------------------------------------
# Columns — every column type, plus the headline ACL features.
#   field | label | type | is_label | owner | editors | read_level | readers | options
# editors/readers may contain a "role:<key>" principal (ACL addressing).
# ---------------------------------------------------------------------------
ROLE_PM = "role:pm"  # headline: a role principal as a column editor

COLUMNS = [
    # Label column — always readable; owned by PM. (text)
    dict(field="initiative", label="Initiative", type="text", is_label=True,
         owner=PM, read_level="public"),
    # multiline-text — public
    dict(field="description", label="Description", type="multiline-text",
         owner=PM, read_level="public"),
    # single-select-split — stage. Owner PM, but ROLE:PM is an editor → every
    # holder of the pm role can edit this column automatically (ACL addressing).
    dict(field="stage", label="Stage", type="single-select-split",
         owner=PM, editors=[ROLE_PM], read_level="public",
         options={"groups": [{"label": "Stage",
                              "options": ["Discovery", "Design", "Build", "Beta", "GA"]}]}),
    # multi-select-split — tags. Owned by DEV.
    dict(field="tags", label="Tags", type="multi-select-split",
         owner=DEV, read_level="public",
         options={"groups": [{"label": "Tags",
                              "options": ["backend", "frontend", "infra", "ml", "compliance"]}]}),
    # number — effort. Owned by DEV.
    dict(field="effort_weeks", label="Effort (weeks)", type="number",
         owner=DEV, read_level="public"),
    # text — target release. Owned by PM.
    dict(field="target_release", label="Target Release", type="text",
         owner=PM, read_level="public"),
    # multiline-text — marketing copy. Owned by MKT.
    dict(field="marketing_copy", label="Marketing Copy", type="multiline-text",
         owner=MKT, read_level="public"),
    # READ-ACL tier: owner-only. Revenue forecast — only the column owner (+admin)
    # may even SEE the values. Owned by PM.
    dict(field="revenue_forecast", label="Revenue Forecast ($)", type="number",
         owner=PM, read_level="owner-only"),
    # READ-ACL tier: explicit-readers. Security review notes — readable only by an
    # explicit allowlist (here Dana + role:pm), plus owner + admin. Owned by DEV.
    dict(field="security_notes", label="Security Review", type="multiline-text",
         owner=DEV, read_level="explicit-readers", readers=[DANA, ROLE_PM]),
]

# Initial cell values, keyed by node label. Only fields present are set.
VALUES = {
    "ACME Platform": dict(description="The whole ACME product surface.", stage="GA",
                          target_release="rolling"),
    "Core Platform": dict(description="Foundational services every product builds on.",
                          stage="GA", target_release="v4.x", effort_weeks=0,
                          revenue_forecast=0),
    "Identity & Access": dict(description="AuthN/AuthZ, SSO, passkeys.", stage="Build",
                              tags=["backend", "compliance"], effort_weeks=8,
                              target_release="v4.2", revenue_forecast=120000,
                              security_notes="Threat model reviewed 2026-05. Pending pen-test on SSO."),
    "SSO Federation": dict(description="SAML + OIDC federation for enterprise tenants.",
                           stage="Beta", tags=["backend", "compliance"], effort_weeks=5,
                           target_release="v4.2", revenue_forecast=90000,
                           marketing_copy="Bring your own IdP. SSO in minutes, not weeks.",
                           security_notes="OIDC flows audited. SAML assertion replay mitigated."),
    "Passkeys": dict(description="WebAuthn passkeys as a passwordless option.",
                     stage="Design", tags=["frontend", "backend"], effort_weeks=4,
                     target_release="v4.3", revenue_forecast=30000,
                     security_notes="FIDO2 attestation policy TBD."),
    "Billing Engine": dict(description="Metering, invoicing, dunning.", stage="Build",
                           tags=["backend"], effort_weeks=10, target_release="v4.2",
                           revenue_forecast=400000),
    "Usage Metering": dict(description="Event-based usage aggregation.", stage="Build",
                           tags=["backend", "infra"], effort_weeks=6, target_release="v4.2",
                           revenue_forecast=180000),
    "Invoicing": dict(description="Invoice generation + tax.", stage="Beta",
                      tags=["backend"], effort_weeks=4, target_release="v4.2",
                      revenue_forecast=220000),
    "Growth": dict(description="Onboarding + lifecycle. Delegated to the Growth lead.",
                   stage="Build", target_release="v4.x", revenue_forecast=0),
    "Onboarding": dict(description="First-run experience.", stage="Build",
                       tags=["frontend"], effort_weeks=5, target_release="v4.2",
                       revenue_forecast=60000,
                       marketing_copy="Get to value on day one."),
    "Guided Setup": dict(description="Step-by-step product tour.", stage="Beta",
                         tags=["frontend"], effort_weeks=3, target_release="v4.2"),
    "Sample Data": dict(description="One-click demo dataset.", stage="GA",
                        tags=["frontend"], effort_weeks=1, target_release="v4.1"),
    "Lifecycle Messaging": dict(description="Triggered email/in-app journeys.",
                                stage="Design", tags=["frontend"], effort_weeks=4,
                                target_release="v4.3"),
    "Win-back Campaign": dict(description="Re-engage churned users.", stage="Discovery",
                              tags=["frontend"], effort_weeks=2, target_release="v4.4",
                              marketing_copy="Come back — here's what's new."),
    "Data & AI": dict(description="Analytics + the assistant.", stage="Build",
                      tags=["ml"], effort_weeks=12, target_release="v4.x",
                      revenue_forecast=500000),
    "Insights Dashboard": dict(description="Self-serve analytics.", stage="Beta",
                               tags=["frontend", "ml"], effort_weeks=6,
                               target_release="v4.2", revenue_forecast=140000),
    "Copilot": dict(description="The in-product AI assistant over the governed API.",
                    stage="Design", tags=["ml", "backend"], effort_weeks=10,
                    target_release="v4.3", revenue_forecast=260000,
                    marketing_copy="Ask your roadmap anything."),
}


# ===========================================================================
# Idempotent teardown — ACME only.
# ===========================================================================
def _wipe_sheet() -> None:
    frappe.flags.ignore_links = True
    if frappe.db.exists("Tree Sheet", SHEET):
        # Subscriptions have no ``sheet`` column (scope/target/Dynamic Link); drop
        # any whose target is a node/column/sheet belonging to ACME BEFORE the
        # nodes/columns vanish.
        acme_targets = set(frappe.get_all("Tree Node", filters={"sheet": SHEET}, pluck="name"))
        acme_targets |= set(frappe.get_all("Tree Column", filters={"sheet": SHEET}, pluck="name"))
        acme_targets.add(SHEET)
        for s in frappe.get_all("Subscription", fields=["name", "target"]):
            if s.target in acme_targets:
                frappe.delete_doc("Subscription", s.name, force=True,
                                  ignore_permissions=True, ignore_on_trash=True)
        for n in frappe.get_all("Tree Node Value", filters={"sheet": SHEET}, pluck="name"):
            frappe.delete_doc("Tree Node Value", n, force=True, ignore_permissions=True)
        for dt in ("Branch Grant", "Change Request", "Tree Event"):
            for n in frappe.get_all(dt, filters={"sheet": SHEET}, pluck="name"):
                frappe.delete_doc(dt, n, force=True, ignore_permissions=True, ignore_on_trash=True)
        for r in frappe.get_all("Tree Node", filters={"sheet": SHEET},
                                fields=["name"], order_by="lft desc"):
            d = frappe.get_doc("Tree Node", r.name)
            d.flags.ignore_nestedset_validations = True
            frappe.delete_doc("Tree Node", r.name, force=True,
                              ignore_permissions=True, ignore_on_trash=True)
        for n in frappe.get_all("Tree Column", filters={"sheet": SHEET}, pluck="name"):
            frappe.delete_doc("Tree Column", n, force=True, ignore_permissions=True)
        frappe.delete_doc("Tree Sheet", SHEET, force=True, ignore_permissions=True)
    # Notifications/acks from the showcase reference events we just deleted. The
    # Notification schema links only via ``tree_event`` (the role op/role are
    # recovered from that event's payload), so we clear a row when EITHER its
    # recipient is a showcase demo user OR its ``tree_event`` is now orphaned
    # (its source event was deleted with the sheet). This is self-healing and
    # never touches the admin's unrelated, still-linked notifications.
    for note in frappe.get_all("Notification",
                               fields=["name", "recipient", "tree_event"]):
        orphaned = bool(note.tree_event) and not frappe.db.exists("Tree Event", note.tree_event)
        if note.recipient not in DEMO_USERS and not orphaned:
            continue
        for ack in frappe.get_all("Acknowledgement", filters={"notification": note.name}, pluck="name"):
            frappe.delete_doc("Acknowledgement", ack, force=True, ignore_permissions=True)
        frappe.delete_doc("Notification", note.name, force=True,
                          ignore_permissions=True, ignore_on_trash=True)
    frappe.db.commit()


def _wipe_demo_role_state() -> None:
    """Remove demo users' role applications + non-catalog (application/admin) role
    grants for the roles this demo touches, so re-runs don't pile up. The role
    CATALOG rows (pm/developer/marketing/finance/design) are preserved."""
    roles = ("pm", "developer", "marketing", "design", "finance")
    for app in frappe.get_all("Arbor Role Application",
                              filters={"requester": ["in", list(DEMO_USERS)]}, pluck="name"):
        frappe.delete_doc("Arbor Role Application", app, force=True, ignore_permissions=True)
    for g in frappe.get_all("Arbor Role Grant",
                            filters={"grantee": ["in", list(DEMO_USERS)],
                                     "role": ["in", list(roles)]}, pluck="name"):
        frappe.delete_doc("Arbor Role Grant", g, force=True, ignore_permissions=True)
    frappe.db.commit()


# ===========================================================================
# Catalog scaffolding (raw frappe — no capability exists for these).
# ===========================================================================
def _ensure_users() -> None:
    for email in DEMO_USERS + PERSONA_USERS:
        if not frappe.db.exists("User", email):
            doc = frappe.get_doc({
                "doctype": "User", "email": email,
                "first_name": email.split("@")[0].split(".")[0].title(),
                "send_welcome_email": 0, "user_type": "System User", "enabled": 1,
            })
            doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_extra_role() -> None:
    role, label, applicable = EXTRA_ROLE
    if not frappe.db.exists("Arbor Role", role):
        frappe.get_doc({"doctype": "Arbor Role", "role": role, "label": label,
                        "applicable": applicable, "active": 1}).insert(ignore_permissions=True)
        frappe.db.commit()


def _build_sheet_shell() -> tuple[str, dict, dict]:
    """Create the Tree Sheet + columns + nodes via the repo (catalog scaffolding),
    returning (sheet, columns_by_field, nodes_by_label). Nodes are created with
    the repo here (not the executor) so the structure exists before we drive
    governed mutations onto it; subsequent value edits + CRs DO go through the
    executor for surface parity."""
    repo = FrappeRepository()

    sheet_doc = frappe.new_doc("Tree Sheet")
    sheet_doc.title = SHEET_TITLE
    sheet_doc.structural_owner = PM
    sheet_doc.status = "active"
    sheet_doc.settings = {}
    sheet_doc.insert(ignore_permissions=True)
    sheet = sheet_doc.name

    columns: dict[str, str] = {}
    for c in COLUMNS:
        editors = c.get("editors") or []
        readers = c.get("readers") or []
        principals = editors + readers
        has_role_principal = any(
            isinstance(p, str) and p.startswith("role:") for p in principals)
        if has_role_principal:
            # The editors/readers child ``user`` field is a Link to User, which
            # rejects a ``role:<key>`` principal. ACL addressing stores the role
            # principal as a literal string (the resolver expands it at decision
            # time), so we insert the doc directly with ``ignore_links`` — the ONE
            # place catalog scaffolding must bypass a Link check. Real-user
            # editors/readers in the same column still round-trip unchanged.
            doc = frappe.new_doc("Tree Column")
            doc.sheet = sheet
            doc.field = c["field"]
            doc.label = c["label"]
            doc.type = c["type"]
            doc.options = c.get("options")
            doc.column_owner = c["owner"]
            doc.is_label = 1 if c.get("is_label") else 0
            doc.editable = 1
            doc.read_level = c.get("read_level", "public")
            for u in editors:
                doc.append("editors", {"user": u})
            for u in readers:
                doc.append("readers", {"user": u})
            doc.flags.ignore_links = True
            doc.insert(ignore_permissions=True)
            name = doc.name
        else:
            name = repo.create_column(sheet, {
                "field": c["field"], "label": c["label"], "type": c["type"],
                "is_label": c.get("is_label", False), "column_owner": c["owner"],
                "editors": editors, "readers": readers,
                "options": c.get("options"), "read_level": c.get("read_level", "public"),
            })
        columns[c["field"]] = name

    nodes: dict[str, str] = {}
    for label, parent_label in TREE:
        nodes[label] = repo.create_node(
            sheet=sheet, parent=nodes[parent_label] if parent_label else None)
    frappe.db.commit()
    return sheet, columns, nodes


def _rename(dt: str, old: str, new: str) -> None:
    if old != new and frappe.db.exists(dt, old) and not frappe.db.exists(dt, new):
        frappe.rename_doc(dt, old, new, force=True)


def _canonicalize_names(sheet: str, columns: dict, nodes: dict) -> tuple[str, dict, dict]:
    """Give the sheet/columns/nodes stable, human-readable ids so the demo URL and
    the journeys doc can reference them verbatim (ACME, acme:stage, node labels)."""
    _rename("Tree Sheet", sheet, SHEET)
    for field, name in list(columns.items()):
        _rename("Tree Column", name, f"acme:{field}")
    for label, name in list(nodes.items()):
        _rename("Tree Node", name, label)
    frappe.db.commit()
    return SHEET, {f: f"acme:{f}" for f in columns}, {l: l for l in nodes}


# ===========================================================================
# Governed mutations — THROUGH THE EXECUTOR (surface parity).
# ===========================================================================
def _seed_values(columns: dict, nodes: dict) -> int:
    """Set initial cell values as the relevant column OWNER so each write is the
    AUTHORIZED/executed path (no CR). Owner per column is known from COLUMNS."""
    repo, sink = FrappeRepository(), FrappeEventSink()
    owner_of = {c["field"]: c["owner"] for c in COLUMNS}
    count = 0
    # Always set the label first.
    for label, vals in VALUES.items():
        ordered = ["initiative"] + [f for f in vals if f != "initiative"]
        merged = dict(vals)
        merged["initiative"] = label
        for field in ordered:
            if field not in merged:
                continue
            execute_action(
                "updateCell",
                {"sheet": SHEET, "node": nodes[label], "column": columns[field],
                 "value": merged[field]},
                actor(owner_of.get(field, PM)), repo, sink,
            )
            count += 1
    frappe.db.commit()
    return count


def _seed_delegation(nodes: dict) -> str:
    """Axis 1 — PM delegates the Growth subtree to Dana via the executor. PM is the
    structural owner of Growth (no nearer grant), so this is the authorized path."""
    repo, sink = FrappeRepository(), FrappeEventSink()
    out = execute_action(
        "delegateBranch",
        {"sheet": SHEET, "branch_root": nodes[DELEGATE_BRANCH], "grantee": DELEGATE_TO},
        actor(PM), repo, sink,
    )
    frappe.db.commit()
    return out.data.get("branch_grant")


def _seed_column_grant(columns: dict) -> None:
    """Axis 2 — the DEV owner of 'tags' adds Bob as an explicit editor via the
    executor (grantColumn). Demonstrates column-authority delegation distinct from
    the role:pm addressing already wired on 'stage'."""
    repo, sink = FrappeRepository(), FrappeEventSink()
    execute_action(
        "grantColumn",
        {"sheet": SHEET, "column": columns["tags"], "editors": [BOB]},
        actor(DEV), repo, sink,
    )
    frappe.db.commit()


def _seed_change_requests(columns: dict, nodes: dict) -> dict:
    """Create a varied, OPEN review inbox by driving NON-OWNER edits through the
    executor (each becomes a Change Request). Then APPROVE a couple to show the
    applied path; leave the rest proposed. Returns a summary dict.

    Routing recap (so the demo is predictable):
      * cell edit on a PM-owned column by a non-pm  -> CR routed to PM
      * structural add under Core Platform by alice -> CR routed to PM (sheet owner)
      * structural add under Growth by bob          -> CR routed to Dana (delegate)
      * column-schema update by a non-owner          -> CR routed to column owner
      * a batch (multi-change) CR spanning owners    -> one CR, per-item approvers
      * a moveNode by a non-owner                     -> dual-end CR (PM + Dana)
    """
    repo, sink = FrappeRepository(), FrappeEventSink()
    created, approved = [], []

    def suggest(action, params, who):
        o = execute_action(action, dict(params, sheet=SHEET), actor(who), repo, sink)
        assert o.kind == "suggested", f"{action} by {who} unexpectedly {o.kind}"
        created.append((action, who, o.change_request))
        return o.change_request

    # 1) cell-value CR: Alice proposes a stage bump on Passkeys (PM-owned col) ->
    #    Alice is NOT pm (no role grant), so this routes to PM. LEFT OPEN.
    suggest("updateCell",
            {"node": nodes["Passkeys"], "column": columns["stage"], "value": "Build"},
            ALICE)

    # 2) cell-value CR on a DEV-owned column by Carol -> routes to DEV. LEFT OPEN.
    suggest("updateCell",
            {"node": nodes["Copilot"], "column": columns["effort_weeks"], "value": 14},
            CAROL)

    # 3) structural add under Core Platform by Alice -> routes to PM. APPROVE it
    #    (shows the applied path: the node really gets created on approval).
    cr_struct = suggest("addNode",
                        {"parent": nodes["Identity & Access"],
                         "values": {"initiative": "Device Trust"}},
                        ALICE)

    # 4) structural add under Growth by Bob -> Growth is delegated to Dana, so this
    #    routes to DANA (not PM). LEFT OPEN — demonstrates delegation re-routing.
    suggest("addNode",
            {"parent": nodes["Onboarding"], "values": {"initiative": "Checklist Widget"}},
            BOB)

    # 5) column-schema CR: Bob proposes renaming the 'effort_weeks' label ->
    #    routes to the column owner (DEV). LEFT OPEN.
    suggest("updateColumn",
            {"column": columns["effort_weeks"], "patch": {"label": "Effort (eng-weeks)"}},
            BOB)

    # 6) BATCH CR (multi-change) by Erin spanning TWO owners in one review unit:
    #    a PM-owned cell edit + a DEV-owned cell edit. One CR; each item routes to
    #    its own approver; nothing applies until BOTH are approved. LEFT OPEN.
    o = execute_action(
        "suggestChanges",
        {"sheet": SHEET, "changes": [
            {"action": "updateCell",
             "params": {"node": nodes["Insights Dashboard"], "column": columns["target_release"],
                        "value": "v4.3"}},               # PM-owned
            {"action": "updateCell",
             "params": {"node": nodes["Insights Dashboard"], "column": columns["effort_weeks"],
                        "value": 7}},                     # DEV-owned
        ]},
        actor(ERIN), repo, sink,
    )
    assert o.kind == "suggested"
    created.append(("suggestChanges", ERIN, o.change_request))
    batch_cr = o.change_request

    # 7) moveNode by Bob: move "Win-back Campaign" (under Growth → Dana's branch) to
    #    sit under "Core Platform" (→ PM). src approver = Dana, dest approver = PM,
    #    Bob is neither -> dual-end CR. LEFT OPEN (Journey shows two-sided approval).
    suggest("moveNode",
            {"node": nodes["Win-back Campaign"], "new_parent": nodes["Core Platform"]},
            BOB)

    # ---- APPROVE a couple as the resolved approver (the applied path) ----------
    # Approve the structural add (#3) AS PM -> the node "Device Trust" is created.
    out = execute_action("approveChange", {"change_request": cr_struct},
                         actor(PM, admin=False), repo, sink)
    assert out.kind == "executed", f"approve of {cr_struct} -> {out.kind}"
    approved.append(cr_struct)
    frappe.db.commit()

    return {"created": created, "approved": approved, "batch_cr": batch_cr}


def _seed_role_applications() -> dict:
    """Open the admin Roles inbox: demo users self-apply for applicable roles via
    the executor (applyForRole). Approve ONE to show the grant materialize (and to
    light up the role:pm column editor — once Alice holds pm she can edit 'stage'
    and read 'security_notes'); leave the others PENDING."""
    repo, sink = FrappeRepository(), FrappeEventSink()
    created, approved = [], []

    def apply(user, role, why):
        o = execute_action("applyForRole", {"role": role, "justification": why},
                           actor(user), repo, sink)
        created.append((user, role, o.data.get("role_application")))
        return o.data.get("role_application")

    app_alice = apply(ALICE, "pm", "Leading the Identity track; need PM authority.")
    apply(BOB, "developer", "Owning Billing Engine implementation.")
    apply(ERIN, "marketing", "Running the GA launch comms.")
    apply(DANA, "design", "Driving the onboarding redesign.")

    # Approve Alice -> pm AS the admin. This makes role:pm resolve to include Alice,
    # so the 'stage' editors (role:pm) and 'security_notes' readers (role:pm) now
    # include her — the headline ACL-addressing payoff, live in the snapshot.
    out = execute_action("approveRoleApplication", {"role_application": app_alice},
                         actor(ADMIN, admin=True), repo, sink)
    assert out.kind == "executed"
    approved.append(app_alice)

    # Also DIRECT-grant pm to the persona PM user so the role principal has a stable
    # member even before any application (admin assignRole path).
    execute_action("assignRole", {"role": "pm", "grantee": PM},
                   actor(ADMIN, admin=True), repo, sink)
    frappe.db.commit()
    return {"created": created, "approved": approved}


def _seed_subscription(nodes: dict) -> str:
    """Carol watches the Growth branch for proposals/approvals WITH acknowledgement
    required — so the notified-vs-acked ledger is auditable. Created via the
    executor (subscribe) so it emits SUBSCRIPTION_CHANGED like production."""
    repo, sink = FrappeRepository(), FrappeEventSink()
    out = execute_action(
        "subscribe",
        {"scope": "branch", "target": nodes[DELEGATE_BRANCH],
         "event_types": ["CHANGE_PROPOSED", "CHANGE_APPROVED", "NODE_CREATED"],
         "delivery": "in-app", "requires_ack": True},
        actor(CAROL), repo, sink,
    )
    frappe.db.commit()
    return out.data.get("subscription")


# ===========================================================================
# Orchestration + summary
# ===========================================================================
def main() -> None:
    # 1. Idempotent teardown (ACME + demo role state only).
    _wipe_sheet()
    _wipe_demo_role_state()

    # 2. Catalog scaffolding.
    _ensure_users()
    _ensure_extra_role()
    sheet, columns, nodes = _build_sheet_shell()
    sheet, columns, nodes = _canonicalize_names(sheet, columns, nodes)

    # 3. Governed mutations through the executor (surface parity).
    n_values = _seed_values(columns, nodes)
    grant = _seed_delegation(nodes)
    _seed_column_grant(columns)
    cr = _seed_change_requests(columns, nodes)
    roles = _seed_role_applications()
    sub = _seed_subscription(nodes)

    frappe.clear_cache()

    # ----- summary -----
    def count(dt, **f):
        return frappe.db.count(dt, filters=f)

    print("=" * 64)
    print("ACME showcase seed COMPLETE")
    print("=" * 64)
    print(f"Sheet:        {SHEET}  ({SHEET_TITLE})  owner={PM}")
    print(f"URL:          http://localhost:5173/?sheet={SHEET}")
    print(f"Columns:      {count('Tree Column', sheet=SHEET)}  "
          f"(types: text, multiline-text, number, single/multi-select-split)")
    print(f"Nodes:        {count('Tree Node', sheet=SHEET)}  (incl. approved 'Device Trust')")
    print(f"Cell values:  {count('Tree Node Value', sheet=SHEET)}  ({n_values} writes via executor)")
    print(f"Branch grant: {grant}  (Growth -> {DELEGATE_TO})")
    print(f"role:pm editor on 'acme:stage'; readers role:pm + {DANA} on 'acme:security_notes'")
    print(f"Read-ACL:     owner-only on revenue_forecast; explicit-readers on security_notes")
    print("-" * 64)
    print(f"Change Requests created: {len(cr['created'])}  (approved: {len(cr['approved'])})")
    for action, who, name in cr["created"]:
        st = frappe.db.get_value("Change Request", name, "status")
        print(f"   - {name:<14} {action:<15} by {who:<24} [{st}]")
    print(f"   batch CR: {cr['batch_cr']}")
    print(f"Open CRs on ACME: {count('Change Request', sheet=SHEET, status='proposed')}")
    print("-" * 64)
    print(f"Role applications created: {len(roles['created'])}  (approved: {len(roles['approved'])})")
    for who, role, name in roles["created"]:
        st = frappe.db.get_value("Arbor Role Application", name, "status")
        print(f"   - {name:<16} {who:<24} -> {role:<10} [{st}]")
    print(f"Open role applications: {count('Arbor Role Application', status='proposed')}")
    pm_grantees = sorted(frappe.get_all(
        "Arbor Role Grant", filters={"role": "pm", "active": 1}, pluck="grantee"))
    print(f"Active pm grantees: {pm_grantees}")
    print("-" * 64)
    print(f"Subscription: {sub}  (Carol watches Growth, requires_ack)")
    print("=" * 64)


if __name__ == "__main__":
    main()
