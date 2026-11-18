import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';

const input = createInterface({ input: process.stdin });
const spec = JSON.parse((await input[Symbol.asyncIterator]().next()).value);
input.close();
const { launch } = await import(pathToFileURL(spec.driver));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const records = [];
let stage = 'launch', browser, second, secondSocket;
const record = (name, started, details = {}) => {
  const value = { case: name, status: 'pass', elapsed_ms: performance.now() - started, ...details };
  records.push(value);
  process.stdout.write(JSON.stringify(value) + '\n');
};

function totp(seed, timestamp) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const c of seed.replaceAll('=', '')) bits += alphabet.indexOf(c).toString(2).padStart(5, '0');
  const bytes = [];
  for (let n = 0; n + 8 <= bits.length; n += 8) bytes.push(parseInt(bits.slice(n, n + 8), 2));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(timestamp / 30000)));
  const digest = createHmac('sha1', Buffer.from(bytes)).update(counter).digest();
  const offset = digest.at(-1) & 15;
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(6, '0');
}

async function session(page) {
  return page.eval(`fetch('/v1/session',{credentials:'include',cache:'no-store'}).then(async r=>({status:r.status,actor:r.ok?(await r.json()).actor.user_id:null}))`);
}

async function login(page, username, password, seed, lastStep) {
  const priorSession = (await page.send('Storage.getCookies')).result.cookies.find(c => c.name === spec.session_cookie)?.value ?? null;
  stage = 'signin_link';
  await page.until(`document.querySelector('a[href="/v1/auth/start"]')`, 12000);
  await page.click('a[href^="/v1/auth/start"]');
  stage = 'provider_password_form';
  await page.until(`document.querySelector('input[name="password"]')`, 20000);
  assert.equal(new URL(await page.eval('location.href')).origin, spec.idp_origin);
  const editableUsername = `document.querySelector('input[name="username"]')?.type!=='hidden' && document.querySelector('input[name="username"]')?.readOnly===false`;
  if (!(await page.eval(editableUsername))) {
    await page.until(`document.querySelector('#reset-login')`, 8000);
    await page.click('#reset-login');
    await page.until(`(${editableUsername}) && document.querySelector('input[name="password"]')`, 12000);
  }
  assert.ok(await page.eval(editableUsername));
  await page.set('input[name="username"]', username);
  await page.set('input[name="password"]', password);
  await page.eval(`document.querySelector('input[name="password"]').form.requestSubmit()`);
  stage = 'provider_otp_form';
  await page.until(`document.querySelector('input[name="otp"]')`, 12000);
  const nearby = [-1, 0, 1].map(offset => totp(seed, Date.now() + offset * 30000));
  const bad = Array.from({length: 100}, (_, n) => String(n).padStart(6, '0')).find(v => !nearby.includes(v));
  await page.set('input[name="otp"]', bad);
  await page.eval(`document.querySelector('input[name="otp"]').form.requestSubmit()`);
  stage = 'provider_bad_otp_denial';
  await page.until(`/Invalid|invalid/i.test((document.body?.innerText ?? '')) && document.querySelector('input[name="otp"]')`, 12000);
  assert.equal((await page.send('Storage.getCookies')).result.cookies.find(c => c.name === spec.session_cookie)?.value ?? null, priorSession);
  while (Math.floor(Date.now() / 30000) <= lastStep) await sleep(200);
  const step = Math.floor(Date.now() / 30000);
  await page.set('input[name="otp"]', totp(seed, Date.now()));
  await page.eval(`document.querySelector('input[name="otp"]').form.requestSubmit()`);
  stage = 'callback_and_workspace';
  await page.until(`location.origin===${JSON.stringify(spec.origin)} && !location.pathname.includes('/v1/auth/callback')`, 15000);
  await page.until(`(document.body?.innerText ?? '').includes(${JSON.stringify(username === 'recovery-user' ? 'K03 Workspace A' : 'K03 Workspace B')})`, 15000);
  return step;
}

