/* A small headless Chrome driver over the DevTools protocol, with no package to install: Node's own
   WebSocket and fetch. Set CHROME_PATH where Chrome is not at the macOS default. */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function launch({ port = 9361 } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "aletheia-e2e-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });
  let target;
  for (let i = 0; i < 75 && !target; i++) {
    await sleep(200);
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch { /* not up yet */ }
  }
  if (!target) { chrome.kill(); throw new Error(`Chrome did not start (${CHROME})`); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, no) => { ws.addEventListener("open", ok); ws.addEventListener("error", no); });
  let n = 0;
  const waiting = new Map();
  const dialogs = [];
  /* Requests to /api/<method> that a test holds back, refuses or rewrites. */
  const routes = new Map();
  const paused = async p => {
    const method = new URL(p.request.url).pathname.split("/").pop();
    const route = routes.get(method);
    if (!route) return send("Fetch.continueRequest", { requestId: p.requestId });
    if (p.responseStatusCode !== undefined) {
      const body = (await send("Fetch.getResponseBody", { requestId: p.requestId })).result;
      const text = body.base64Encoded ? Buffer.from(body.body, "base64").toString("utf8") : body.body;
      const next = p.responseStatusCode === 200 ? route.rewrite(JSON.parse(text)) : JSON.parse(text);
      return send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: p.responseStatusCode, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(next)).toString("base64") });
    }
    route.seen.push(p.requestId);
    route.waiting.push(p.requestId);
    route.arrived?.();
  };
  const syncFetch = () => send("Fetch.enable", { patterns: [...routes.entries()].map(([m, r]) => ({ urlPattern: `*/api/${m}`, requestStage: r.rewrite ? "Response" : "Request" })) });
  ws.addEventListener("message", e => {
    const m = JSON.parse(e.data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
    // the app asks before a reload loses review marks (beforeunload): a test moving on says yes, and it is recorded
    else if (m.method === "Page.javascriptDialogOpening") { dialogs.push(m.params.type); send("Page.handleJavaScriptDialog", { accept: true }); }
    else if (m.method === "Fetch.requestPaused") paused(m.params);
  });
  const send = (method, params = {}) => new Promise(r => { const id = ++n; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });

  const page = {
    send,
    /** Native dialogs seen so far, such as "beforeunload". */
    dialogs,
    /** Holds every call to this API method until the test says what happens to it. */
    async hold(method) {
      const route = { seen: [], waiting: [] };
      routes.set(method, route);
      await syncFetch();
      const next = () => route.waiting.shift();
      const end = async () => { routes.delete(method); if (routes.size) await syncFetch(); else await send("Fetch.disable"); };
      return {
        seen: () => route.seen.length,
        arrived: () => new Promise(r => { if (route.waiting.length) r(); else route.arrived = r; }),
        async release() { for (let id; (id = next());) await send("Fetch.continueRequest", { requestId: id }); await end(); },
        /** The connection drops: the service may or may not have acted. */
        async lose() { for (let id; (id = next());) await send("Fetch.failRequest", { requestId: id, errorReason: "ConnectionReset" }); await end(); },
        async answer(status, body) { for (let id; (id = next());) await send("Fetch.fulfillRequest", { requestId: id, responseCode: status, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(body)).toString("base64") }); await end(); },
      };
    },
    /** Replaces what the real service answered to this method. */
    async rewrite(method, rewrite) { routes.set(method, { seen: [], waiting: [], rewrite }); await syncFetch(); },
    async unroute() { routes.clear(); await send("Fetch.disable"); },
    /** Sets a select or input the way a person's change does. */
    set: (selector, value) => page.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) throw new Error("no field: ${selector.replace(/"/g, "'")}"); const proto = e.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, "value").set.call(e, ${JSON.stringify(value)}); e.dispatchEvent(new Event(e.tagName === "SELECT" ? "change" : "input", { bubbles: true })); return true; })()`),
    /** A finger on the element's centre (Chrome's touch emulation). */
    async tap(selector) {
      const at = await page.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({ block: "center" }); const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      await page.frames(2);
      await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [at] });
      await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await page.frames(3);
    },
    resize: (width, height) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false }),
    /** A PNG of the whole page, not only the part in view. */
    shot: async (file, full = false) => {
      const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full });
      writeFileSync(file, Buffer.from(r.result.data, "base64"));
    },
    async eval(expression) {
      const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text);
      return r.result?.result?.value;
    },
    /** A fresh page at this address: storage cleared, so no run leaks into the next. */
    async open(url) {
      await send("Page.navigate", { url: new URL(url).origin + "/?blank" });
      await sleep(150);
      await page.eval("localStorage.clear(); sessionStorage.clear(); true");
      await send("Page.navigate", { url });
      await page.until("document.querySelector('h1') !== null");
      await page.frames(2);
    },
    async until(expression, ms = 4000) {
      const end = Date.now() + ms;
      while (Date.now() < end) { if (await page.eval(`!!(${expression})`)) return; await sleep(50); }
      throw new Error(`timed out waiting for: ${expression}`);
    },
    frames: k => page.eval(`new Promise(r => { let i = ${k}; const f = () => (--i ? requestAnimationFrame(f) : r(true)); requestAnimationFrame(f); })`),
    async key(name) {
      const KEYS = { Tab: 9, Enter: 13, Escape: 27, " ": 32, ArrowDown: 40, ArrowUp: 38, x: 88 };
      const code = KEYS[name];
      // Enter and Space carry their text, or the browser never "presses" the focused button
      const text = name === "Enter" ? "\r" : name === " " ? " " : name.length === 1 ? name : undefined;
      await send("Input.dispatchKeyEvent", { type: "keyDown", windowsVirtualKeyCode: code, key: name, code: name === " " ? "Space" : name.length === 1 ? "Key" + name.toUpperCase() : name, text });
      await send("Input.dispatchKeyEvent", { type: "keyUp", windowsVirtualKeyCode: code, key: name, code: name === " " ? "Space" : name.length === 1 ? "Key" + name.toUpperCase() : name });
      await page.frames(2);
    },
    async type(text) { await send("Input.insertText", { text }); await page.frames(1); },
    /** Focus an element the way a keyboard user reaches it. */
    focus: selector => page.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) throw new Error("no element: ${selector.replace(/"/g, "'")}"); el.focus(); return true; })()`),
    click: selector => page.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) throw new Error("no element: ${selector.replace(/"/g, "'")}"); el.click(); return true; })()`),
    /** A button by its visible text, for actions that have no stable selector. */
    clickText: text => page.eval(`(() => { const el = [...document.querySelectorAll("button, a")].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})); if (!el) throw new Error("no button: ${text}"); el.click(); return true; })()`),
    active: () => page.eval(`(() => { const a = document.activeElement; return a ? { tag: a.tagName.toLowerCase(), id: a.id, cls: String(a.className), text: (a.getAttribute("aria-label") || a.textContent || "").trim().slice(0, 60), body: a === document.body, inReader: !!a.closest(".insp"), inDialog: !!a.closest("[role=dialog]") } : null; })()`),
  };
  // Chrome is still writing its profile as it exits: wait for the exit, then remove it
  const close = async () => {
    ws.close();
    const exited = new Promise(r => chrome.once("exit", r));
    chrome.kill();
    await Promise.race([exited, sleep(3000)]);
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  };
  return { page, close };
}
