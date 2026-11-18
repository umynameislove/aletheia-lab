import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { launch } from './cdp.mjs';

const root = 'C:/t/j02-final-20261116';
const cfg = JSON.parse(readFileSync(root+'/fixture-private.json'));
const origin = 'https://localhost:18776';
const target = 'idp-da2093353a73.test';
const mode = process.argv[2];
let nextPort = 9460;
const reports = {};
const active = [];
const traces = [];
const pause = ms => new Promise(done => setTimeout(done,ms));
const waitText = (page,text,ms=12000) => page.until(`document.body?.innerText.includes(${JSON.stringify(text)})`,ms);
const click = (page,label) => page.eval(`(()=>{const e=[...document.querySelectorAll('button,a,input[type=submit]')].find(x=>(x.value||x.textContent.trim())===${JSON.stringify(label)});if(!e||e.disabled)throw Error('Control unavailable');e.click();return true;})()`);
async function browser() {
  const item = await launch({port:nextPort++}); active.push(item);
  await item.page.send('Network.enable');
  item.page.on('Network.responseReceived', ({response}) => {
    const u = new URL(response.url);
    if (u.pathname.startsWith('/v1/') || u.hostname === target && u.pathname.includes('/logout')) {
      traces.push({browser:active.indexOf(item), path:u.pathname, status:response.status, at:new Date().toISOString()});
    }
  });
  return item.page;
}
const session = page => page.eval(`(async()=>{const r=await fetch('/v1/session',{cache:'no-store'});const j=await r.json();return {status:r.status, actor:j.actor?.user_id, code:j.code};})()`);
async function logIn(page, index, open=true) {
  if(open) await page.open(origin+'/#/workspaces');
  await waitText(page,'Sign in to Aletheia'); await click(page,'Sign in');
  await page.until(`location.hostname===${JSON.stringify(target)}&&!!document.querySelector('#kc-form-login')`,16000);
  await page.set('#username',cfg.actors[index].username); await page.set('#password',cfg.password);
  await page.click('#kc-login');
  await page.until(`location.origin===${JSON.stringify(origin)}&&document.body?.innerText.includes('Workspace access')`,16000);
  assert.deepEqual(await session(page),{status:200,actor:cfg.actors[index].user_id});
}
async function signOut(page, all) {
  const path=all?'/v1/auth/logout-all':'/v1/auth/logout';
  const count=traces.filter(x=>x.path===path).length;
  await click(page,all?'Sign out all sessions':'Sign out');
  await page.until(`location.hostname===${JSON.stringify(target)}&&location.pathname.includes('/logout')&&!!document.body`,12000);
  const destination=await page.eval(`({origin:location.origin, redirect:new URLSearchParams(location.search).get('post_logout_redirect_uri'),controls:[...document.querySelectorAll('button,input[type=submit]')].map(x=>({id:x.id,label:x.value||x.textContent}))})`);
  assert.equal(destination.origin,`https://${target}:18777`);
  const verifyRoute=destination.redirect??origin+'/';
  assert.equal(verifyRoute,origin+'/');
  if(await page.eval(`location.hostname===${JSON.stringify(target)}`)) {
    const control=destination.controls.find(x=>x.id==='kc-logout'||x.id==='kc-logout-confirm'||/^log\s*out$/i.test(x.label.trim()));
    assert.ok(control,'Keycloak confirmation control');
    if(control.id) await page.click('#'+control.id); else await click(page,control.label);
  }
  await page.until(`location.origin===${JSON.stringify(origin)}&&location.pathname==='/'&&document.body?.innerText.includes('Sign in to Aletheia')`,16000);
  assert.equal(traces.filter(x=>x.path===path).length,count+1);
  assert.equal(traces.filter(x=>x.path===path).at(-1).status,200);
  assert.equal((await session(page)).status,401);
  return {response:200,provider_confirmation:true,return_path:'/',old_app_session:401};
}
function link(index) {return '#/investigation?'+new URLSearchParams(cfg.links[index]);}
async function readResult(page,index) {
  const hash=link(index);
  await page.eval(`location.hash=${JSON.stringify(hash)}`);
  await page.until(`document.querySelector('.verdict')&&!document.querySelector('.session-cover')&&document.body?.innerText.includes(${JSON.stringify(`J02 2be4f511 actor ${index} observed metrics`)})`,15000);
}
async function report(name, work) {
  const start=performance.now(); reports[name]={status:'running'};
  const fields=await work();
  reports[name]={status:'pass',ms:Math.round(performance.now()-start),...fields};
  console.log('PASS',name,reports[name].ms);
}

