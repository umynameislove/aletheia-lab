# K18 clean local install and deterministic release artifacts

The release builder stages only the explicit `source-manifest.json` inventory,
checks every SHA256 before building, refuses symlinks/cache/private actor paths,
and never writes to the original source. Use a new output directory outside the
source tree. A manifest is a trusted release input, not a way to select arbitrary
unreviewed files from an operator's machine.

Install the three build pins in a new Python 3.12 virtual environment first:

```text
python -m venv NEW_BUILD_VENV
NEW_BUILD_PYTHON -m pip install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r SOURCE/deploy/hosted/build-requirements.lock
NEW_BUILD_PYTHON -I SOURCE/scripts/k18_release_build.py --source SOURCE --manifest SOURCE_MANIFEST_JSON --output NEW_OUTPUT
```

`NEW_BUILD_PYTHON` means `NEW_BUILD_VENV/bin/python` on macOS/Linux or
`NEW_BUILD_VENV\Scripts\python.exe` on Windows. Replace the other uppercase
tokens with real paths. The builder verifies the actual installed build-tool
versions against the staged lock. It takes the timestamp from `--epoch`, then
`SOURCE_DATE_EPOCH`, then the pinned manifest. Retain `build.log`, `result.json`
and both `dist` artifacts. A build failure leaves its isolated output for review.

Wheel payload bytes, including the installer RECORD, are preserved. ZIP order,
timestamps and regular-file mode become deterministic. Source-distribution
payload bytes are also preserved while gzip timestamps and tar order, modes,
owner names/IDs and host-only PAX metadata are normalized. Regular source/package
files have mode 0644 and archive directories 0755; the Python installer creates
console entrypoints. This release has no executable binary source payload.
Normalization changes archive hashes, so issue a new release identity and keep
earlier sealed archives unchanged.

Repeat with another fresh build venv and another new output. Compare the SHA256
of both final wheels and both final sdists. The observed two-build local proof
is for the same Python/platform and pinned tools. A matching hash is not proof
that the Linux image, operating-system packages or another operator's machine
has been tested.

Use a separate clean runtime venv with all 52 production dependency pins:

```text
python -m venv NEW_RUNTIME_VENV
NEW_RUNTIME_PYTHON -m pip install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes --report runtime-install.json -r SOURCE/deploy/hosted/requirements.lock
NEW_RUNTIME_PYTHON -m pip install --no-deps WHEEL_PATH
NEW_RUNTIME_PYTHON -m pip check
NEW_RUNTIME_PYTHON -I SOURCE/scripts/k18_installed_release_check.py --kit KIT_ROOT --output NEW_PROOF_OUTPUT
NEW_RUNTIME_PYTHON -I -m aletheia_lab --help
NEW_RUNTIME_PYTHON -I -m aletheia_lab.hosted.server --help
NEW_RUNTIME_PYTHON -I SOURCE/scripts/k18_recovery_admin.py --help
```

Remove `PYTHONPATH` and build-only `SOURCE_DATE_EPOCH` from the environment before
the runtime check; run without `-O`/`-OO`. The verifier fails closed if optimization
would disable its assertions. `KIT_ROOT` contains `source-manifest.json`,
`source/deploy/hosted/requirements.lock` and
`source/fixtures/metric-dimensions-hosted-v2/snapshot.json`.
The check refuses external `.pth` reuse and system site packages, verifies every
installed runtime file, runs one real analysis child and three real report
children, checks their installed module origins, and verifies JSON/Markdown/PDF
bytes against the installed renderer. It requires only the production runtime
pins. It checks the exact installed 52-pin version closure and rejects extra
distributions except the application and pip bootstrap. Actual fresh-install
provenance comes from the separate hashlocked installer receipt; package presence
alone cannot establish how an environment was created. PDF parser/text extraction is an explicitly separate development-tool
verification; installing a parser is not required for the runtime proof.

These checks close local dependency and artifact-rebuild gaps. They do not run
Docker, log in through an IdP, start an installed ManagedRuntime against staging,
or qualify image UID/volumes/secret mounts/OS scanning. Storage DR still needs
encrypted independent off-host database/WAL/object/latest-authority checkpoints,
failed-host recovery and measured RPO/RTO on the actual deployment dataset. The
existing native logical/PITR and process-recovery fixtures retain their stated
small local scope.
