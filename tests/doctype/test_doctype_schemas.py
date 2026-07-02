"""Lane tests for the 13 Arbor DocType JSON definitions (doctypes lane).

NO bench required. These are pure structural assertions over the DocType JSON on
disk, verifying conformance to docs/DATA-MODEL.md and that the Tree Event Select
options exactly equal the closed set in arbor.core.types.EVENT_TYPES (DRY: one
source of truth for event types).

Run: pytest arbor/tests/doctype/test_doctype_schemas.py
"""

from __future__ import annotations

import json
import os

import pytest

from arbor.core.types import EVENT_TYPES

DOCTYPE_DIR = os.path.join(
	os.path.dirname(__file__), "..", "..", "arbor", "arbor", "doctype"
)


def _load(name_snake: str) -> dict:
	path = os.path.join(DOCTYPE_DIR, name_snake, f"{name_snake}.json")
	with open(path) as fh:
		return json.load(fh)


def _fieldnames(dt: dict) -> set[str]:
	return {f["fieldname"] for f in dt["fields"]}


ALL_DOCTYPES = [
	("tree_sheet", "Tree Sheet"),
	("tree_column", "Tree Column"),
	("tree_column_editor", "Tree Column Editor"),
	("tree_node", "Tree Node"),
	("tree_node_value", "Tree Node Value"),
	("branch_grant", "Branch Grant"),
	("change_request", "Change Request"),
	("change_request_approval", "Change Request Approval"),
	("subscription", "Subscription"),
	("notification", "Notification"),
	("acknowledgement", "Acknowledgement"),
	("webhook_endpoint", "Webhook Endpoint"),
	("webhook_delivery", "Webhook Delivery"),
	("tree_event", "Tree Event"),
]


@pytest.mark.parametrize("snake,label", ALL_DOCTYPES)
def test_doctype_loads_with_correct_name_and_module(snake, label):
	dt = _load(snake)
	assert dt["name"] == label
	assert dt["module"] == "Arbor"
	assert dt["doctype"] == "DocType"


def test_count_is_thirteen_plus_two_child_tables():
	# 13 canonical DocTypes; Tree Column Editor + Change Request Approval are the
	# two child tables among them per the lane brief.
	assert len(ALL_DOCTYPES) == 14


def test_tree_sheet_fields():
	f = _fieldnames(_load("tree_sheet"))
	assert {"title", "description", "structural_owner", "status", "settings"} <= f


def test_tree_column_fields_and_constraints():
	dt = _load("tree_column")
	f = _fieldnames(dt)
	assert {
		"sheet", "field", "label", "type", "options", "width",
		"editable", "column_owner", "editors", "is_label",
	} <= f
	editors = next(x for x in dt["fields"] if x["fieldname"] == "editors")
	assert editors["fieldtype"] == "Table"
	assert editors["options"] == "Tree Column Editor"
	type_field = next(x for x in dt["fields"] if x["fieldname"] == "type")
	assert set(type_field["options"].split("\n")) == {
		"text", "multiline-text", "number", "single-select-split", "multi-select-split",
	}


def test_tree_column_editor_is_child():
	dt = _load("tree_column_editor")
	assert dt.get("istable") == 1
	assert "user" in _fieldnames(dt)


def test_tree_node_is_nestedset():
	dt = _load("tree_node")
	assert dt.get("is_tree") == 1
	assert dt.get("nsm_parent_field") == "parent_tree_node"
	f = _fieldnames(dt)
	assert {"sheet", "parent_tree_node", "lft", "rgt", "is_group"} <= f
	# No label/content field on the node itself (label is a Tree Node Value).
	assert "title" not in f and "label" not in f and "node_name" not in f


def test_tree_node_value_fields():
	f = _fieldnames(_load("tree_node_value"))
	assert {"sheet", "node", "column", "value", "version"} <= f


def test_branch_grant_fields():
	dt = _load("branch_grant")
	f = _fieldnames(dt)
	assert {"sheet", "branch_root", "grantee", "scope", "granted_by", "active"} <= f
	scope = next(x for x in dt["fields"] if x["fieldname"] == "scope")
	assert scope["options"] == "structure"


def test_change_request_fields_and_approvals_child():
	dt = _load("change_request")
	f = _fieldnames(dt)
	assert {
		"sheet", "target_kind", "operation", "payload", "requester",
		"resolved_approver", "status", "decided_by", "decided_at",
		"resulting_event", "approvals",
	} <= f
	approvals = next(x for x in dt["fields"] if x["fieldname"] == "approvals")
	assert approvals["fieldtype"] == "Table"
	assert approvals["options"] == "Change Request Approval"
	status = next(x for x in dt["fields"] if x["fieldname"] == "status")
	assert set(status["options"].split("\n")) == {
		"proposed", "approved", "rejected", "withdrawn",
	}


