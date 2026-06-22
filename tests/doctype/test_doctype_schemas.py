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
