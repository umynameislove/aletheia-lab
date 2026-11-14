"""Opt-in Chrome check of existing demo components, not hosted/J-02 acceptance.

Run this file with --run-browser; ordinary pytest starts no browser or server.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "dashboard" / "web"
SCOPE = "existing-demo-components-only"

# Uses the same native WebSocket/CDP approach as dashboard/web/tests/e2e/cdp.mjs.
# Browser-level sessions let both tabs share one isolated profile/channel.
SCRIPT = r"""
import assert from 'node:assert/strict';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const base = new URL(process.env.K03_BROWSER_BASE);
const endpoint = new URL(process.env.K03_BROWSER_CDP);
assert.equal(base.hostname, '127.0.0.1');
assert.equal(endpoint.hostname, '127.0.0.1');
const ws = new WebSocket(endpoint);
const pending = new Map();
let serial = 0;
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(Error('CDP connection deadline')), 5000);
  ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
  ws.addEventListener('error', () => { clearTimeout(timer); reject(Error('CDP unavailable')); }, { once: true });
});
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  const item = pending.get(message.id);
  if (!item) return;
  pending.delete(message.id); clearTimeout(item.timer);
  if (message.error) item.reject(Error('CDP command rejected'));
  else item.resolve(message.result);
});
function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { pending.delete(id); reject(Error('CDP command deadline')); }, 5000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}
async function tab() {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const command = (method, params) => send(method, params, sessionId);
  const evaluate = async expression => {
    const value = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (value.exceptionDetails) throw Error('Browser assertion expression failed');
    return value.result.value;
  };
  const until = async expression => {
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await pause(40);
    }
    throw Error('Component state deadline');
  };
  await command('Page.enable');
  await command('Page.navigate', { url: new URL('#/investigation', base).href });
  await until("!!document.querySelector('h1.question') && !!document.querySelector('.acct')");
  const clickText = text => evaluate(`(() => {
    const button = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)});
    if (!button) throw Error('Missing component control'); button.click(); return true;
  })()`);
  const preview = async text => {
    await evaluate("document.querySelector('.acct').click(); true");
    await until("!!document.querySelector('.acct-menu')");
    await clickText(text);
  };
  return { evaluate, until, clickText, preview };
}
try {
  const first = await tab();
  const question = await first.evaluate("document.querySelector('h1.question').textContent");
  assert.ok(question.length > 0);
  await first.preview('Preview: session ended');
  await first.until("document.querySelector('#session-title')?.textContent === 'Your session has ended' && document.activeElement?.id === 'session-title'");
  assert.equal(await first.evaluate("document.querySelector('.session-app').hidden && document.querySelector('.session-app').hasAttribute('inert')"), true);
  assert.equal(await first.evaluate("document.querySelector('h1.question').textContent"), question);
  await first.clickText('Sign in again');
  await first.until("!document.querySelector('.session-cover') && !document.querySelector('.session-app').hidden");
  assert.equal(await first.evaluate("document.querySelector('h1.question').textContent"), question);
  const second = await tab();
  await first.preview('Preview: access removed');
  for (const page of [first, second]) {
    await page.until("document.querySelector('#session-title')?.textContent === 'You no longer have access' && !document.querySelector('.session-app')");
    assert.equal(await page.evaluate(`document.body.textContent.includes(${JSON.stringify(question)})`), false);
    await page.evaluate("window.dispatchEvent(new Event('online')); window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); true");
  }
  await pause(150);
  for (const page of [first, second]) assert.equal(await page.evaluate("!document.querySelector('.session-app')"), true);
  console.log(JSON.stringify({ scope: 'existing-demo-components-only', expiry_cover: true,
    expiry_focus: true, same_context_resume: true, tabs: 2, broadcast_wipe: true,
    sensitive_dom_removed: true, terminal_after_browser_events: true }));
} finally {
  ws.close();
  for (const item of pending.values()) clearTimeout(item.timer);
}
"""


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-browser", action="store_true")
    parser.add_argument("--chrome", type=Path)
    args = parser.parse_args(argv)
    if not args.run_browser:
        parser.error("--run-browser is required; this check starts isolated local processes")
    return args


def _debugger_url(value: str) -> str:
    lines = value.splitlines()
    if len(lines) != 2 or not lines[0].isascii() or not lines[0].isdigit():
        raise ValueError("Invalid owned Chrome debugger endpoint")
    port = int(lines[0])
    suffix = lines[1].removeprefix("/devtools/browser/")
    if not 1 <= port <= 65535 or not lines[1].startswith("/devtools/browser/"):
        raise ValueError("Invalid owned Chrome debugger endpoint")
    if not suffix or any(char not in "0123456789abcdefABCDEF-" for char in suffix):
        raise ValueError("Invalid owned Chrome debugger endpoint")
    return f"ws://127.0.0.1:{port}{lines[1]}"


def _evidence(text: str) -> dict[str, object]:
    value = json.loads(text)
    fields = ("expiry_cover", "expiry_focus", "same_context_resume", "broadcast_wipe",
              "sensitive_dom_removed", "terminal_after_browser_events")
    if not isinstance(value, dict) or value.get("scope") != SCOPE or value.get("tabs") != 2:
        raise ValueError("Invalid browser component evidence")
    if any(value.get(field) is not True for field in fields):
        raise ValueError("Incomplete browser component evidence")
    return value


class _QuietStatic(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def _run(command: list[str], *, cwd: Path, environment: dict[str, str], timeout: int) -> str:
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Component command failed (exit {result.returncode}): {result.stderr[-2000:]}")
    return result.stdout


def _cleanup(directory, process, server, worker):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if server is not None:
        if worker is not None and worker.is_alive():
            server.shutdown()
        server.server_close()
    if worker is not None:
        worker.join(timeout=2)
    for attempt in range(10):
        try:
            shutil.rmtree(directory)
            break
        except PermissionError:
            if attempt == 9:
                print("K03_BROWSER_OWNED_TEMP_RETAINED=" + str(directory), flush=True)
            else:
                time.sleep(0.2)


def _live(chrome: Path | None) -> dict[str, object]:
    browser = chrome or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    browser = browser.resolve(strict=True)
    node = shutil.which("node")
    vite = WEB / "node_modules" / "vite" / "bin" / "vite.js"
    if not browser.is_file() or browser.name.lower() not in ("chrome.exe", "msedge.exe"):
        raise ValueError("Select an installed Chrome or Edge executable")
    if node is None or not vite.is_file():
        raise RuntimeError("Installed Node and local Vite are required; no packages are installed")
    directory = Path(tempfile.mkdtemp(prefix="aletheia-k03-browser-")).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if directory.parent != temporary_root or not directory.name.startswith("aletheia-k03-browser-"):
        raise ValueError("Unexpected owned component directory")
    process: subprocess.Popen[bytes] | None = None
    server: ThreadingHTTPServer | None = None
    worker: threading.Thread | None = None
    environment = dict(os.environ)
    try:
        _run([node, str(vite), "build", "--configLoader", "runner", "--outDir",
              str(directory / "dist"), "--emptyOutDir"], cwd=WEB,
             environment=environment, timeout=90)
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
            _QuietStatic, directory=str(directory / "dist")))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        profile = directory / ("profile-" + uuid4().hex)
        profile.mkdir()
        process = subprocess.Popen([str(browser), "--headless=new", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
            "--disable-component-update", "--no-pings", "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 15
        endpoint = profile / "DevToolsActivePort"
        while not endpoint.is_file():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Owned Chrome did not expose a debugger before its deadline")
            time.sleep(0.05)
        environment["K03_BROWSER_CDP"] = _debugger_url(endpoint.read_text(encoding="utf-8"))
        environment["K03_BROWSER_BASE"] = f"http://127.0.0.1:{server.server_port}/"
        script = directory / "check.mjs"
        script.write_text(SCRIPT, encoding="utf-8")
        return _evidence(_run([node, str(script)], cwd=directory,
                              environment=environment, timeout=45).strip())
    finally:
        _cleanup(directory, process, server, worker)


def test_browser_cli_requires_explicit_opt_in():
    with pytest.raises(SystemExit):
        _arguments([])
    assert _arguments(["--run-browser"]).run_browser is True


@pytest.mark.parametrize("value", ["0\n/devtools/browser/abc", "65536\n/devtools/browser/abc",
    "9222\nws://other.invalid/x", "9222\n/devtools/browser/../x", "9222\n/devtools/browser/abc\nextra"])
def test_debugger_file_cannot_select_a_nonlocal_or_unowned_endpoint(value):
    with pytest.raises(ValueError, match="owned Chrome"):
        _debugger_url(value)


def test_debugger_endpoint_is_always_loopback():
    assert _debugger_url("9222\n/devtools/browser/123-abc\n") == "ws://127.0.0.1:9222/devtools/browser/123-abc"


@pytest.mark.parametrize("payload", [{}, {"scope": SCOPE, "tabs": 2},
                                    {"scope": "hosted-acceptance", "tabs": 2}])
def test_component_evidence_cannot_claim_hosted_or_incomplete_acceptance(payload):
    with pytest.raises(ValueError):
        _evidence(json.dumps(payload))


if __name__ == "__main__":
    import sys

    arguments = _arguments(sys.argv[1:])
    print("K03_BROWSER_COMPONENT=" + json.dumps(_live(arguments.chrome), sort_keys=True), flush=True)
