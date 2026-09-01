#!/bin/bash
# Assemble the App Center iapp deploy directory for the standalone build.
# Usage: standalone/package.sh [outdir]   (default /tmp/arbor-deploy)
# Then:  cd <outdir> && app-center iapp deploy 19 --dir .
#
# Contents: the python package (core + standalone + the PURE arbor.arbor
# subpackages the app reuses: agent/ + dispatch/ — no frappe import at top level),
# the built frontend (frontend/dist -> dist/), a `server:app` shim matching the
# iapp start command, and requirements.txt.
# Env the app expects (set once via `app-center iapp env set 19 K=V`):
#   ARBOR_SECRET_KEY=<random>  ARBOR_DEV_LOGIN=1  ARBOR_FRONTEND_DIST=/app/webroot
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/tmp/arbor-deploy}"
rm -rf "$OUT"; mkdir -p "$OUT/arbor/arbor"
cp "$ROOT/arbor/__init__.py" "$OUT/arbor/"
cp -R "$ROOT/arbor/core" "$OUT/arbor/core"
cp -R "$ROOT/arbor/standalone" "$OUT/arbor/standalone"
cp "$ROOT/arbor/arbor/__init__.py" "$OUT/arbor/arbor/"
cp -R "$ROOT/arbor/arbor/agent" "$OUT/arbor/arbor/agent"
cp -R "$ROOT/arbor/arbor/dispatch" "$OUT/arbor/arbor/dispatch"
cp -R "$ROOT/frontend/dist" "$OUT/webroot"
find "$OUT" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
cat > "$OUT/server.py" <<'PY'
"""iapp entrypoint shim — the app's start command is `uvicorn server:app`."""
from arbor.standalone.app import app  # noqa: F401
PY
cat > "$OUT/requirements.txt" <<'REQ'
fastapi>=0.115
uvicorn>=0.30
sqlalchemy>=2.0
pymysql>=1.1
itsdangerous>=2.1
authlib>=1.3
python-multipart>=0.0.9
litellm>=1.40
REQ
echo "assembled: $OUT ($(du -sh "$OUT" | cut -f1), $(find "$OUT" -type f | wc -l | tr -d ' ') files)"
