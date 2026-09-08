"""Saved-view payload rules — PURE (this module never imports frappe).

The ONE definition of what a stored ``SheetView`` payload may look like, shared
verbatim by BOTH api lanes (``arbor.arbor.api`` and ``arbor.standalone.app``)
the way the dispatch modules are: a saved view that validated on one adapter
must validate identically on the other, or the same frontend would behave
differently per deployment.

The shape mirrors ``frontend/src/lib/view.ts``'s ``SheetView`` /
``isSheetView``: ``{v: 1, hidden: [], order: [], width?: {}, collapsed?: []}``.

Two rules worth stating out loud:

* **Unknown top-level keys are a 400, not a silent drop.** A saved view is
  presentation state, never a payload channel; refusing the unknown keeps the
  stored blob to the four known facets AND keeps a newer client's extra facet
  from disappearing without anyone noticing (a silent drop has been a real bug
  in this codebase twice).
* **The size cap is the share token's 4096 bytes.** Saved views and the ``?v=``
  link are orthogonal mechanisms over the SAME overlay, so an arrangement that
  can be saved must also stay expressible as a link; capping both at 4096
  decoded bytes keeps that true and keeps a row from becoming storage for
  something that is not an arrangement.

Column REDACTION (``filter_payload_columns``) is the other half of
reveal-impossibility: a published view's ``order``/``hidden``/``width`` names
columns, so a reader only ever gets back the names of columns they can read.
The client-side guarantee already holds without this (``resolveColumns`` starts
from the read-ACL-filtered snapshot and never force-shows), but a column the
reader cannot read should not have its NAME leak through a shared view either
— the same reason ``_require_readable_cell`` 404s instead of admitting an
owner-only cell exists.
"""

from __future__ import annotations

import json
from typing import Any

#: Max serialized payload size, in bytes. Same budget as the ``?v=`` share
#: token's decoded length (frontend/src/lib/view.ts) — see the module docstring.
MAX_PAYLOAD_BYTES = 4096

#: Max label length. Matches the VARCHAR(140) both adapters store it in.
MAX_LABEL_LEN = 140

#: Max saved views one author may keep on one sheet. A picker is only useful
#: while it is scannable, and this is also the only bound on how many rows one
#: user can add to a sheet everybody lists — without it, "save" is an unbounded
#: write. Generous enough that no real workflow meets it.
MAX_VIEWS_PER_SHEET = 50

#: The only two visibilities. ``private`` = author-only (what a save creates);
#: ``sheet`` = everyone who can read the sheet.
VISIBILITIES = ("private", "sheet")

#: The known SheetView facets. Anything else is a 400 (see the module docstring).
_ALLOWED_KEYS = {"v", "hidden", "order", "width", "collapsed"}


class SavedViewError(ValueError):
    """A malformed label / payload / visibility. Each api lane maps this to 400."""


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SavedViewError(f"view.{field} must be a list of column names")
    return list(value)


def normalize_label(raw: Any) -> str:
    """Trim and bound a view's human label (400 on empty / oversize)."""
    label = raw.strip() if isinstance(raw, str) else ""
    if not label:
        raise SavedViewError("A saved view needs a name")
    if len(label) > MAX_LABEL_LEN:
        raise SavedViewError(f"A view name must be at most {MAX_LABEL_LEN} characters")
    return label


def normalize_visibility(raw: Any, default: str = "private") -> str:
    """``private`` | ``sheet`` (400 on anything else). A save with no visibility
    is PRIVATE: publishing to the sheet is always an explicit second step."""
    if raw is None or raw == "":
        return default
    if raw not in VISIBILITIES:
        raise SavedViewError("visibility must be 'private' or 'sheet'")
    return str(raw)


def validate_payload(raw: Any) -> dict[str, Any]:
    """Validate a stored/incoming SheetView payload; return it normalized.

    Accepts either a decoded mapping or its JSON text (the frappe lane stores
    the field as JSON text, the standalone lane as a JSON column — one function
    so neither shape gets its own rules). Raises ``SavedViewError`` on a
    non-object, an unknown ``v``, a non-conforming facet, an unknown key, or an
    over-budget blob.
    """
    if isinstance(raw, (str, bytes)):
        # Bound the text BEFORE parsing: a multi-megabyte string must not be
        # turned into objects just to be rejected afterwards.
        if len(raw) > MAX_PAYLOAD_BYTES * 4:
            raise SavedViewError("This view is too large to save")
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise SavedViewError("view must be a JSON object") from exc

    if not isinstance(raw, dict):
        raise SavedViewError("view must be a JSON object")

    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise SavedViewError(f"Unknown view field(s): {', '.join(sorted(unknown))}")

    # ``v`` is the shape version, exactly as the share token reads it: an
    # unknown version is refused rather than guessed at.
    if raw.get("v") != 1 or isinstance(raw.get("v"), bool):
        raise SavedViewError("Unsupported view version")

    out: dict[str, Any] = {
        "v": 1,
        "hidden": _string_list(raw.get("hidden"), "hidden"),
        "order": _string_list(raw.get("order"), "order"),
    }

    width = raw.get("width")
    if width is not None:
        if not isinstance(width, dict) or not all(isinstance(k, str) for k in width):
            raise SavedViewError("view.width must be a column -> pixels map")
        for key, val in width.items():
            # bool is an int subclass in python; a boolean width is a shape error.
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise SavedViewError(f"view.width[{key}] must be a number")
        out["width"] = {k: v for k, v in width.items()}

    collapsed = raw.get("collapsed")
    if collapsed is not None:
        out["collapsed"] = _string_list(collapsed, "collapsed")

    if len(json.dumps(out, separators=(",", ":")).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise SavedViewError("This view is too large to save")
    return out


def filter_payload_columns(payload: Any, readable: set[str]) -> dict[str, Any]:
    """Drop every column reference the caller cannot READ (reveal-impossibility,
    see the module docstring). ``collapsed`` holds NODE names and is untouched —
    the read-ACL is a column axis. A payload that no longer validates (a legacy
    row) degrades to the default view rather than raising on a read path."""
    try:
        view = validate_payload(payload)
    except SavedViewError:
        return {"v": 1, "hidden": [], "order": []}
    out: dict[str, Any] = {
        "v": 1,
        "hidden": [c for c in view["hidden"] if c in readable],
        "order": [c for c in view["order"] if c in readable],
    }
    if "width" in view:
        out["width"] = {k: v for k, v in view["width"].items() if k in readable}
    if "collapsed" in view:
        out["collapsed"] = list(view["collapsed"])
    return out
