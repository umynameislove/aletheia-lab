import hmac
import ipaddress
import json
import secrets
import ssl
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "live-drill"
PEER = "192.168.2.52"
ADDRESS = "192.168.2.61"
CONTROL = ROOT / "control"
CONTROL.mkdir(exist_ok=True)
TOKEN_FILE = ROOT / "operator-token.txt"
with TOKEN_FILE.open("x", encoding="ascii") as f:
    f.write(secrets.token_hex(24))
TOKEN = "Bearer " + TOKEN_FILE.read_text()

def records(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows

def recovery_open():
    gate = CONTROL / "may-recover"
    try:
        until = float(gate.read_text())
    except (ValueError, FileNotFoundError):
        return False
    return time.time() < until and not closed()

def closed():
    return (EVIDENCE / "result.json").exists() or (ROOT / "outcome.json").exists()

def state():
    current = records(EVIDENCE / "collector" / "collector.jsonl")
    queue = next((r for r in reversed(current) if r.get("source")=="queue"), {})
    result = EVIDENCE / "result.json"
    if not result.exists():
        result = ROOT / "outcome.json"
    return dict(
        sampling=bool(current), operator_ready=(CONTROL/"ready.json").exists(),
        disruption_started=(CONTROL/"outage.json").exists(), may_recover=recovery_open(),
        pending_jobs=queue.get("pending_count"), oldest_pending_seconds=queue.get("queue_oldest_seconds"),
        alerts=records(EVIDENCE/"receiver"/"receiver.jsonl"),
        recovery_requested=(CONTROL/"recover.json").exists(),
        result=json.loads(result.read_text()) if result.exists() and result.stat().st_size else None,
    )

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def respond(self, status, value):
        data = json.dumps(value,separators=(",",":")).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        valid_peer = str(ipaddress.ip_address(self.client_address[0])) in {PEER, ADDRESS}
        ok = valid_peer and hmac.compare_digest(self.headers.get("Authorization",""), TOKEN)
        if not ok:
            self.respond(403,{"error":"authorization required"})
        return ok

    def do_GET(self):
        if self.allowed():
            self.respond(200,state()) if self.path=="/state" else self.respond(404,{})

    def do_POST(self):
        if not self.allowed():
            return
        if self.client_address[0] != PEER or self.path not in ("/ready","/recover"):
            self.respond(403,{})
            return
        action = self.path.lstrip("/")
        if closed():
            self.respond(410,{"error":"exercise closed"})
            return
        if action=="recover" and not recovery_open():
            self.respond(409,{"error":"queue and burn alerts are not both confirmed"})
            return
        action_file = CONTROL/(action+".json")
        try:
            with action_file.open("x",encoding="utf-8") as stream:
                json.dump({"action":action,"observer":"Quan-MacBook","source_ip":PEER,
                           "utc":datetime.now(UTC).isoformat(),"epoch":time.time()},stream)
        except FileExistsError:
            pass
        self.respond(200,{"accepted":action})

    def log_message(self,*args):
        pass

private = Path("C:/t/aletheia-k14-staging-da2093353a73")
cert = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
cert.minimum_version = ssl.TLSVersion.TLSv1_2
cert.load_cert_chain(private/"api.crt",private/"api.key")
with ThreadingHTTPServer((ADDRESS,19016),Handler) as server:
    server.socket=cert.wrap_socket(server.socket,server_side=True)
    server.timeout=1
    print("K16_OPERATOR_PORTAL_READY",flush=True)
    until=time.monotonic()+3600
    while time.monotonic()<until:
        server.handle_request()
