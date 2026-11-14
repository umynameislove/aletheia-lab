import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [driver, downloads, output] = process.argv.slice(2);
const fixture = JSON.parse(await new Promise(resolve => {
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => { input += chunk; });
  process.stdin.on('end', () => resolve(input));
}));
const { launch } = await import(pathToFileURL(driver));
const started = performance.now();
const browser = await launch({ port: fixture.debug_port });
const { page } = browser;
try {
  const version = (await page.send('Browser.getVersion')).result;
  await page.send('Network.enable');
  for (const cookie of fixture.cookie.split('; ')) {
    const split = cookie.indexOf('=');
    const name = cookie.slice(0, split);
    const set = await page.send('Network.setCookie', { name, value: cookie.slice(split + 1),
      url: fixture.origin, path: '/', secure: true, httpOnly: name.endsWith('_session') });
    assert.equal(set.result.success, true);
  }
  await page.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
  let fields;
  let lineage;
  const imports = [];
  if (fixture.upload_file) {
    for (let index = 0; index < (fixture.repeat_uploads ?? 1); index++) {
    await page.open(`${fixture.origin}/#/workspaces`);
    const workspace = fixture.workspace_name;
    const workspaceButton = `[...document.querySelectorAll('.checklist li')].find(li=>li.querySelector('b')?.textContent===${JSON.stringify(workspace)})?.querySelector('button')`;
    await page.until(workspaceButton, 15000);
    await page.eval(`if (!${workspaceButton}.disabled) ${workspaceButton}.click()`);
    await page.until(`[...document.querySelectorAll('button')].some(b=>b.textContent==='Connect project')`, 12000);
    await page.clickText('Connect project');
    await page.until(`document.querySelector('#hosted-project-zip')`, 10000);
    await page.send('DOM.enable');
    const doc = await page.send('DOM.getDocument');
    const input = await page.send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '#hosted-project-zip' });
    await page.send('DOM.setFileInputFiles', { nodeId: input.result.nodeId, files: [fixture.upload_file] });
    await page.until(`[...document.querySelectorAll('button')].some(b=>b.textContent==='Upload and preview' && !b.disabled)`, 10000);
    await page.clickText('Upload and preview');
    await page.until(`document.querySelector('[aria-label="Direction for độ_chính_xác"]')`, 45000);
    await page.set('#f-target-identifier-field', 'id');
    await page.set('[aria-label="Direction for độ_chính_xác"]', 'higher_is_better');
    await page.set('[aria-label="Regression threshold for độ_chính_xác"]', '0.08');
    await page.set('#f-baseline-run-id', 'lần-0');
    await page.set('#f-metric-sources-0-class-field', 'class');
    await page.set('#f-metric-sources-0-unit-field', 'unit');
    await page.set('#def-0-unit', '%');
    await page.clickText('Confirm mapping and save snapshot');
    await page.until(`document.querySelector('#hosted-first-question')`, 45000);
    await page.eval(`(() => { const e = document.querySelector('#hosted-first-question'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(e, 'What is observed?'); e.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await page.clickText('Analyze saved snapshot');
    await page.until(`document.querySelector('.verdict')`, 45000);
    fields = new URLSearchParams((await page.eval('location.hash')).split('?')[1]);
    assert.ok(fields.get('job') && fields.get('result') && fields.get('snapshot'));
    const lineageButton = `[...document.querySelectorAll('.claims-tools button')].find(b => b.textContent.trim() === 'Lineage')`;
    await page.until(lineageButton, 10000);
    await page.eval(`${lineageButton}.click()`);
    await page.until(`document.querySelectorAll('.tbl--edges tbody tr').length > 0`, 15000);
    lineage = await page.eval(`({
      rendered_nodes: document.querySelectorAll('.lg-row button[title]').length,
      visible_relation_rows: document.querySelectorAll('.tbl--edges tbody tr').length,
      edges_have_clickable_endpoints: [...document.querySelectorAll('.tbl--edges tbody tr')].every(row => row.querySelectorAll('button').length === 2),
    })`);
    assert.ok(lineage.visible_relation_rows > 0 && lineage.edges_have_clickable_endpoints);
    imports.push({ project_id: fields.get('project'), snapshot_id: fields.get('snapshot'), result_id: fields.get('result'), job_id: fields.get('job') });
    if (index > 0) {
      assert.equal(imports[index].project_id, imports[0].project_id);
      assert.equal(imports[index].snapshot_id, imports[0].snapshot_id, 'Identical ZIP confirmation reuses its first authorized snapshot');
      assert.equal(imports[index].result_id, imports[0].result_id, 'The same question remains a canonical result replay');
      assert.notEqual(imports[index].job_id, imports[index-1].job_id, 'Separate UI intents retain separate durable jobs');
    }
    }
  } else {
    fields = new URLSearchParams({ workspace: fixture.values.workspace_id, job: fixture.job, project: fixture.values.project_id,
      snapshot: fixture.values.snapshot_id, result: fixture.values.result_id });
  }
  await page.open(`${fixture.origin}/#/retention?${fields}`);
  const button = `[...document.querySelectorAll('.np-row')].find(r => r.innerText.startsWith('PDF'))?.querySelector('button')`;
  await page.until(`${button} && !${button}.disabled`, 15000);
  await page.eval(`${button}.click()`);
  await page.until(`document.body.innerText.includes('Sent to your browser as')`, 25000);
  let pdf;
  for (let i = 0; i < 40 && !pdf; i++) {
    pdf = (await readdir(downloads)).find(name => name.endsWith('.pdf'));
    if (!pdf) await new Promise(resolve => setTimeout(resolve, 150));
  }
  assert.ok(pdf, 'Chrome must save the PDF');
  const bytes = await readFile(join(downloads, pdf));
  assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
  const result = { file: pdf, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
    result_id: fields.get('result'), snapshot_id: fields.get('snapshot'), job_id: fields.get('job'),
    browser: { product: version.product, protocol: version.protocolVersion }, elapsed_seconds: (performance.now() - started) / 1000,
    uploaded_in_browser: Boolean(fixture.upload_file), imports, ...(lineage && { lineage }) };
  await writeFile(output, JSON.stringify(result, null, 2));
  if (fixture.session_checks_file) {
    const { checkSessionInTwoTabs } = await import("./browser_session_cases.mjs");
    await checkSessionInTwoTabs(page, fixture, fields);
  }
} catch (error) {
  console.error(error);
  try {
    await writeFile(output + '.failure.json', JSON.stringify({
      screen: await page.eval('document.body?.innerText ?? ""'),
      hash: await page.eval('location.hash'),
    }, null, 2));
    await page.shot(output + '.failure.png', true);
  } catch { /* preserve the original failure if Chrome has already exited */ }
  process.exitCode = 1;
} finally {
  await browser.close();
}