async function attachTab(page) {
  const id = (await page.send('Target.createTarget', { url: spec.origin })).result.targetId;
  let target;
  for (let n = 0; n < 40 && !target; n++) {
    target = (await (await fetch(`http://127.0.0.1:${spec.debug_port}/json/list`)).json()).find(t => t.id === id);
    if (!target) await sleep(100);
  }
  assert.ok(target);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve); ws.addEventListener('error', reject); });
  let sequence = 0;
  const waiting = new Map();
  ws.addEventListener('message', event => {
    const m = JSON.parse(event.data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise(resolve => {
    const n = ++sequence; waiting.set(n, resolve); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const evalValue = async expression => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.error || r.result.exceptionDetails) throw new Error('Secondary tab evaluation failed');
    return r.result.result.value;
  };
  await send('Page.enable');
  return { eval: evalValue, ws, id };
}

try {
  browser = await launch({ port: spec.debug_port });
  const page = browser.page;
  const version = (await page.send('Browser.getVersion')).result.product;
  await page.open(spec.origin);
  stage = 'real_browser_mfa';
  let started = performance.now();
  let lastA = await login(page, 'recovery-user', spec.password_a, spec.seed_a, spec.last_a);
  assert.deepEqual(await session(page), { status: 200, actor: 'k03_a' });
  const cookies = (await page.send('Storage.getCookies')).result.cookies.filter(c => c.domain === '127.0.0.1');
  const credential = cookies.find(c => c.name === spec.session_cookie);
  assert.ok(credential && credential.secure && credential.httpOnly && credential.sameSite === 'Lax' && credential.path === '/');
  const stored = await page.eval(`JSON.stringify({local:{...localStorage},session:{...sessionStorage},visible:document.cookie})`);
  assert.ok(!stored.includes(credential.value) && !stored.includes(spec.password_a) && !stored.includes(spec.seed_a));
  record('chrome_real_mfa_callback_secure_cookie_and_storage', started, { chrome: version, bad_otp_denied: true, session_status: 200 });

  stage = 'two_tabs'; started = performance.now();
  second = await attachTab(page); secondSocket = second.ws;
  for (let n = 0; n < 100 && !(await second.eval(`(document.body?.innerText ?? '').includes('K03 Workspace A')`)); n++) await sleep(50);
  assert.deepEqual(await session(second), { status: 200, actor: 'k03_a' });
  record('chrome_second_tab_same_real_session', started);

  stage = 'logout_all_propagation';
  await page.clickText('Sign out all sessions');
  started = performance.now();
  let cleared = false;
  while (performance.now() - started < 5000) {
    if (!(await second.eval(`(document.body?.innerText ?? '').includes('K03 Workspace A')`)) && (await session(second)).status === 401) { cleared = true; break; }
    await sleep(50);
  }
  assert.ok(cleared);
  assert.equal((await session(page)).status, 401);
  record('chrome_logout_all_clears_other_tab_within_5s', started, { deadline_ms: 5000 });

  stage = 'relogin_after_local_logout'; started = performance.now();
  lastA = await login(page, 'recovery-user', spec.password_a, spec.seed_a, lastA);
  assert.deepEqual(await session(page), { status: 200, actor: 'k03_a' });
  record('chrome_real_mfa_relogin_after_local_logout', started);

  stage = 'idp_logout_confirmation'; started = performance.now();
  await page.clickText('Sign out');
  await page.until(`location.origin===${JSON.stringify(spec.idp_origin)} && document.querySelector('#kc-logout, input[name="confirmLogout"], button[name="confirmLogout"]')`, 15000);
  await page.click('#kc-logout, input[name="confirmLogout"], button[name="confirmLogout"]');
  await page.until(`location.origin===${JSON.stringify(spec.origin)} || document.querySelector('a[href="${spec.origin}/"]')`, 15000);
  if (new URL(await page.eval('location.href')).origin !== spec.origin) await page.click(`a[href="${spec.origin}/"]`);
  await page.until(`location.origin===${JSON.stringify(spec.origin)} && document.querySelector('a[href="/v1/auth/start"]')`, 15000);
  assert.equal((await session(page)).status, 401);
  record('chrome_product_logout_confirms_real_idp_logout', started);

  stage = 'account_switch'; started = performance.now();
  await login(page, 'second-user', spec.password_b, spec.seed_b, spec.last_b);
  assert.deepEqual(await session(page), { status: 200, actor: 'k03_b' });
  assert.equal(await page.eval(`(document.body?.innerText ?? '').includes('K03 Workspace A')`), false);
  assert.ok(await page.eval(`(document.body?.innerText ?? '').includes('K03 Workspace B')`));
  record('chrome_b_login_after_product_and_idp_logout_no_a_workspace', started, { mode: 'logout-idp-then-login' });

  stage = 'history_after_switch'; started = performance.now();
  await page.send('Page.navigate', { url: spec.origin + '/#/workspaces' });
  await page.until(`(document.body?.innerText ?? '').includes('K03 Workspace B')`, 12000);
  assert.equal(await page.eval(`(document.body?.innerText ?? '').includes('K03 Workspace A')`), false);
  assert.deepEqual(await session(page), { status: 200, actor: 'k03_b' });
  record('chrome_current_account_route_revalidation', started, { bfcache_qualification: false });

  process.stdout.write(JSON.stringify({ summary: 'pass', cases: records.length, last_a: lastA }) + '\n');
} catch (error) {
  let view;
  try { view = await browser.page.eval(`({origin:location.origin,path:location.pathname,headings:[...document.querySelectorAll('h1,h2')].map(x=>x.textContent),provider_error:(document.querySelector('#kc-error-message')?.textContent??'').slice(0,350),inputs:[...document.querySelectorAll('input')].map(x=>x.name),buttons:[...document.querySelectorAll('button,a')].map(x=>x.textContent.trim().slice(0,60)).slice(0,15)})`); } catch {}
  const kind = error.message?.startsWith('timed out waiting') ? 'wait_timeout' : error.message?.includes('context') ? 'navigation_context' : 'evaluation_or_assertion';
  process.stdout.write(JSON.stringify({ summary: 'failed', stage, error: error.name, kind, view, cases: records.length }) + '\n');
  process.exitCode = 1;
} finally {
  if (secondSocket) secondSocket.close();
  if (browser) await browser.close();
}
