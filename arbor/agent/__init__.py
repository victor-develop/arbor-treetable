"""Public re-export shim: ``arbor.agent`` -> ``arbor.arbor.agent``.

The documented agent endpoint is ``POST /api/method/arbor.agent.chat``
(ARCHITECTURE §8.1). The real implementation lives at ``arbor.arbor.agent.chat``
(app module path on a bench). This package only re-exports the submodules so the
collapsed dotted path resolves; no agent logic is duplicated here.
"""

from __future__ import annotations
