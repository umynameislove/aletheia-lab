"""Bounded owned native process tree with exact observed listener verification."""

import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, body):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(body, indent=2, sort_keys=True) + "\n")


def allocations(directory):
    return sum(
        p.stat().st_blocks * 512 for p in directory.rglob("*") if p.is_file() and not p.is_symlink()
    )


def reserve_ports(count):
    held = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET)
        sock.bind(("127.0.0.1", 0))
        held.append(sock)
    ports = [s.getsockname()[1] for s in held]
    for sock in held:
        sock.close()
    return ports


class NativeGuard:
    def __init__(
        self,
        argv,
        environment,
        cwd,
        output,
        disk_root,
        seconds=120,
        rss=768 * 1024**2,
        disk=100 * 1024**2,
        torch_unbound=False,
    ):
        self.argv, self.environment, self.cwd = argv, environment, cwd
        self.output, self.disk_root = output, disk_root
        self.seconds, self.rss_limit, self.disk_limit = seconds, rss, disk
        self.torch_unbound = torch_unbound
        self.observed, self.connections, self.proofs = {}, {}, {}
        self.representation_proofs = {}
        self.failures, self.inspection_failures = [], []
        self.maximum_rss = self.maximum_disk = 0
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.log = (output / "native.log").open("xb")
        self.child = subprocess.Popen(
            argv,
            env=environment,
            cwd=cwd,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()

    def monitor(self):
        try:
            while not self.stop.wait(0.12) and self.child.poll() is None:
                parent = psutil.Process(self.child.pid)
                live = [parent, *parent.children(recursive=True)]
                total = psutil.Process(os.getpid()).memory_info().rss
                for proc in live:
                    try:
                        self.observed[proc.pid] = {
                            "pid": proc.pid,
                            "created": proc.create_time(),
                            "argv": proc.cmdline(),
                        }
                        total += proc.memory_info().rss
                        for conn in proc.net_connections(kind="inet"):
                            unbound = (
                                conn.status == "CLOSE"
                                and conn.laddr
                                and conn.laddr.ip == "0.0.0.0"
                                and conn.laddr.port == 0
                                and not conn.raddr
                            )
                            record = {
                                "pid": proc.pid,
                                "status": conn.status,
                                "family": int(conn.family),
                                "local": list(conn.laddr) if conn.laddr else None,
                                "remote": list(conn.raddr) if conn.raddr else None,
                                "excluded_unbound_descriptor": bool(unbound),
                            }
                            self.connections[
                                (proc.pid, str(conn.laddr), str(conn.raddr), conn.status)
                            ] = record
                            if not unbound:
                                for endpoint in (conn.laddr, conn.raddr):
                                    if endpoint and not self.qualified_loopback(endpoint, proc.pid):
                                        raise RuntimeError(
                                            "Observed bound/connected non-loopback endpoint"
                                        )
                            if conn.status == psutil.CONN_LISTEN:
                                key = (proc.pid, conn.laddr.port)
                                if key not in self.proofs:
                                    command = [
                                        "/usr/sbin/lsof",
                                        "-nP",
                                        "-a",
                                        "-p",
                                        str(proc.pid),
                                        "-iTCP:" + str(conn.laddr.port),
                                        "-sTCP:LISTEN",
                                    ]
                                    proof = subprocess.run(
                                        command, capture_output=True, text=True, timeout=3
                                    )
                                    good = (
                                        proof.returncode == 0
                                        and (str(proc.pid) in proof.stdout)
                                        and (
                                            "127.0.0.1:" + str(conn.laddr.port) in proof.stdout
                                            or "[::1]:" + str(conn.laddr.port) in proof.stdout
                                        )
                                    )
                                    self.proofs[key] = {
                                        **record,
                                        "pass": good,
                                        "returncode": proof.returncode,
                                        "stdout": proof.stdout,
                                        "stderr": proof.stderr,
                                    }
                                    if not good:
                                        raise RuntimeError(
                                            "Actual listener lacks exact PID/port lsof loopback proof"
                                        )
                    except psutil.NoSuchProcess:
                        continue
                self.maximum_rss = max(self.maximum_rss, total)
                self.maximum_disk = max(self.maximum_disk, allocations(self.disk_root))
                if total > self.rss_limit or self.maximum_disk > self.disk_limit:
                    raise RuntimeError("Prospective native RSS/disk ceiling exceeded")
                if time.monotonic() - self.started > self.seconds:
                    raise TimeoutError("Prospective native elapsed ceiling exceeded")
        except BaseException as exc:
            self.inspection_failures.append({"type": type(exc).__name__, "message": str(exc)})
            self.failures.append(str(exc))
            try:
                if self.child.poll() is None and os.getpgid(self.child.pid) == self.child.pid:
                    os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def qualified_loopback(self, endpoint, pid):
        address = ipaddress.ip_address(endpoint.ip)
        if address.is_loopback:
            return True
        # macOS psutil reports IPv4-compatible IPv6 for gRPC IPv4 sockets.
        # Never accept textual suffixes: independently verify the exact PID/port.
        if (
            sys.platform != "darwin"
            or not isinstance(address, ipaddress.IPv6Address)
            or address.packed[:12] != bytes(12)
        ):
            return False
        embedded = ipaddress.IPv4Address(address.packed[-4:])
        if not embedded.is_loopback:
            return False
        key = (pid, endpoint.ip, endpoint.port)
        if key not in self.representation_proofs:
            command = ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP:" + str(endpoint.port)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            network_names = re.findall(r"\bTCP\s+(\S+)", result.stdout)
            exact = str(embedded) + ":" + str(endpoint.port)
            endpoints = [part for name in network_names for part in name.split("->")]
            passed = result.returncode == 0 and exact in endpoints and str(pid) in result.stdout
            self.representation_proofs[key] = {
                "pid": pid,
                "psutil_ip": endpoint.ip,
                "port": endpoint.port,
                "expected_lsof_endpoint": exact,
                "pass": passed,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        return self.representation_proofs[key]["pass"]

    def alive(self):
        if self.failures or self.child.poll() is not None:
            raise RuntimeError("Native process exited or monitor failed: " + repr(self.failures))
        if time.monotonic() - self.started > self.seconds:
            raise TimeoutError("Native elapsed budget exhausted")

    def request(self, port, method, route, payload=None, seconds=3):
        self.alive()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=seconds)
        started = time.monotonic_ns()
        try:
            connection.request(
                method,
                route,
                None if payload is None else json.dumps(payload),
                {} if payload is None else {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(262145)
            if len(raw) > 262144:
                raise RuntimeError("Native response exceeds sealed payload ceiling")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                body = None
            return {
                "method": method,
                "route": route,
                "port": port,
                "status": response.status,
                "body": body,
                "raw_utf8": raw.decode("utf-8", errors="replace"),
                "parent_start_ns": started,
                "parent_end_ns": time.monotonic_ns(),
            }
        finally:
            connection.close()

    def ready(self, port, route, timeout=40):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            self.alive()
            try:
                receipt = self.request(port, "GET", route, seconds=1)
                if receipt["status"] == 200:
                    return receipt
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.15)
        raise TimeoutError("Readiness unavailable before bounded deadline")

    def close(self):
        # Keep observing watcher descendants until the spawning parent is stopped.
        if self.child.poll() is None and os.getpgid(self.child.pid) == self.child.pid:
            os.killpg(self.child.pid, signal.SIGTERM)
        try:
            self.child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(self.child.pid, signal.SIGKILL)
            self.child.wait(timeout=5)
        self.stop.set()
        self.thread.join(timeout=5)
        for item in reversed(list(self.observed.values())):
            try:
                proc = psutil.Process(item["pid"])
                if proc.create_time() == item["created"] and proc.status() != psutil.STATUS_ZOMBIE:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
            except psutil.NoSuchProcess:
                pass
        self.log.close()
        remaining = []
        for item in self.observed.values():
            try:
                proc = psutil.Process(item["pid"])
                if proc.create_time() == item["created"] and proc.status() != psutil.STATUS_ZOMBIE:
                    remaining.append(item["pid"])
            except psutil.NoSuchProcess:
                pass
        cleanup = []
        deadline = time.monotonic() + 8
        for port in sorted({key[1] for key in self.proofs}):
            while True:
                with socket.socket(socket.AF_INET) as sock:
                    sock.settimeout(0.3)
                    connected = sock.connect_ex(("127.0.0.1", port)) == 0
                proof = subprocess.run(
                    ["/usr/sbin/lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                closed = not connected and proof.returncode == 1 and not proof.stdout
                if closed or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            cleanup.append({"port": port, "closed": closed, "lsof": proof.stdout})
        result = {
            "argv": self.argv,
            "exit_code": self.child.returncode,
            "observed_owned_processes": list(self.observed.values()),
            "observed_connections": list(self.connections.values()),
            "native_listener_lsof_proofs": list(self.proofs.values()),
            "socket_representation_lsof_proofs": list(self.representation_proofs.values()),
            "inspection_failures": self.inspection_failures,
            "failures": self.failures,
            "maximum_sampled_owned_rss_bytes": self.maximum_rss,
            "maximum_allocated_owned_disk_bytes": self.maximum_disk,
            "elapsed_including_cleanup_seconds": time.monotonic() - self.started,
            "remaining_owned_pids": remaining,
            "port_cleanup": cleanup,
            "native_log_sha256": sha(self.output / "native.log"),
            "all_observed_listeners_qualified": bool(self.proofs)
            and all(p["pass"] for p in self.proofs.values()),
            "cleanup_complete": not remaining and all(p["closed"] for p in cleanup),
            "limits": "Observed owned process tree and sampled localhost TCP sockets; trusted host, no hostile-host or exhaustive network isolation proof.",
        }
        write_new(self.output / "guard-result.json", result)
        return result
