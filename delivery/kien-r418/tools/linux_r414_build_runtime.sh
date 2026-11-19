#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
runtime="$(mktemp -d /tmp/aletheia-r414-build-XXXXXXXX)"
python3 -m venv "$runtime/venv"
"$runtime/venv/bin/python" -m pip install --only-binary=:all: --require-hashes \
  -r "$root/Release-K01-K18-R4.14/source/deploy/hosted/build-requirements.lock"
"$runtime/venv/bin/python" -m pip install --only-binary=:all: -c "$root/test-constraints.txt" pypdf==6.21.4
"$runtime/venv/bin/python" -m pip check
cp -- "$root/evidence/linux-runtime-path.txt" "$root/evidence/linux-runtime-path-before-r414-restart.txt"
printf '%s\n' "$runtime" > "$root/evidence/linux-runtime-path.txt"
printf '%s\n' "$runtime" > "$root/evidence/linux-runtime-r414-path.txt"
