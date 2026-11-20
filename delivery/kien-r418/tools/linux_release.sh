#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
revision="${1:-R4}"
case "$revision" in R4|R4.2|R4.3|R4.4|R4.5|R4.6|R4.7|R4.8|R4.9|R4.10|R4.11|R4.12|R4.13|R4.14|R4.15|R4.18) ;; *) exit 2 ;; esac
kit="$root/Release-K01-K18-$revision"
out="$root/evidence/linux-release-${revision,,}"
mkdir "$out"
build_python="$(cat "$root/evidence/linux-runtime-path.txt")/venv/bin/python"
scratch="$(mktemp -d /tmp/aletheia-r4-wheel-XXXXXXXX)"
unset PYTHONPATH PYTHONHOME SOURCE_DATE_EPOCH
export PYTHONDONTWRITEBYTECODE=1
for attempt in a b; do
  "$build_python" -B "$kit/source/scripts/k18_release_build.py" --source "$kit/source" \
    --manifest "$kit/source-manifest.json" --output "$scratch/$attempt"
  cp "$scratch/$attempt/result.json" "$out/build-$attempt.json"
done
for artifact in "$scratch/a/dist/"*; do
  cmp "$artifact" "$scratch/b/dist/${artifact##*/}"
done
mkdir "$kit/dist"
cp "$scratch/a/dist/"* "$kit/dist/"
python3 -m venv "$scratch/installed"
runtime="$scratch/installed/bin/python"
"$runtime" -m pip install --only-binary=:all: --require-hashes -r "$kit/source/deploy/hosted/requirements.lock"
"$runtime" -m pip install --no-index --no-deps "$kit/dist/"*.whl
"$runtime" -m pip check
"$runtime" -I -B "$kit/source/scripts/k18_installed_release_check.py" --kit "$kit" --output "$out/installed-proof"
"$build_python" -I -B - "$out/installed-proof/report.pdf" <<'PY'
from pathlib import Path
import json, sys, unicodedata
from pypdf import PdfReader

path = Path(sys.argv[1])
reader = PdfReader(path, strict=True)
content = unicodedata.normalize('NFC', '\n'.join(page.extract_text() for page in reader.pages))
expected = json.loads(path.with_suffix('.json').read_bytes())
metric = expected['snapshot']['metric_definitions'][0]['metric_name']
assert reader.pages and metric in content and expected['result']['id'] in content and expected['snapshot']['id'] in content
path.with_name('pdf-parser.json').write_text(json.dumps({'pages': len(reader.pages), 'vietnamese_text_extracted': True}), encoding='utf-8')
PY
