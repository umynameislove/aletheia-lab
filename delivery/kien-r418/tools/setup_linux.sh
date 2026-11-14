#!/usr/bin/env bash
set -euo pipefail
kit_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
runtime_root="$(mktemp -d /tmp/aletheia-r4-linux-XXXXXXXX)"
python3 -m venv "$runtime_root/venv"
"$runtime_root/venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$kit_root/source/deploy/hosted/requirements.lock"
"$runtime_root/venv/bin/python" -m pip install --only-binary=:all: --require-hashes -r "$kit_root/source/deploy/hosted/build-requirements.lock"
"$runtime_root/venv/bin/python" -m pip install -c "$kit_root/test-constraints.txt" pytest==9.2.0 hypothesis==6.170.15 pypdf==6.21.4 'jsonschema[format]==4.25.1' httpx==0.28.1 ruff==0.17.4
"$runtime_root/venv/bin/python" -m pip check
printf '%s\n' "$runtime_root" > "$kit_root/evidence/linux-runtime-path.txt"
