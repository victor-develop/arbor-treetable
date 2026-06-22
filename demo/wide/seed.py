"""Arbor demo seed — "Product Feature Matrix" (sheet WIDE): a deliberately WIDE,
content-DENSE, multi-role table for stress-testing information-dense presentation
(many columns, several paragraph-length, owned by 5 different roles).

Runs like the other demo seeds: from cwd /Users/victorzhou/temp/frappe-bench

    env/bin/python <repo>/demo/wide/seed.py

Idempotent: a prior WIDE sheet is raw-deleted leaf-first before rebuild. Writes
per-role API keys to /tmp/wide_keys.json (PM/DEV/MKT/QA/LEGAL + ADMIN reused).
"""

import os
import json

import frappe

frappe.init(site="arbor.test", sites_path="sites")
frappe.connect()
frappe.flags.in_test = True

import sys
sys.path.insert(0, "apps/arbor")

from arbor.arbor.adapter.repository import FrappeRepository
from arbor.arbor.adapter.seed import ensure_personas, _user

SHEET_NAME = "WIDE"
SHEET_TITLE = "Product Feature Matrix"

PM, DEV, MKT, QA, LEGAL = (
    "pm@arbor.example", "dev@arbor.example", "marketing@arbor.example",
    "qa@arbor.example", "legal@arbor.example",
)
PERSONAS = (PM, DEV, MKT, QA, LEGAL)

# field | label | type | is_label | owner
COLUMNS = [
    ("feature",             "Feature",              "text",           True,  PM),
    ("definition",          "Definition",           "multiline-text", False, PM),
    ("priority",            "Priority",             "text",           False, PM),
    ("status",              "Status",               "text",           False, PM),
    ("target_release",      "Target Release",       "text",           False, PM),
    ("owner_team",          "Owner Team",           "text",           False, PM),
    ("acceptance_criteria", "Acceptance Criteria",  "multiline-text", False, QA),
    ("test_plan",           "Test Plan",            "multiline-text", False, QA),
    ("effort_days",         "Effort (days)",        "number",         False, DEV),
    ("usage_event",         "Usage Event",          "text",           False, DEV),
    ("api_endpoint",        "API Endpoint",         "text",           False, DEV),
    ("data_schema",         "Data Schema",          "multiline-text", False, DEV),
    ("marketing_copy",      "Marketing Copy",       "multiline-text", False, MKT),
    ("marketing_link",      "Marketing Page",       "text",           False, MKT),
    ("legal_notes",         "Legal / Compliance",   "multiline-text", False, LEGAL),
    ("risk_notes",          "Risk Notes",           "multiline-text", False, LEGAL),
]

# (label, parent_label)
TREE = [
    ("Platform", None),
    ("Authentication", "Platform"),
    ("Email / Password Login", "Authentication"),
    ("SSO / OIDC", "Authentication"),
    ("Multi-Factor Auth", "Authentication"),
    ("Authorization", "Platform"),
    ("Roles & Permissions", "Authorization"),
    ("API Tokens", "Authorization"),
    ("Billing", None),
    ("Subscriptions", "Billing"),
    ("Plan Management", "Subscriptions"),
    ("Proration", "Subscriptions"),
    ("Invoicing", "Billing"),
    ("Invoice Generation", "Invoicing"),
    ("Tax Calculation", "Invoicing"),
    ("Payments", "Billing"),
    ("Card Payments", "Payments"),
    ("Refunds", "Payments"),
    ("Analytics", None),
    ("Dashboards", "Analytics"),
    ("Usage Dashboard", "Dashboards"),
    ("Revenue Dashboard", "Dashboards"),
    ("Reports", "Analytics"),
    ("Scheduled Reports", "Reports"),
    ("Export to CSV", "Reports"),
    ("Notifications", None),
    ("Email Notifications", "Notifications"),
    ("In-App Notifications", "Notifications"),
    ("Webhooks", "Notifications"),
]