def test_change_request_approval_is_child():
	dt = _load("change_request_approval")
	assert dt.get("istable") == 1
	assert "user" in _fieldnames(dt)


def test_subscription_target_is_dynamic_link():
	dt = _load("subscription")
	f = _fieldnames(dt)
	assert {
		"subscriber", "subscriber_kind", "scope", "target",
		"event_types", "delivery", "requires_ack",
	} <= f
	target = next(x for x in dt["fields"] if x["fieldname"] == "target")
	assert target["fieldtype"] == "Dynamic Link"


def test_notification_fields():
	f = _fieldnames(_load("notification"))
	assert {
		"tree_event", "change_request", "recipient", "channel",
		"delivered_at", "requires_ack",
	} <= f


def test_acknowledgement_fields():
	f = _fieldnames(_load("acknowledgement"))
	assert {"notification", "user", "acked_at"} <= f


def test_webhook_endpoint_fields():
	dt = _load("webhook_endpoint")
	f = _fieldnames(dt)
	assert {"url", "secret", "event_types", "scope", "target", "active"} <= f
	secret = next(x for x in dt["fields"] if x["fieldname"] == "secret")
	assert secret["fieldtype"] == "Password"


def test_webhook_delivery_fields():
	dt = _load("webhook_delivery")
	f = _fieldnames(dt)
	assert {
		"endpoint", "tree_event", "status", "attempts",
		"last_response", "next_retry_at", "signature",
	} <= f
	status = next(x for x in dt["fields"] if x["fieldname"] == "status")
	assert set(status["options"].split("\n")) == {
		"pending", "delivered", "failed", "exhausted",
	}


def test_tree_event_options_equal_core_closed_set():
	dt = _load("tree_event")
	type_field = next(x for x in dt["fields"] if x["fieldname"] == "type")
	options = tuple(type_field["options"].split("\n"))
	# DRY: the Select options MUST equal the core's closed set, in order.
	assert options == EVENT_TYPES


def test_tree_event_is_append_only_in_permissions():
	dt = _load("tree_event")
	for perm in dt["permissions"]:
		assert not perm.get("write"), "Tree Event must not grant write (append-only)"
		assert not perm.get("delete"), "Tree Event must not grant delete (append-only)"


# ===========================================================================
# Wave-4 schema parity — new DocTypes (impersonation, cell comments, process)
# and new columns on existing DocTypes. Same bench-free, JSON-on-disk regime.
# ===========================================================================

#: Wave-0 additions: 4 top-level DocTypes + 2 child tables.
WAVE4_DOCTYPES = [
	("arbor_impersonation_session", "Arbor Impersonation Session"),
	("arbor_cell_comment", "Arbor Cell Comment"),
	("arbor_process", "Arbor Process"),
	("arbor_process_stage", "Arbor Process Stage"),
	("arbor_process_run", "Arbor Process Run"),
	("arbor_process_run_stage", "Arbor Process Run Stage"),
]

#: The Wave-4 child tables MUST carry istable=1.
WAVE4_CHILD_TABLES = {"arbor_process_stage", "arbor_process_run_stage"}


@pytest.mark.parametrize("snake,label", WAVE4_DOCTYPES)
def test_wave4_doctype_loads_with_correct_name_and_module(snake, label):
	dt = _load(snake)
	assert dt["name"] == label
	assert dt["module"] == "Arbor"
	assert dt["doctype"] == "DocType"


# Frappe injects a handful of standard fieldnames that may legally appear in
# field_order without an explicit entry in `fields` (e.g. the child-table idx).
_STANDARD_FRAPPE_FIELDS = {"idx"}


@pytest.mark.parametrize(
	"snake,label", ALL_DOCTYPES + WAVE4_DOCTYPES
)
def test_every_field_is_in_field_order(snake, label):
	# Structural invariant: every declared field appears exactly once in
	# field_order, and field_order names only declared (or Frappe-standard)
	# fields. Child tables and top-level DocTypes alike must satisfy this or
	# Frappe silently drops fields.
	dt = _load(snake)
	field_order = dt.get("field_order", [])
	declared = [f["fieldname"] for f in dt["fields"]]
	assert set(declared) <= set(field_order), (
		f"{label}: fields not in field_order: "
		f"{set(declared) - set(field_order)}"
	)
	unknown = set(field_order) - set(declared) - _STANDARD_FRAPPE_FIELDS
	assert not unknown, (
		f"{label}: field_order names undeclared fields: {unknown}"
	)
	assert len(field_order) == len(set(field_order)), (
		f"{label}: duplicate entries in field_order"
	)


