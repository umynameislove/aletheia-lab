# Qwen3.8 local sensitivity preparation

## Boundary

This runbook prepares the exact Qwen3.8 text-only local runtime. It does not
authorize the 72-request sensitivity, open protected outcomes, or consume a
registered attempt. The model, receipts, and logs stay outside Git under
an operator-selected private directory.

Ollama is not used because the study binds the exact GGUF bytes, chat template,
runtime commit, build flags, and server arguments. Vision, DFlash, and MTP
sidecars are intentionally excluded.

## Download and build

Run this block from a fresh Terminal. It downloads to `.part`, verifies the
complete file, and only then renames it to the frozen filename.

```bash
set -euo pipefail

ALETHEIA_QWEN_ROOT='/absolute/private/path/to/aletheia-qwen'
ALETHEIA_BUILD_VENV="$ALETHEIA_QWEN_ROOT/runtime/build-venv"

mkdir -p "$ALETHEIA_QWEN_ROOT/q8"
mkdir -p "$ALETHEIA_QWEN_ROOT/runtime"
mkdir -p "$ALETHEIA_QWEN_ROOT/source"
mkdir -p "$ALETHEIA_QWEN_ROOT/receipts"

curl -L --fail --retry 5 --continue-at - \
  --output "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf.part" \
  'https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/resolve/efbb3b1f70a21d97fd4495240648405f7228554f/Qwen3.8-27B-Q8_0.gguf?download=true'

test "$(stat -f '%z' "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf.part")" = '28595763648'
test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf.part" | awk '{print $1}')" = 'aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1'
test ! -e "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf"
mv "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf.part" \
  "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf"

curl -L --fail --retry 5 \
  --output "$ALETHEIA_QWEN_ROOT/source/config.json" \
  'https://huggingface.co/Qwen/Qwen3.8-27B/resolve/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/config.json?download=true'
curl -L --fail --retry 5 \
  --output "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" \
  'https://huggingface.co/Qwen/Qwen3.8-27B/resolve/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/tokenizer_config.json?download=true'
curl -L --fail --retry 5 \
  --output "$ALETHEIA_QWEN_ROOT/source/chat_template.jinja" \
  'https://huggingface.co/Qwen/Qwen3.8-27B/resolve/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/chat_template.jinja?download=true'

test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/source/config.json" | awk '{print $1}')" = '191e0af232104ed8b65258cf3fb2b842e288008baca7633c11b82a1ac7203aab'
test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" | awk '{print $1}')" = 'b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27'
test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/source/chat_template.jinja" | awk '{print $1}')" = 'c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041'

if [ ! -d "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp/.git" ]; then
  test ! -e "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp"
  git clone --branch v0.4.1 --single-branch \
    'https://github.com/ggml-org/llama.cpp.git' \
    "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp"
fi

cd "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp"
test -z "$(git status --porcelain --untracked-files=no)"
test "$(git rev-parse 'HEAD^{commit}')" = 'b29c606e28a01b1bc8c1351026a0fa6e616bf6c4'
test "$(git rev-parse 'v0.4.1^{tag}')" = '29aaf1c27faa48292357cea2120d94114a545006'

if [ ! -x "$ALETHEIA_BUILD_VENV/bin/python" ]; then
  python3 -m venv "$ALETHEIA_BUILD_VENV"
fi
source "$ALETHEIA_BUILD_VENV/bin/activate"
python -m pip install --upgrade pip cmake

cmake -S . -B build \
  -DGGML_METAL=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 12

grep -F 'GGML_METAL:BOOL=ON' build/CMakeCache.txt
grep -F 'CMAKE_BUILD_TYPE:STRING=Release' build/CMakeCache.txt
test -x build/bin/llama-server
build/bin/llama-server --version

printf 'QWEN38_Q8_PREPARATION=PASS\n'
```

## Outcome-blind calibration after the transport correction

The first operator preflight stopped before server start because the repository
virtual environment could not resolve CMake. The corrected environment reached a
fully loaded loopback server, but the first chat request was rejected before
generation because the provider payload had been wrapped in the execution-hash
namespace. Both failures are preserved by content identity in the forward
technical-correction artifact. Neither produced model output, opened protected
outcomes, or consumed a registered attempt.

Run the block below only after the transport correction is merged with green CI
and preparation has printed `QWEN38_Q8_PREPARATION=PASS`. Do not delete or reuse
the failed `v1` server log. The corrected attempt uses new create-only `v2`
paths. The entrypoint owns the loopback server and performs exactly six synthetic
B1/A3 cells plus one repeatability replicate. It stores hashes and operational
metadata, not raw model responses.

```bash
set -euo pipefail

ALETHEIA_REPO='/absolute/path/to/working-baseline'
ALETHEIA_QWEN_ROOT='/absolute/private/path/to/aletheia-qwen'

cd "$ALETHEIA_REPO"
export PATH="$ALETHEIA_REPO/.venv/bin:$ALETHEIA_QWEN_ROOT/runtime/build-venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"

test "$(command -v python)" = "$ALETHEIA_REPO/.venv/bin/python"
test "$(command -v cmake)" = "$ALETHEIA_QWEN_ROOT/runtime/build-venv/bin/cmake"

test -f "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v1-server.log"
test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v1-server.log" | awk '{print $1}')" = '930e9deed1ca043026f1268a690e6e4b77321f8a1506b69cf7619306a7715f6a'

test ! -e "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v2.json"
test ! -e "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v2-server.log"

PYTHONPATH=src python scripts/calibrate_qwen_local_sensitivity.py \
  --technical-correction configs/evaluation/diagnosis_qwen38_calibration_technical_correction.json \
  --model "$ALETHEIA_QWEN_ROOT/q8/Qwen3.8-27B-Q8_0.gguf" \
  --llama-checkout "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp" \
  --source-tokenizer-config "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" \
  --receipt "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v2.json" \
  --server-log "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v2-server.log"

PYTHONPATH=src python scripts/audit_qwen_local_calibration.py \
  --technical-correction configs/evaluation/diagnosis_qwen38_calibration_technical_correction.json \
  --receipt "$ALETHEIA_QWEN_ROOT/receipts/qwen38-q8-calibration-v2.json"
```

The audit must report `status: pass`, seven inference calls, zero registered
attempts, and unopened protected outcomes. Any failure or interruption is
preserved and reviewed; do not delete the receipt/log and rerun silently.
