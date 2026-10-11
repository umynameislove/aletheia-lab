# Public model reload versus process restart

An authored minimal operational reproducer for unmodified **MLServer1.7.1**,
Python3.12, `parallel_workers=0`. It asks about dependency refresh scope, not
whether MLServer violates a promised recursive reload contract. This is a
source-informed control, not an independent deployment or new incident family.
No Aletheia module, signing system or observer is used.

Use a fresh copy of this folder, an environment with MLServer1.7.1, and free
loopback ports18083/18084. Record the package/Python versions and retain complete
responses, including PID, instance token and component revisions. Do not run
against an existing service. Do not edit the repository's example in place.

1. Start the ordinary server from the copied folder, disabling bytecode writes:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 mlserver start .
   ```

2. From another terminal, wait for readiness and send an ordinary inference:

   ```sh
   curl --noproxy '*' --fail http://127.0.0.1:18083/v2/models/probe/ready
   curl --noproxy '*' --fail -H 'Content-Type: application/json' \
     -d '{"id":"probe-0","inputs":[{"name":"x","shape":[1],"datatype":"INT64","data":[0]}]}' \
     http://127.0.0.1:18083/v2/models/probe/infer
   ```

3. In the copy, change `HELPER_VALUE = 1` to `10` and revision to `helper-r2`.
   Keep the server running. Source size changes and bytecode suppression prevent
   a same-size/mtime `.pyc` artefact from imitating an import-cache result. Reload
   using the public repository API, then repeat inference with a new request ID:

   ```sh
   curl --noproxy '*' --fail -H 'Content-Type: application/json' -d '{}' \
     http://127.0.0.1:18083/v2/repository/models/probe/load
   ```

4. Change only `RUNTIME_OFFSET = 0` to `100` and revision to `runtime-r2`.
   Repeat the same public reload and another inference.
5. Stop the process normally, confirm it exited, restart with the same final
   source files, and repeat inference. Confirm the response PID changed.

Observed configured native execution:

| Stage | Runtime/helper actually served | Output for x=0 |
| --- | --- | ---: |
| Initial | runtime-r1 / helper-r1 | 1 |
| Helper edited, API reload | runtime-r1 / helper-r1 | 1 |
| Runtime edited, API reload | runtime-r2 / helper-r1 | 101 |
| Fresh process, unchanged final disk | runtime-r2 / helper-r2 | 110 |

The instance token changed at each successful public reload; same-process PID
did not. A successful load acknowledgement therefore did not imply refresh of
the already imported helper. Among **these two tested actions**, restart repaired
future helper freshness and model reload did not. This does not prove the least
possible repair, a runtime fault in every configuration, or a framework bug.

The original execution retained6 inference responses,2 successful repository
loads, source hashes, package RECORD checks and raw server logs. Initial socket
denial/startup retries were retained separately. Formatting/comment-only edits
make this public example semantically equivalent to the executed source, not
the original byte-identical historic artifact. No published host/PID/private path
is required to reproduce the mechanism.

Source anchors: [native implementation reload](https://github.com/SeldonIO/MLServer/blob/1d1f3ee42f96744d809aca941ed2925347d198e9/mlserver/settings.py#L60-L66),
[repository API](https://github.com/SeldonIO/MLServer/blob/1d1f3ee42f96744d809aca941ed2925347d198e9/mlserver/handlers/model_repository.py#L62-L80),
[Python reload contract](https://docs.python.org/3/library/importlib.html#importlib.reload).
Parallel-worker configurations, recursive helper reload, unload/load and repair
timing were not evaluated here. Maintainer contract adjudication remains absent.