@pytest.mark.parametrize("snake,label", WAVE4_DOCTYPES)
def test_wave4_child_tables_flagged_istable(snake, label):
	dt = _load(snake)
	if snake in WAVE4_CHILD_TABLES:
		assert dt.get("istable") == 1, f"{label} must be a child table (istable=1)"
	else:
		assert not dt.get("istable"), f"{label} must NOT be a child table"


def test_arbor_impersonation_session_fields():
	dt = _load("arbor_impersonation_session")
	f = _fieldnames(dt)
	assert {
		"real_user", "impersonated_user", "active",
		"started_at", "ended_at", "reason",
	} <= f
	real_user = next(x for x in dt["fields"] if x["fieldname"] == "real_user")
	assert real_user["fieldtype"] == "Link"
	assert real_user["options"] == "User"


def test_arbor_cell_comment_fields():
	dt = _load("arbor_cell_comment")
	f = _fieldnames(dt)
	assert {
		"sheet", "node", "column", "thread_root", "parent_comment",
		"author", "body", "mentions", "resolved", "resolved_by", "resolved_at",
	} <= f
	# Self-referential threading links target the comment DocType itself.
	for fn in ("thread_root", "parent_comment"):
		fld = next(x for x in dt["fields"] if x["fieldname"] == fn)
		assert fld["fieldtype"] == "Link"
		assert fld["options"] == "Arbor Cell Comment"


def test_arbor_process_fields_and_stages_child():
	dt = _load("arbor_process")
	f = _fieldnames(dt)
	assert {"sheet", "title", "enabled", "stages"} <= f
	stages = next(x for x in dt["fields"] if x["fieldname"] == "stages")
	assert stages["fieldtype"] == "Table"
	assert stages["options"] == "Arbor Process Stage"


def test_arbor_process_stage_fields():
	dt = _load("arbor_process_stage")
	assert dt.get("istable") == 1
	assert {"column", "sla_seconds"} <= _fieldnames(dt)


def test_arbor_process_run_link_field_is_arbor_process_not_process():
	# 'process' is a reserved DocType fieldname in Frappe; the Wave-0 fix renamed
	# the Arbor Process Run link to 'arbor_process'. Lock that in.
	dt = _load("arbor_process_run")
	f = _fieldnames(dt)
	assert "arbor_process" in f, "Run must link via 'arbor_process' (reserved-name fix)"
	assert "process" not in f, "'process' is reserved; must not be a fieldname"
	link = next(x for x in dt["fields"] if x["fieldname"] == "arbor_process")
	assert link["fieldtype"] == "Link"
	assert link["options"] == "Arbor Process"
	assert {"sheet", "node", "status", "run_stages"} <= f
	run_stages = next(x for x in dt["fields"] if x["fieldname"] == "run_stages")
	assert run_stages["fieldtype"] == "Table"
	assert run_stages["options"] == "Arbor Process Run Stage"


def test_arbor_process_run_stage_fields():
	dt = _load("arbor_process_run_stage")
	assert dt.get("istable") == 1
	assert {
		"stage_idx", "column", "entered_at", "filled_at",
		"due_at", "breached",
	} <= _fieldnames(dt)


# --- New columns on existing DocTypes (impersonation + comment provenance) ---


def test_tree_event_has_impersonation_provenance_columns():
	dt = _load("tree_event")
	f = _fieldnames(dt)
	assert {"real_user", "impersonated_as"} <= f


def test_change_request_has_real_actor_columns():
	dt = _load("change_request")
	f = _fieldnames(dt)
	assert {"real_requester", "real_decider"} <= f


def test_change_request_approval_has_real_user_column():
	dt = _load("change_request_approval")
	assert "real_user" in _fieldnames(_load("change_request_approval"))
	assert "real_user" in _fieldnames(dt)


def test_notification_has_source_and_comment_columns():
	dt = _load("notification")
	f = _fieldnames(dt)
	assert {"source", "comment"} <= f
	source = next(x for x in dt["fields"] if x["fieldname"] == "source")
	assert source["fieldtype"] == "Select"
	assert "comment" in set(source["options"].split("\n"))
	comment = next(x for x in dt["fields"] if x["fieldname"] == "comment")
	assert comment["fieldtype"] == "Link"
	assert comment["options"] == "Arbor Cell Comment"


def test_notification_tree_event_is_now_nullable():
	# Notifications can originate from a comment/process, not only a Tree Event,
	# so tree_event must no longer be mandatory (reqd falsy / 0).
	dt = _load("notification")
	tree_event = next(x for x in dt["fields"] if x["fieldname"] == "tree_event")
	assert not tree_event.get("reqd"), "Notification.tree_event must be nullable (reqd 0)"


def test_event_types_still_exactly_eleven():
	# Wave-4 adds no new event type; the closed set stays at 11 members.
	assert len(EVENT_TYPES) == 11
	assert len(set(EVENT_TYPES)) == 11
