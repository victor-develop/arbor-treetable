"""Public re-export shim: ``arbor.adapter.seed`` -> ``arbor.arbor.adapter.seed``.

The canonical BENCH seed builder (``seed_canonical_sheet`` / ``ensure_personas``
/ ``ensure_user``) used by the backend integration tests. Imports frappe (only
runnable on a bench); re-exported here so the documented collapsed path resolves.
No seed logic is duplicated.
"""

from __future__ import annotations

from arbor.arbor.adapter.seed import *  # noqa: F401,F403
