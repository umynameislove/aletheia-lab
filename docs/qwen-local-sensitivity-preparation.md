# Qwen local sensitivity preparation

## Boundary

This runbook prepares the pinned local runtime. It does not authorize model
inference, open main outcomes, choose the 12-family subset, or consume any
registered attempt. The 32.5 GB Q8 weight stays outside Git. Q6 may be prepared
only as a separately identified operational fallback.

The authoritative identity and policy are in
`configs/evaluation/diagnosis_qwen_local_sensitivity_candidate.json`.

## User-run preparation commands

These commands use explicit locations outside the repository. They require
network access and substantial disk space, so they are intentionally not run by
the project audit.

```bash
set -euo pipefail

ALETHEIA_QWEN_ROOT='/absolute/private/path/to/aletheia-qwen'

mkdir -p "$ALETHEIA_QWEN_ROOT/q8"
mkdir -p "$ALETHEIA_QWEN_ROOT/runtime"
mkdir -p "$ALETHEIA_QWEN_ROOT/source"
mkdir -p "$ALETHEIA_QWEN_ROOT/receipts"

curl -L --fail --retry 5 --continue-at - \
  --output "$ALETHEIA_QWEN_ROOT/q8/qwen3-coder-30b-a3b-instruct-q8_0.gguf" \
  'https://huggingface.co/ggml-org/Qwen3-Coder-30B-A3B-Instruct-Q8_0-GGUF/resolve/99d445b5fd000458cabc098da6a79c2967a472f1/qwen3-coder-30b-a3b-instruct-q8_0.gguf?download=true'

test "$(stat -f '%z' "$ALETHEIA_QWEN_ROOT/q8/qwen3-coder-30b-a3b-instruct-q8_0.gguf")" = '32483933856'
test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/q8/qwen3-coder-30b-a3b-instruct-q8_0.gguf" | awk '{print $1}')" = 'f22993e29318b5b9ec2026f6b65802a5ca99b38ab4844aab83aed8a26ce00ff6'

curl -L --fail --retry 5 \
  --output "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" \
  'https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/resolve/b2cff646eb4bb1d68355c01b18ae02e7cf42d120/tokenizer_config.json?download=true'

test "$(/usr/bin/shasum -a 256 "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" | awk '{print $1}')" = '60f6e8cb15c98dd07300a3cc465ea662de245d2095e4245616af21b2324db3fc'

git clone --branch v0.4.1 --single-branch \
  'https://github.com/ggml-org/llama.cpp.git' \
  "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp"

cd "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp"
test "$(git rev-parse HEAD^{commit})" = 'b29c606e28a01b1bc8c1351026a0fa6e616bf6c4'
test "$(git rev-parse v0.4.1^{tag})" = '29aaf1c27faa48292357cea2120d94114a545006'

python3 -m venv "$ALETHEIA_QWEN_ROOT/runtime/build-venv"
source "$ALETHEIA_QWEN_ROOT/runtime/build-venv/bin/activate"
python -m pip install --upgrade pip cmake

cmake --version
cmake -S . -B build \
  -DGGML_METAL=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 12

test -x build/bin/llama-server
build/bin/llama-server --version
```

If the target clone already exists, stop and inspect it; do not delete or
overwrite it automatically. Record the exact CMake, compiler, llama-server,
macOS, and hardware outputs in the future calibration receipt.

## Frozen server envelope for later calibration

Do not start this server until a development-only calibration packet exists.
When that packet is ready, the launch envelope is:

```bash
ALETHEIA_QWEN_ROOT='/absolute/private/path/to/aletheia-qwen'

"$ALETHEIA_QWEN_ROOT/runtime/llama.cpp/build/bin/llama-server" \
  --model "$ALETHEIA_QWEN_ROOT/q8/qwen3-coder-30b-a3b-instruct-q8_0.gguf" \
  --host 127.0.0.1 \
  --port 18080 \
  --ctx-size 32768 \
  --n-predict 600 \
  --n-gpu-layers 99 \
  --jinja \
  --no-context-shift \
  --temp 0.7 \
  --top-p 0.8 \
  --top-k 20 \
  --min-p 0 \
  --repeat-penalty 1.05 \
  --seed 17
```

No tools are supplied. Bind only to loopback. The calibration may inspect
schema compatibility, OOM/crash/truncation, memory, runtime, template identity,
and same-seed repeatability. It must not inspect main correctness,
groundedness, labels, or hypothesis direction.

## Frozen development calibration

Do not start a server manually for this step. The entrypoint verifies the Q8
bytes, llama.cpp commit/tag/binary, source template and embedded GGUF template,
then owns a loopback-only server with the frozen flags. It runs the six
synthetic `3 cases x {B1,A3}` cells plus one same-seed replicate and writes no
raw response into the receipt. It does not open or execute any main request.

Run this only after the preparation commands above succeed:

```bash
set -euo pipefail

ALETHEIA_REPO='/absolute/path/to/working-baseline'
ALETHEIA_QWEN_ROOT='/absolute/private/path/to/aletheia-qwen'

cd "$ALETHEIA_REPO"
source .venv/bin/activate

test ! -e "$ALETHEIA_QWEN_ROOT/receipts/q8-calibration-v1.json"
test ! -e "$ALETHEIA_QWEN_ROOT/receipts/q8-calibration-v1-server.log"

PYTHONPATH=src python scripts/calibrate_qwen_local_sensitivity.py \
  --model "$ALETHEIA_QWEN_ROOT/q8/qwen3-coder-30b-a3b-instruct-q8_0.gguf" \
  --llama-checkout "$ALETHEIA_QWEN_ROOT/runtime/llama.cpp" \
  --source-tokenizer-config "$ALETHEIA_QWEN_ROOT/source/tokenizer_config.json" \
  --receipt "$ALETHEIA_QWEN_ROOT/receipts/q8-calibration-v1.json" \
  --server-log "$ALETHEIA_QWEN_ROOT/receipts/q8-calibration-v1-server.log"

PYTHONPATH=src python scripts/audit_qwen_local_calibration.py \
  --receipt "$ALETHEIA_QWEN_ROOT/receipts/q8-calibration-v1.json"
```

The receipt must say `development_operational_calibration_pass`,
`protected_main_outcomes_opened: false`, `main_registered_attempts_consumed: 0`,
`scientific_quality_selection_performed: false`, six calibration cells, and
seven local inference calls. A failure is preserved and reviewed; do not edit
the receipt, silently rerun, or activate Q6 based on answer quality.

The audit must return `status: pass`. It verifies the receipt self-hash, exact
request sequence, frozen model/runner/template identities, server flags,
memory envelope, and absence of raw outputs. The HTTP client rejects redirects
and every request URL must remain on plain loopback HTTP.

## What remains after preparation

- run the frozen Q8 development calibration and preserve its receipt/log;
- activate Q6 only if Q8 fails a predeclared operational criterion;
- obtain outcome-blind independent review of the calibration receipt, the
  already sealed 32-family main census, and the already locked 72 request IDs;
  and
- issue the next forward manifest before any sensitivity output is generated.
