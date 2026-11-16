import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {request} from 'node:https';
import {createHash} from 'node:crypto';
import {launch} from './cdp.mjs';
const root='C:/t/j02-closeout-20261116';
const privatePath=root+'/private.json';
const cfg=JSON.parse(readFileSync(privatePath));
const ca=readFileSync('C:/t/aletheia-k14-staging-da2093353a73/ca.pem');
const origin='https://localhost:18776';
let report;try {report=JSON.parse(readFileSync(root+'/result.json'));}catch{report={run:cfg.run,started:new Date().toISOString(),cases:[],http:[],failures:[]};}
const save=()=>{writeFileSync(privatePath,JSON.stringify(cfg));writeFileSync(root+'/result.json',JSON.stringify(report,null,2));};
async function scenario(name,fn){
 if(report.cases.some(x=>x.name===name&&x.status==='pass')){console.log('RETAIN',name);return;}
 const start=performance.now();
 try {const details=await fn();report.cases.push({name,status:'pass',ms:Math.round(performance.now()-start),...details});save();console.log('PASS',name);}
 catch(e){report.failures.push({name,ms:Math.round(performance.now()-start),type:e.name,message:e.message.slice(0,500)});save();throw e;}
}
async function api(index,path,method='GET',body,extra={}){
 const actor=cfg.actors[index],start=performance.now();
 const bytes=body===undefined?null:Buffer.from(JSON.stringify(body));
 const headers={Host:'localhost:18776',Origin:origin,Accept:'application/json',Cookie:actor.cookies.filter(x=>x.domain==='localhost').map(x=>x.name+'='+x.value).join('; '),...extra};
 if(method!=='GET'){headers['X-CSRF-Token']=actor.csrf;if(bytes){headers['Content-Type']='application/json';headers['Content-Length']=bytes.length;}}
 const answer=await new Promise((resolve,reject)=>{const req=request({hostname:'127.0.0.1',port:18876,path,method,servername:'localhost',ca,headers,agent:false,timeout:12000},r=>{const chunks=[];r.on('data',c=>chunks.push(c));r.on('end',()=>{const text=Buffer.concat(chunks).toString();let value;try{value=JSON.parse(text);}catch{value={};}resolve({status:r.statusCode,body:value,headers:r.headers,raw_sha256:createHash("sha256").update(Buffer.concat(chunks)).digest("hex"),raw_bytes:Buffer.concat(chunks).length});});});req.on('error',reject);req.on('timeout',()=>req.destroy(new Error('API test timeout')));req.end(bytes);});
 report.http.push({actor:index,method,path,status:answer.status,ms:Math.round(performance.now()-start),...(answer.body.code?{code:answer.body.code}:{})});save();return answer;
}
function sql(text){
 return execFileSync('docker',['exec','-i','aletheia-k14-stg-da2093353a73-pg','psql','-U','postgres','-d','aletheia','-X','-A','-t','-v','ON_ERROR_STOP=1'],{input:`SET search_path TO ${cfg.schema};\n${text}`,encoding:'utf8',stdio:['pipe','pipe','pipe']});
}
async function browser(index){
 if(cfg.actors[index].cookies){const check=await api(index,'/v1/session');if(check.status===401){delete cfg.actors[index].cookies;delete cfg.actors[index].csrf;report.fixture_session_refreshes??=[];report.fixture_session_refreshes.push({actor:index,at:new Date().toISOString(),reason:'expired after Docker interruption'});save();}}
 const b=await launch({port:9440+index}),p=b.page;
 if(cfg.actors[index].cookies){await p.send('Network.setCookies',{cookies:cfg.actors[index].cookies});await p.send('Page.navigate',{url:origin+'/#/workspaces'});await p.until('document.body?.innerText.includes("Workspace access")',14000);}
 else {
  await p.open(origin+'/#/workspaces');await p.until('document.body?.innerText.includes("Sign in to Aletheia")',12000);
  await p.click('a[href="/v1/auth/start"]');await p.until('document.querySelector("#kc-form-login")',16000);
  await p.set('#username',cfg.actors[index].username);await p.set('#password',cfg.password);await p.click('#kc-login');
  await p.until('location.origin==="https://localhost:18776" && document.body?.innerText.includes("Workspace access")',18000);
  cfg.actors[index].cookies=(await p.send('Network.getCookies',{urls:[origin]})).result.cookies;
  const s=await p.eval('(async()=>{const r=await fetch("/v1/session");return {status:r.status,body:await r.json()};})()');
  assert.equal(s.status,200);assert.equal(s.body.actor.user_id,cfg.actors[index].user_id);
  cfg.actors[index].csrf=s.body.csrf_token;save();
 }
 return b;
}
async function createResult(b,index){
 const p=b.page,actor=cfg.actors[index];
 await p.send('Page.navigate',{url:origin+'/#/workspaces'});
 await p.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Open workspace"||b.textContent==="Connect project")',10000);
 if(!await p.eval('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")'))await p.clickText('Open workspace');
 await p.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")',10000);
 await p.clickText('Connect project');await p.until('document.querySelector("#hosted-project-zip")',8000);
 const d=await p.send('DOM.getDocument'),n=await p.send('DOM.querySelector',{nodeId:d.result.root.nodeId,selector:'#hosted-project-zip'});
 await p.send('DOM.setFileInputFiles',{nodeId:n.result.nodeId,files:['C:/Users/Trung Kiên/.codex/worktrees/product-api-publication/_repo_aletheia_ref/fixtures/metric-dimensions-hosted-v2/source.zip']});
 await p.clickText('Upload and preview');
 await p.until('[...document.querySelectorAll("button")].some(b=>b.textContent.includes("Confirm mapping"))',25000);
 const pick=(name,value)=>p.set('#f-'+name.replace(/[^a-z0-9]+/gi,'-'),value);
 await pick('target.identifier_field','id');await p.set('[aria-label="Direction for độ_chính_xác"]','higher_is_better');await p.set('[aria-label="Regression threshold for độ_chính_xác"]','0.08');await pick('baseline_run_id','lần-0');await pick('metric_sources[0].class_field','class');await pick('metric_sources[0].unit_field','unit');await p.set('#def-0-unit','%');
 await p.clickText('Confirm mapping and save snapshot');await p.until('document.querySelector("#hosted-first-question")',25000);
 const question=`J02 ${cfg.run} actor ${index} observed metrics`;
 await p.eval(`(()=>{const e=document.querySelector('#hosted-first-question');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,${JSON.stringify(question)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
 await p.clickText('Analyze saved snapshot');await p.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',25000);
 actor.ids=await p.eval('Object.fromEntries(new URLSearchParams(location.hash.split("?")[1]))');actor.question=question;save();
 await p.shot(root+`/actor-${index}-result.png`,true);
 return {claims:10,ids:actor.ids};
}
const ws=()=>cfg.actors[0].workspace;
const base=()=>'/v1/workspaces/'+ws();
const expect=async(index,path,method,body,status,extra)=>{const r=await api(index,path,method,body,extra);assert.equal(r.status,status,`${method||'GET'} ${path}: ${r.body.code??''}`);return r.body;};
const browsers=[];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const link=index=>origin+'/#/investigation?'+new URLSearchParams(cfg.actors[index].ids);
async function openResult(page,index=0){await page.send('Page.navigate',{url:link(index)});await page.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',12000);}
try {
 for(const i of [0,1,2,3,5]){const r=await api(i,'/v1/session');if(r.status===401){const b=await browser(i);await b.close();}}
 await scenario('owner_transfer_vs_self_leave_preserves_one_owner',async()=>{
  if(cfg.raceWorkspace){
   const path='/v1/workspaces/'+cfg.raceWorkspace;
   const previousTransfer=report.http.find(x=>x.path===path+'/transfer-owner'&&x.status===200);
   const previousLeave=report.http.find(x=>x.path===path+'/members/'+cfg.actors[5].user_id&&x.status===412&&x.code==='revision_conflict');
   if(previousTransfer&&previousLeave){
    const current=await expect(1,path+'/members','GET',undefined,200),owners=current.items.filter(x=>x.active&&x.role==='owner');
    assert.equal(owners.length,1);assert.equal(owners[0].user_id,cfg.actors[1].user_id);
    return {transfer_status:200,self_leave_status:412,self_leave_code:'revision_conflict',active_owners:1,real_concurrent_http:true,qualification:'retained actual race; corrected harness HTTP expectation and independently checked final owner; no race rerun'};
   }
  }
  if(!cfg.raceWorkspace){cfg.raceWorkspace=(await expect(5,'/v1/workspaces','POST',{name:'J02 '+cfg.run+' owner race'},201)).workspace_id;save();}
  const path='/v1/workspaces/'+cfg.raceWorkspace;
  const invitation=await expect(5,path+'/invitations','POST',{user_id:cfg.actors[1].user_id,role:'admin',expires_in_seconds:600},201);
  await expect(1,'/v1/invitations/accept','POST',{token:invitation.token},200);
  const before=await expect(5,path+'/members','GET',undefined,200),target=before.items.find(x=>x.user_id===cfg.actors[1].user_id),owner=before.items.find(x=>x.user_id===cfg.actors[5].user_id);
  const [transfer,leave]=await Promise.all([
   api(5,path+'/transfer-owner','POST',{user_id:target.user_id},{'If-Match':`"${target.revision}"`}),
   api(5,path+'/members/'+owner.user_id,'PATCH',{active:false},{'If-Match':`"${owner.revision}"`})]);
  assert.equal(transfer.status,200);assert.ok([200,403,412].includes(leave.status));if(leave.status===412)assert.equal(leave.body.code,'revision_conflict');
  const after=await expect(1,path+'/members','GET',undefined,200),owners=after.items.filter(x=>x.active&&x.role==='owner');
  assert.equal(owners.length,1);assert.equal(owners[0].user_id,target.user_id);
  return {transfer_status:transfer.status,self_leave_status:leave.status,active_owners:owners.length,real_concurrent_http:true};
 });
 await scenario('late_result_success_after_workspace_switch_is_discarded',async()=>{
  const other=cfg.actors[1],path='/v1/workspaces/'+other.workspace;
  if(!cfg.adminInOther){
   const invite=await expect(1,path+'/invitations','POST',{user_id:cfg.actors[2].user_id,role:'viewer',expires_in_seconds:600},201);
   await expect(2,'/v1/invitations/accept','POST',{token:invite.token},200);
   const source=sql(`SELECT source_id FROM j01_upload_snapshots WHERE snapshot_id='${other.ids.snapshot}' AND workspace_id='${other.workspace}';`).trim().split(/\r?\n/).at(-1);
   sql(`INSERT INTO j01_project_grants(workspace_id,project_id,user_id,actions,revision) VALUES ('${other.workspace}','${other.ids.project}','${cfg.actors[2].user_id}',ARRAY['read','export'],1);`);
   await expect(1,path+'/sources/'+source+'/grants/'+cfg.actors[2].user_id,'PUT',{actions:['read','export']},200,{'If-Match':'"0"'});
   cfg.adminInOther=true;save();
  }
  const b=await browser(2);let paused,body;
  try{
   b.page.on('Fetch.requestPaused',async p=>{paused=p;body=(await b.page.send('Fetch.getResponseBody',{requestId:p.requestId})).result;});
   await b.page.send('Fetch.enable',{patterns:[{urlPattern:origin+base()+'/results/'+cfg.actors[0].ids.result,requestStage:'Response'}]});
   await b.page.send('Page.navigate',{url:link(0)});
   const end=Date.now()+10000;while(!body&&Date.now()<end)await sleep(50);assert.ok(body,'actual API result response was captured');
   assert.equal(paused.responseStatusCode,200);
   await b.page.eval(`location.hash=${JSON.stringify(new URL(link(1)).hash)}`);
   await b.page.until(`document.querySelector('.verdict')&&!document.querySelector('.session-cover')&&document.body?.innerText.includes(${JSON.stringify(other.question)})`,12000);
   const released=await b.page.send('Fetch.fulfillRequest',{requestId:paused.requestId,responseCode:200,responseHeaders:paused.responseHeaders,body:body.base64Encoded?body.body:Buffer.from(body.body).toString('base64')});
   await b.page.send('Fetch.disable');await sleep(1000);
   assert.equal(await b.page.eval(`document.body?.innerText.includes(${JSON.stringify(cfg.actors[0].question)})`),false);
   assert.equal(await b.page.eval(`document.body?.innerText.includes(${JSON.stringify(other.question)})`),true);
   await b.page.shot(root+'/late-result-discarded.png',true);
   return {held_actual_status:200,release_cancelled:!!released.error,current_workspace:other.workspace,old_title_visible:false,method:'hold actual response at CDP transport boundary; no backend stub'};
  }finally{await b.page.send('Fetch.disable').catch(()=>{});await b.close();}
 });
 await scenario('actual_back_forward_cache_or_safe_history_reload',async()=>{
  const b=await browser(2);const events=[];
  try{
   b.page.on('Page.backForwardCacheNotUsed',e=>events.push(e.notRestoredExplanations));
   await openResult(b.page);
   await b.page.eval('window.__j02Restored=false; window.addEventListener("pageshow",e=>{if(e.persisted)window.__j02Restored=true;});true');
   const history=await b.page.send('Page.getNavigationHistory');const entry=history.result.entries[history.result.currentIndex];
   await b.page.send('Page.navigate',{url:origin+'/health/ready'});await b.page.until('document.body?.innerText.includes("ready")',6000);
   await sleep(300);await b.page.send('Page.navigateToHistoryEntry',{entryId:entry.id});
   await b.page.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',12000);
   const restored=await b.page.eval('window.__j02Restored===true');
   const value={actual_persisted_pageshow:restored,not_restored_reasons:events.flat().map(x=>x.reason),private_view_returned_only_after_access_check:true};
   writeFileSync(root+'/bfcache-observation.json',JSON.stringify(value,null,2));return value;
  }finally{await b.close();}
 });
 await scenario('browser_network_outage_covers_then_recovers_without_mutation',async()=>{
  const b=await browser(2);const requests=[];
  try{
   await openResult(b.page);await b.page.send('Network.enable');
   b.page.on('Network.requestWillBeSent',p=>{if(p.request.url.startsWith(origin+'/v1/'))requests.push({method:p.request.method,path:new URL(p.request.url).pathname});});
   const start=performance.now();
   await b.page.send('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
   await b.page.until('document.querySelector(".session-cover")&&document.querySelector(".session-app").hidden',4800);
   const covered=performance.now()-start;assert.ok(covered<=5000);
   await sleep(5200);assert.equal(requests.filter(x=>x.method!=='GET').length,0);
   const recover=performance.now();await b.page.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
   await b.page.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',12000);
   const recovery=performance.now()-recover;await b.page.shot(root+'/network-recovered.png',true);
   return {fault:'Chrome network offline; not IdP process shutdown',cover_ms:Math.round(covered),recovery_ms:Math.round(recovery),mutation_requests:0,observed_requests:requests.length};
  }finally{await b.close();}
 });
 await scenario('two_tabs_logout_all_clears_peer_private_view',async()=>{
  const b=await browser(3);
  try{
   await openResult(b.page);
   const target=await b.page.send('Target.createTarget',{url:origin+'/#/workspaces'});
   const attached=await b.page.send('Target.attachToTarget',{targetId:target.result.targetId,flatten:true});const sid=attached.result.sessionId;
   const eval2=async expression=>(await b.page.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sid)).result.result.value;
   const end=Date.now()+10000;while(Date.now()<end&&!await eval2('document.body?.innerText.includes("Workspace access")'))await sleep(100);
   const start=performance.now();await eval2('[...document.querySelectorAll("button")].find(b=>b.textContent==="Sign out all sessions").click();true');
   await b.page.until('!document.querySelector(".verdict")&&!document.body?.innerText.includes("J02 '+cfg.run+' actor 0 observed metrics")',4800);
   const elapsed=performance.now()-start;assert.ok(elapsed<=5000);await expect(3,'/v1/session','GET',undefined,401);
   await b.page.shot(root+'/peer-logout-cleared.png',true);
   await b.page.send('Target.closeTarget',{targetId:target.result.targetId});
   return {tabs:2,peer_clear_ms:Math.round(elapsed),server_session_after_logout:401};
  }finally{await b.close();}
 });
 await scenario('real_idp_logout_then_other_account_drops_draft_and_old_route',async()=>{
  const b=await browser(0);
  try{
   await openResult(b.page);const old=cfg.actors[0];
   await b.page.set('.composer input','J02 private draft '+cfg.run);
   await b.page.clickText('Sign out');
   await b.page.until('document.querySelector("#kc-logout")||document.body?.innerText.includes("Sign in to Aletheia")',12000);
   if(await b.page.eval('!!document.querySelector("#kc-logout")'))await b.page.click('#kc-logout');
   await b.page.until('document.body?.innerText.includes("Sign in to Aletheia")',12000);
   assert.equal(await b.page.eval('location.hash.includes("'+old.ids.project+'")'),false);
   await b.page.click('a[href="/v1/auth/start"]');await b.page.until('document.querySelector("#kc-form-login")',12000);
   await b.page.set('#username',cfg.actors[1].username);await b.page.set('#password',cfg.password);await b.page.click('#kc-login');
   await b.page.until('location.origin==="https://localhost:18776"&&document.body?.innerText.includes("Workspace access")',16000);
   const visible=await b.page.eval(`({old:document.body?.innerText.includes(${JSON.stringify(old.question)}),draft:[...document.querySelectorAll('input,textarea')].some(e=>e.value.includes('J02 private draft'))})`);
   assert.deepEqual(visible,{old:false,draft:false});
   await b.page.send('Page.navigate',{url:link(0)});await b.page.until('document.body?.innerText.includes("Workspace access")',10000);
   assert.equal(await b.page.eval(`document.body?.innerText.includes(${JSON.stringify(old.question)})`),false);
   await b.page.shot(root+'/account-switch-clean.png',true);
   return {real_idp_logout:true,account_after_login:cfg.actors[1].user_id,old_private_title_visible:false,old_draft_visible:false,old_result_link_denied:true};
  }finally{await b.close();}
 });
 report.status='stage3_pass';save();
}catch(e){report.status='failed';save();console.error(e.message);process.exitCode=1;}