PRIORITY = ["P0", "P1", "P2", "P3"]
STATUS = ["Planned", "In progress", "Blocked", "Shipped", "Deprecated"]
TEAMS = ["Platform", "Growth", "Payments", "Data", "Trust & Safety"]


def _snake(label: str) -> str:
    out = []
    for ch in label.lower():
        out.append(ch if ch.isalnum() else "_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _content(label: str, i: int) -> dict:
    """Realistic, paragraph-length values per column so the table is genuinely
    content-dense (the point of this demo)."""
    snake = _snake(label)
    return {
        "definition": (
            f"{label} — the capability that lets customers {('configure','operate','review','automate','extend')[i % 5]} "
            f"{label.lower()} end to end. Spans the customer-facing surface, the governed API, "
            f"and the agent tools, and inherits the sheet's two-axis permission model."
        ),
        "priority": PRIORITY[i % len(PRIORITY)],
        "status": STATUS[i % len(STATUS)],
        "target_release": f"v{1 + (i % 4)}.{(i * 3) % 12}",
        "owner_team": TEAMS[i % len(TEAMS)],
        "acceptance_criteria": (
            f"GIVEN a customer with access to {label}, WHEN they perform the primary action, "
            f"THEN the result is persisted, audited, and reflected in the snapshot within 1s. "
            f"Edge cases: empty input, concurrent edit, permission denied → routed to a Change Request."
        ),
        "test_plan": (
            f"Unit: pure logic for {label.lower()} (branch coverage >=90%). Integration: real "
            f"adapter end-to-end on a bench. E2e: drive the {label.lower()} flow as each persona "
            f"and assert executed-vs-suggested routing. Regression suite tag: {snake}."
        ),
        "effort_days": (i % 8) + 1,
        "usage_event": f"{snake}_used",
        "api_endpoint": f"POST /api/method/arbor.{snake}",
        "data_schema": (
            f"{{ \"id\": \"str\", \"{snake}_state\": \"enum\", \"updated_by\": \"user\", "
            f"\"updated_at\": \"datetime\", \"version\": \"int\" }} — versioned for optimistic "
            f"concurrency; soft-delete via status."
        ),
        "marketing_copy": (
            f"Ship {label} faster. Arbor gives your team a governed, single source of truth for "
            f"{label.lower()} — every change tracked, every role in its lane, no more spreadsheet chaos."
        ),
        "marketing_link": f"https://shop.example/features/{snake.replace('_', '-')}",
        "legal_notes": (
            f"{label}: review data-residency + retention. PII touched: "
            f"{('none','email','billing','usage','identity')[i % 5]}. "
            f"Requires DPA addendum for EU; audit log retained 7y."
        ),
        "risk_notes": (
            f"Risk: {('low','medium','high')[i % 3]}. Primary failure mode for {label.lower()} is "
            f"a stale write under concurrency (mitigated by base_version guard). Dependency on the "
            f"billing provider adds external-outage exposure."
        ),
    }


def drop_wide():
    if not frappe.db.exists("Tree Sheet", SHEET_NAME):
        return
    frappe.flags.ignore_links = True
    for n in frappe.get_all("Tree Node Value", filters={"sheet": SHEET_NAME}, pluck="name"):
        frappe.delete_doc("Tree Node Value", n, force=True, ignore_permissions=True)
    for dt in ("Branch Grant", "Change Request", "Tree Event"):
        for n in frappe.get_all(dt, filters={"sheet": SHEET_NAME}, pluck="name"):
            frappe.delete_doc(dt, n, force=True, ignore_permissions=True, ignore_on_trash=True)
    for r in frappe.get_all("Tree Node", filters={"sheet": SHEET_NAME}, fields=["name"], order_by="lft desc"):
        d = frappe.get_doc("Tree Node", r.name)
        d.flags.ignore_nestedset_validations = True
        frappe.delete_doc("Tree Node", r.name, force=True, ignore_permissions=True, ignore_on_trash=True)
    for n in frappe.get_all("Tree Column", filters={"sheet": SHEET_NAME}, pluck="name"):
        frappe.delete_doc("Tree Column", n, force=True, ignore_permissions=True)
    frappe.delete_doc("Tree Sheet", SHEET_NAME, force=True, ignore_permissions=True)
    frappe.db.commit()


def build():
    ensure_personas(PERSONAS)
    repo = FrappeRepository()
    sheet_doc = frappe.new_doc("Tree Sheet")
    sheet_doc.title = SHEET_TITLE
    sheet_doc.structural_owner = _user(PM)
    sheet_doc.status = "active"
    sheet_doc.settings = {}
    sheet_doc.insert(ignore_permissions=True)
    sheet = sheet_doc.name

    columns = {}
    for field, label, ctype, is_label, owner in COLUMNS:
        name = repo.create_column(sheet, {
            "field": field, "label": label, "type": ctype, "is_label": is_label,
            "column_owner": _user(owner), "editors": [], "options": None,
            "read_level": "public",
        })
        cd = frappe.get_doc("Tree Column", name)
        if cd.read_level != "public":
            cd.read_level = "public"; cd.save(ignore_permissions=True)
        columns[field] = name

    nodes = {}
    for label, parent_label in TREE:
        nodes[label] = repo.create_node(sheet=sheet, parent=nodes[parent_label] if parent_label else None)
    frappe.db.commit()
    return sheet, columns, nodes


def rn(dt, old, new):
    if old != new and frappe.db.exists(dt, old) and not frappe.db.exists(dt, new):
        frappe.rename_doc(dt, old, new, force=True)


def main():
    drop_wide()
    sheet, columns, nodes = build()
    rn("Tree Sheet", sheet, SHEET_NAME)
    for field, name in columns.items():
        rn("Tree Column", name, f"wide:{field}")
    for label, name in nodes.items():
        rn("Tree Node", name, label)
    frappe.db.commit()

    sheet = SHEET_NAME
    columns = {f: f"wide:{f}" for f in columns}
    nodes = {l: l for l in nodes}

    repo = FrappeRepository()
    for i, (label, _parent) in enumerate(TREE):
        repo.set_value(sheet, nodes[label], columns["feature"], label)
        for field, val in _content(label, i).items():
            repo.set_value(sheet, nodes[label], columns[field], val)
    frappe.db.commit()

    role_user = {"PM": PM, "DEV": DEV, "MKT": MKT, "QA": QA, "LEGAL": LEGAL}
    keys = {}
    for role, email in role_user.items():
        u = _user(email)
        if not frappe.db.get_value("User", u, "api_key"):
            from frappe.core.doctype.user.user import generate_keys as _gen
            _gen(u); frappe.db.commit()
        keys[role] = f"{frappe.db.get_value('User', u, 'api_key')}:{frappe.utils.password.get_decrypted_password('User', u, 'api_secret')}"
    prev = json.load(open("/tmp/e2e_keys.json")) if os.path.exists("/tmp/e2e_keys.json") else {}
    if prev.get("ADMIN"):
        keys["ADMIN"] = prev["ADMIN"]
    open("/tmp/wide_keys.json", "w").write(json.dumps(keys))
    frappe.db.commit()
    frappe.clear_cache()

    print("SHEET_OK", frappe.db.exists("Tree Sheet", SHEET_NAME))
    print("COLS", frappe.db.count("Tree Column", filters={"sheet": SHEET_NAME}))
    print("NODES", frappe.db.count("Tree Node", filters={"sheet": SHEET_NAME}))
    print("VALUES", frappe.db.count("Tree Node Value", filters={"sheet": SHEET_NAME}))
    print("KEYS", list(keys.keys()))


if __name__ == "__main__":
    main()
