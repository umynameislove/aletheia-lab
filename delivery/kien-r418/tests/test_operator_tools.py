"""Operator wrapper correctness only. No Docker/Windows/PG workload is executed."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

STAGE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, STAGE / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


RUN = module("run_checks")
CAPTURE = module("capture_operator")


def receipt():
    return {
        "schema_version": "k07-transfer-observation/v2",
        "observation_kind": "completed",
        "mib": 200,
        "byte_size": 200 * 1024**2,
        "part_count": 50,
        "ready": True,
        "sha256": "a" * 64,
        "stored_sha256": "a" * 64,
        "confirmed_progress": [i * 4 * 1024**2 for i in range(1, 51)],
        "recovered_or_retried_unknown": False,
    }


def junit(path, child=""):
    path.write_text(
        '<testsuites><testsuite><testcase name="physical[L-200MiB]">'
        + child
        + "</testcase></testsuite></testsuites>"
    )


class OperatorTools(unittest.TestCase):
    def test_phase_selection_keeps_real200_and_platform_smoke(self):
        self.assertEqual(len(RUN.selections("smoke")), 4)
        self.assertEqual(
            RUN.selections("upload200")[-1],
            "tests/integration/test_k07_upload_sizes_postgres.py::test_real_physical_resumable_transfer_to_verified_source[L-200MiB]",
        )
        self.assertIn("tests/unit/test_k07_upload_deadline.py", RUN.selections("upload200"))

    def test_full_inventory_discovers_k18_and_marked_integration(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "tests/unit").mkdir(parents=True)
            (source / "tests/integration").mkdir()
            (source / "tests/unit/test_k18_new.py").write_text(
                "@pytest.mark.integration\ndef test_real(): pass\n"
            )
            (source / "tests/integration/test_k17_new.py").write_text("")
            with patch.object(RUN, "SOURCE", source):
                self.assertIn("tests/unit/test_k18_new.py", RUN.selections("unit"))
                self.assertIn("tests/unit/test_k18_new.py::test_real", RUN.selections("postgres"))
                self.assertIn("tests/integration/test_k17_new.py", RUN.selections("postgres"))

    def test_strict200_requires_hash_progress_receipt_and_no_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            junit(output / "junit.xml")
            self.assertFalse(RUN.strict_gate(output, "upload200", 0)["passed"])
            target = output / "transfer-200MiB-result.json"
            target.write_text(json.dumps(receipt()))
            self.assertTrue(RUN.strict_gate(output, "upload200", 0)["passed"])
            invalid = receipt()
            invalid["stored_sha256"] = "b" * 64
            target.write_text(json.dumps(invalid))
            self.assertFalse(RUN.strict_gate(output, "upload200", 0)["passed"])
            target.write_text(json.dumps(receipt()))
            junit(output / "junit.xml", '<skipped message="not executed"/>')
            self.assertFalse(RUN.strict_gate(output, "upload200", 0)["passed"])
            junit(output / "junit.xml")
            (output / "transfer-200MiB_failure-result.json").write_text("{}")
            self.assertFalse(RUN.strict_gate(output, "upload200", 0)["passed"])
            self.assertFalse(RUN.strict_gate(output, "upload200", 1)["passed"])

    def test_sanitizes_known_private_dsn_password_and_junit(self):
        private = "postgresql://fixture:secret-private@localhost:55435/test"
        cleaned = RUN.sanitize(
            private + " secret-private postgres://other:unknown@localhost/db",
            (private, "secret-private"),
        )
        self.assertNotIn("secret-private", cleaned)
        self.assertNotIn("unknown", cleaned)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "junit.xml"
            target.write_text("<testcase><failure>" + private + "</failure></testcase>")
            RUN.sanitize_junit(target, (private, "secret-private"))
            self.assertNotIn("secret-private", target.read_text())

    def test_manifest_uses_dynamic_counts_and_rejects_fake_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            source = kit / "source"
            source.mkdir()
            (source / "file.py").write_bytes(b"pass\n")
            files = {"file.py": hashlib.sha256(b"pass\n").hexdigest()}
            identity = hashlib.sha256(
                json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            manifest = {
                "files": files,
                "runtime": files,
                "file_count": 1,
                "runtime_count": 1,
                "source_identity": identity,
                "runtime_identity": identity,
            }
            (kit / "source-manifest.json").write_text(json.dumps(manifest))
            with patch.object(RUN, "KIT", kit), patch.object(RUN, "SOURCE", source):
                self.assertEqual(RUN.verify()["runtime_count"], 1)
                manifest["runtime_count"] = 639
                (kit / "source-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    RUN.verify()

    def test_main_creates_work_parent_and_clears_inherited_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            source = kit / "source"
            source.mkdir()
            database = kit / "private-url"
            database.write_text("postgresql://fixture:secret-private@localhost/test")
            output = kit / "results/upload-first"
            manifest = {
                "source_identity": "c" * 64,
                "runtime_identity": "d" * 64,
                "file_count": 2500,
                "runtime_count": 667,
            }

            def fake_run(command, *, cwd, env, log, secrets):
                self.assertEqual(cwd, source)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertNotIn("K06_RECOVERY_ALLOW", env)
                self.assertFalse(any(name.upper().startswith("PG") for name in env))
                self.assertEqual(env["K07_EVIDENCE_DIR"], str(output.resolve()))
                work = Path(
                    next(arg.split("=", 1)[1] for arg in command if arg.startswith("--basetemp="))
                )
                self.assertTrue(work.parent.is_dir())
                self.assertFalse(work.exists())
                junit(output / "junit.xml")
                (output / "transfer-200MiB-result.json").write_text(json.dumps(receipt()))
                return subprocess.CompletedProcess(command, 0)

            args = [
                "run_checks.py",
                "upload200",
                "--database-file",
                str(database),
                "--output",
                str(output),
            ]
            with (
                patch.object(RUN, "KIT", kit),
                patch.object(RUN, "SOURCE", source),
                patch.object(RUN, "verify", return_value=manifest),
                patch.object(RUN, "cleanup_generated_caches", return_value={}),
                patch.object(RUN, "run_logged", side_effect=fake_run),
                patch.object(sys, "argv", args),
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "must-never-reach-child",
                        "K06_RECOVERY_ALLOW": "owned-container-only",
                        "PGHOSTADDR": "203.0.113.10",
                        "PGSERVICE": "other-database",
                        "PGOPTIONS": "-c search_path=public",
                        "PgHost": "foreign",
                    },
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(RUN.main(), 0)
                saved = json.loads((output / "result.json").read_text())
                self.assertTrue(saved["qualified"])
                self.assertEqual(saved["runtime_count"], 667)
                self.assertEqual(saved["file_count"], 2500)
                self.assertNotIn("secret-private", (output / "attempt.json").read_text())
                # Output reuse is rejected without changing the earlier receipt.
                before = (output / "result.json").read_bytes()
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(RUN.main(), 1)
                self.assertEqual((output / "result.json").read_bytes(), before)

    def test_logs_only_keep_fixed_classes_never_private_queries(self):
        raw = "2026-11-13T01:02:03Z database system is ready to accept connections\nSTATEMENT: password=private; SELECT secret FROM users\n"
        summary = CAPTURE.summarize_logs(raw)
        self.assertEqual(summary["fixed_event_counts"]["ready"], 1)
        self.assertNotIn("private", json.dumps(summary))
        self.assertNotIn("SELECT", json.dumps(summary))

    def test_capture_uses_only_read_commands_and_discards_error_text(self):
        commands = []

        def fake(command, **kwargs):
            commands.append(command)
            if command[1] == "logs":
                return {
                    "status": "captured",
                    "exit_code": 0,
                    "_memory_only": "SECRET password=unknown",
                }
            if command[1] == "inspect":
                return {
                    "status": "captured",
                    "_memory_only": json.dumps({"image_id": "sha256:" + "a" * 64}),
                }
            if command[1] == "exec":
                return {
                    "status": "command_failed",
                    "exit_code": 1,
                    "_memory_only": "postgresql://user:unique-secret-zz@host/db",
                }
            return {"status": "captured", "_memory_only": "{}"}

        with patch.object(CAPTURE, "bounded", side_effect=fake):
            value = CAPTURE.capture("fixture-test", "hosted_test", "hosted_test")
        serialized = json.dumps(value)
        self.assertNotIn("unique-secret-zz", serialized)
        self.assertNotIn("unknown", serialized)
        self.assertTrue(
            all(
                command[1] in {"inspect", "info", "stats", "image", "exec", "logs"}
                for command in commands
            )
        )
        self.assertFalse(any(".Config.Env" in " ".join(command) for command in commands))
        self.assertIn(
            "default_transaction_read_only=on",
            " ".join(next(command for command in commands if command[1] == "exec")),
        )

    def test_capture_rejects_option_or_shell_injection(self):
        with self.assertRaises(ValueError):
            CAPTURE.capture("--all", "hosted_test", "hosted_test")
        with self.assertRaises(ValueError):
            CAPTURE.capture("fixture", "bad;user", "hosted_test")

    def test_recovery_requires_exact_separate_fixture_and_both_witnesses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "private-url"
            database.write_text("postgresql://k06:synthetic@127.0.0.1:55438/k06_recovery")
            config = root / "recovery.json"
            value = {
                "container_id": "a" * 64,
                "image_id": "sha256:" + "b" * 64,
                "container_name": "k06_recovery_" + "c" * 32,
                "port": 55438,
                "database_file": str(database),
            }
            config.write_text(json.dumps(value))
            env, private_file = RUN.load_recovery_config(config)
            self.assertEqual(env["K06_RECOVERY_ALLOW"], "owned-container-only")
            self.assertEqual(private_file, database)
            value["port"] = 55437
            config.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                RUN.load_recovery_config(config)
            (root / "junit.xml").write_text(
                '<testsuites><testsuite><testcase name="graceful"/><testcase name="crash"/></testsuite></testsuites>'
            )
            self.assertFalse(RUN.strict_gate(root, "recovery", 0)["passed"])
            (root / "recovery-evidence").mkdir()
            for name in ("graceful", "crash"):
                (root / "recovery-evidence" / (name + "-witness.json")).write_text(
                    json.dumps({"same_original_receipt": True, "durable_settings_on": True})
                )
            self.assertTrue(RUN.strict_gate(root, "recovery", 0)["passed"])

    def test_recovery_rejects_query_overrides_and_fragment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "private-url"
            config = root / "recovery.json"
            config.write_text(
                json.dumps(
                    {
                        "container_id": "a" * 64,
                        "image_id": "sha256:" + "b" * 64,
                        "container_name": "k06_recovery_" + "c" * 32,
                        "port": 55438,
                        "database_file": str(database),
                    }
                )
            )
            for suffix in (
                "?hostaddr=203.0.113.10&port=5432&user=admin&dbname=foreign",
                "#ignored",
                "?",
                "#",
            ):
                with self.subTest(suffix=suffix):
                    database.write_text(
                        "postgresql://k06:synthetic@127.0.0.1:55438/k06_recovery" + suffix
                    )
                    with self.assertRaises(ValueError):
                        RUN.load_recovery_config(config)

    def test_work_parent_rejects_symlink_junction_and_source_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            source = kit / "source"
            source.mkdir()
            with patch.object(RUN, "KIT", kit), patch.object(RUN, "SOURCE", source):
                (kit / "work").symlink_to(source, target_is_directory=True)
                with self.assertRaises(ValueError):
                    RUN.prepare_work_parent()
                (kit / "work").unlink()
                with patch.object(Path, "is_junction", return_value=True):
                    with self.assertRaises(ValueError):
                        RUN.prepare_work_parent()
                with patch.object(RUN, "SOURCE", kit):
                    with self.assertRaises(ValueError):
                        RUN.prepare_work_parent()
                self.assertTrue(RUN.prepare_work_parent().is_dir())

    def test_diagnostic_cli_child_does_not_inherit_libpq_environment(self):
        actual_popen = subprocess.Popen
        captured = []

        def observed(*args, **kwargs):
            captured.append(kwargs["env"])
            return actual_popen(*args, **kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "PGHOSTADDR": "203.0.113.10",
                    "PGSERVICE": "other",
                    "PGOPTIONS": "-c search_path=public",
                },
            ),
            patch.object(CAPTURE.subprocess, "Popen", side_effect=observed),
        ):
            value = CAPTURE.bounded([sys.executable, "-c", "print('selected')"])
        self.assertEqual(value["status"], "captured")
        self.assertFalse(any(name.upper().startswith("PG") for name in captured[0]))

    def test_optimized_interpreter_is_rejected_before_missing_source(self):
        result = subprocess.run(
            [sys.executable, "-I", "-O", str(STAGE / "tools/run_checks.py"), "verify"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("source_identity", result.stdout)

    def test_bounded_tool_output_and_deadline(self):
        result = CAPTURE.bounded([sys.executable, "-c", "print('x' * 1000000)"], timeout=5)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["_memory_only"].encode()), CAPTURE.MAXIMUM)
        result = CAPTURE.bounded([sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.05)
        self.assertTrue(result["timed_out"])


if __name__ == "__main__":
    unittest.main()
