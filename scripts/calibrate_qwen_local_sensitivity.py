#!/usr/bin/env python3
"""Run the frozen, outcome-blind local Qwen operational calibration."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_json
from aletheia_lab.evaluation.qwen_local_calibration import (
    QwenCalibrationError,
    build_calibration_receipt,
    build_calibration_requests,
    frozen_server_flags,
    load_and_verify_candidate,
    require_loopback_base_url,
    run_development_calibration,
    sample_process_rss_kib,
    verify_local_artifacts,
    wait_until_healthy,
)


def _write_new(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("configs/evaluation/diagnosis_qwen_local_sensitivity_candidate.json"),
    )
    parser.add_argument(
        "--development-plan",
        type=Path,
        default=Path("configs/evaluation/diagnosis_development_pilot_plan.json"),
    )
    parser.add_argument(
        "--response-contract",
        type=Path,
        default=Path("configs/evaluation/diagnosis_main_response_contract.json"),
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--llama-checkout", type=Path, required=True)
    parser.add_argument("--source-tokenizer-config", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--server-log", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--load-timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    server: subprocess.Popen[str] | None = None
    log_handle = None
    try:
        candidate = load_and_verify_candidate(args.candidate)
        development_plan = json.loads(args.development_plan.read_text(encoding="utf-8"))
        response_contract = json.loads(args.response_contract.read_text(encoding="utf-8"))
        calibration_requests = build_calibration_requests(
            development_plan, response_contract
        )
        artifacts = verify_local_artifacts(
            candidate=candidate,
            model_path=args.model,
            llama_checkout=args.llama_checkout,
            source_tokenizer_config=args.source_tokenizer_config,
        )
        if args.receipt.exists() or args.server_log.exists():
            raise QwenCalibrationError("receipt and server log paths must not already exist")
        args.server_log.parent.mkdir(parents=True, exist_ok=True)
        binary = args.llama_checkout / "build/bin/llama-server"
        server_flags = frozen_server_flags(candidate, args.port)
        command = [
            str(binary),
            "--model",
            str(args.model),
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--ctx-size",
            "32768",
            "--n-predict",
            "600",
            "--n-gpu-layers",
            "99",
            "--jinja",
            "--no-context-shift",
            "--temp",
            "0.7",
            "--top-p",
            "0.8",
            "--top-k",
            "20",
            "--min-p",
            "0",
            "--repeat-penalty",
            "1.05",
            "--seed",
            "17",
        ]
        base_url = require_loopback_base_url(f"http://127.0.0.1:{args.port}")
        log_handle = args.server_log.open("x", encoding="utf-8")
        server = subprocess.Popen(  # noqa: S603
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_until_healthy(base_url, server, args.load_timeout_seconds)
        rss_samples_kib = [sample_process_rss_kib(server.pid)]
        generation_policy = candidate.get("generation_policy")
        if not isinstance(generation_policy, dict):
            raise QwenCalibrationError("candidate generation policy is malformed")
        records = run_development_calibration(
            base_url=base_url,
            requests=calibration_requests,
            response_schema=response_contract["json_schema"],
            timeout=int(generation_policy["timeout_seconds"]),
        )
        rss_samples_kib.append(sample_process_rss_kib(server.pid))
        observed_peak_gib = max(rss_samples_kib) / 1024**2
        host_envelope = candidate.get("host_envelope")
        if not isinstance(host_envelope, dict):
            raise QwenCalibrationError("candidate host envelope is malformed")
        maximum_peak_gib = float(host_envelope["maximum_observed_peak_memory_gib"])
        if observed_peak_gib > maximum_peak_gib:
            raise QwenCalibrationError("observed process RSS exceeds frozen host envelope")
        artifacts = {
            **artifacts,
            "process_rss_samples_kib": tuple(rss_samples_kib),
            "observed_process_rss_peak_gib": round(observed_peak_gib, 3),
            "maximum_observed_peak_memory_gib": maximum_peak_gib,
            "memory_measurement_scope": (
                "process_rss_proxy; transient or driver-level Metal allocations may not be "
                "fully represented"
            ),
        }
        receipt = build_calibration_receipt(
            candidate=candidate,
            development_plan=development_plan,
            response_contract=response_contract,
            artifacts=artifacts,
            records=records,
            server_flags=server_flags,
        )
        _write_new(args.receipt, receipt)
    except (OSError, KeyError, TypeError, ValueError, QwenCalibrationError) as exc:
        print(json.dumps({"status": "fail", "error_type": type(exc).__name__}))
        return 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        if log_handle is not None:
            log_handle.close()
    print(canonical_execution_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