try {
  if(mode==='auth'||mode==='all') {
    for(const all of [false,true]) await report(all?'logout_all_then_switch':'logout_then_switch',async()=>{
      const page=await browser(); await logIn(page,0);
      const peer=all?await browser():null;
      if(peer) await logIn(peer,0);
      const fields=await signOut(page,all);
      if(peer) assert.equal((await session(peer)).status,401);
      await logIn(page,1,false);
      assert.equal(await page.eval(`location.pathname`),'/');
      assert.equal(await page.eval(`document.body.innerText.includes('J02 verify 0')`),false);
      await page.shot(root+`/${all?'all':'normal'}-other-account.png`);
      return {...fields, login_form_after_logout:true, new_actor:'1', same_chrome_profile:true, other_app_session:all?401:undefined};
    });
  }
  if(mode==='copy'||mode==='all') await report('masked_invitation_can_be_copied_and_accepted',async()=>{
    const owner=await browser(), invited=await browser();
    await logIn(owner,0); await logIn(invited,2);
    await waitText(owner,'Invite a member');
    await owner.set('#workspace-invite-title + p + form input[type=email]',cfg.actors[2].email).catch(async()=>{
      await owner.set('input[type=email]',cfg.actors[2].email);
    });
    await click(owner,'Find accounts'); await waitText(owner,'J02 verify 2');
    await owner.eval(`(()=>{const e=[...document.querySelectorAll('input[type=radio]')].find(x=>x.closest('label')?.innerText.includes('J02 verify 2'))??document.querySelector('input[type=radio]');if(!e)throw Error('Target missing');e.click();return true;})()`);
    await click(owner,'Create invitation'); await waitText(owner,'Copy invitation token');
    await owner.send('Browser.grantPermissions',{origin,permissions:['clipboardReadWrite','clipboardSanitizedWrite']});
    await owner.send('Emulation.setFocusEmulationEnabled',{enabled:true});
    await click(owner,'Copy invitation token'); await waitText(owner,'Invitation token copied');
    const token=await owner.eval(`(async()=>{const input=document.querySelector('.notice input[type=password][readonly]');const value=await navigator.clipboard.readText();if(!input||value!==input.value)throw Error('Clipboard mismatch');await navigator.clipboard.writeText('');return value;})()`);
    assert.equal(typeof token,'string');
    await owner.shot(root+'/invitation-copied-masked.png');
    await invited.set('#workspace-accept-title + form input',token);await click(invited,'Accept invitation');
    await invited.until(`document.body?.innerText.includes('Your role')&&document.body?.innerText.includes('viewer')`,12000);
    assert.ok(traces.some(x=>x.path==='/v1/invitations/accept'&&x.status===200));
    return {clipboard_matched:true, token_remained_masked:true, accept_http:200};
  });
  if(mode==='link'||mode==='all') await report('revoked_workspace_can_open_a_new_result_in_the_same_document',async()=>{
    const owner=await browser(), viewer=await browser();
    await logIn(owner,0); await logIn(viewer,1);
    await readResult(viewer,0);
    await viewer.eval('window.j02SameDocument = true');
    const id=cfg.actors[1].user_id;
    const row=`document.querySelector(${JSON.stringify(`select[aria-label="Role for ${id}"]`)})?.closest('tr')`;
    await owner.until(row,8000);
    await owner.eval(`(${row}).querySelector('button.btn--danger').click();true`);
    await owner.until(`document.querySelector('[role=dialog]')`,6000);
    const start=performance.now();
    await owner.eval(`[...document.querySelectorAll('[role=dialog] button')].find(x=>x.textContent==='Remove access').click();true`);
    await viewer.until(`location.hash==='#/workspaces'&&document.body?.innerText.includes('Workspace access')&&!document.querySelector('.verdict')`,5000);
    const cleared=Math.round(performance.now()-start);assert.ok(cleared<=5000);
    assert.equal((await session(viewer)).status,200);
    await readResult(viewer,1);
    assert.ok(await viewer.eval(`window.j02SameDocument === true && !document.body.innerText.includes('J02 2be4f511 actor 0 observed metrics')`));
    await viewer.shot(root+'/remaining-B-same-tab.png');
    return {removed_view_ms_from_click:cleared, same_document:true, session:200, workspace_b_result_and_header:200};
  });
  if(mode==='prepare') await report('restore_j02_followup_fixture',async()=>{
    const owner=await browser(); await logIn(owner,0);
    const workspace=cfg.links[0].workspace;
    for(const [index,active] of [[1,true],[2,false]]) {
      const actor=cfg.actors[index].user_id;
      const status=await owner.eval(`(async()=>{const session=await (await fetch('/v1/session')).json();const base='/v1/workspaces/'+${JSON.stringify(workspace)};
        const list=await (await fetch(base+'/members')).json();const m=list.items.find(x=>x.user_id===${JSON.stringify(actor)});
        if(!m)throw Error('Fixture member missing');if(m.active===${active})return 200;
        return (await fetch(base+'/members/'+m.user_id,{method:'PATCH',headers:{'X-CSRF-Token':session.csrf_token,'Content-Type':'application/json','If-Match':'"'+m.revision+'"'},body:JSON.stringify({active:${active}})})).status;})()`);
      assert.equal(status,200);
    }
    return {viewer_A_restored:true,invite_target_membership_inactive:true};
  });
} catch(error) {
  const key=Object.keys(reports).at(-1);
  if(key)reports[key]={status:'fail',error:error.message};
  const page=active.at(-1)?.page;
  if(page)reports.failure_screen=await page.eval(`({path:location.pathname,route:location.hash.split('?')[0],title:document.querySelector('h1')?.textContent,buttons:[...document.querySelectorAll('button')].map(x=>x.textContent).slice(0,25)})`).catch(()=>null);
  console.error(error.message);
  process.exitCode=1;
} finally {
  writeFileSync(root+`/browser-${mode}.json`,JSON.stringify({reports,traces},null,2));
  for(const item of active.reverse()) await item.close();
}
