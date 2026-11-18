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
 const answer=await new Promise((resolve,reject)=>{const req=request({hostname:'127.0.0.1',port:18876,path,method,servername:'localhost',ca,headers,timeout:12000},r=>{const chunks=[];r.on('data',c=>chunks.push(c));r.on('end',()=>{const text=Buffer.concat(chunks).toString();let value;try{value=JSON.parse(text);}catch{value={};}resolve({status:r.statusCode,body:value,headers:r.headers,raw_sha256:createHash("sha256").update(Buffer.concat(chunks)).digest("hex"),raw_bytes:Buffer.concat(chunks).length});});});req.on('error',reject);req.on('timeout',()=>req.destroy(new Error('API test timeout')));req.end(bytes);});
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
try {
 const ids=cfg.actors[0].ids;
 const resultPath=base()+'/results/'+ids.result;
 await scenario('grant_boundaries_before_and_after_explicit_grants',async()=>{
  for(const i of [3,4,5])await expect(i,resultPath+'/header','GET',undefined,404);
  const lines=sql(`SELECT source_id FROM j01_upload_snapshots WHERE snapshot_id='${ids.snapshot}' AND workspace_id='${ws()}';`).trim().split(/\r?\n/);
  const source=lines.at(-1);assert.match(source,/^src_/);cfg.source=source;save();
  for(const i of [3,4,5,6]){
   const actions=i===3?['read','write','review','export']:['read','review','export'];
   sql(`INSERT INTO j01_project_grants(workspace_id,project_id,user_id,actions,revision) VALUES ('${ws()}','${ids.project}','${cfg.actors[i].user_id}',ARRAY[${actions.map(x=>"'"+x+"'").join(',')}],1) ON CONFLICT DO NOTHING;`);
   await expect(0,base()+'/sources/'+source+'/grants/'+cfg.actors[i].user_id,'PUT',{actions},200,{'If-Match':'"0"'});
  }
  return {no_grant_denials:3,project_grants:'operator-created synthetic grants; normal runtime authorization still enforced',source_grants:'authenticated owner HTTP endpoint'};
 });
 const result=await expect(0,resultPath,'GET',undefined,200);
 const evidence=result.view.evidence[0].id;
 for(const [i,role] of [[0,'owner'],[2,'admin'],[3,'analyst'],[4,'reviewer'],[5,'viewer']])await scenario('role_matrix_'+role,async()=>{
  const caps=await expect(i,base()+'/capabilities','GET',undefined,200);assert.equal(caps.membership.role,role);
  await expect(i,resultPath+'/header','GET',undefined,200);
  await expect(i,resultPath+'/claims?limit=2','GET',undefined,200);
  await expect(i,resultPath+'/evidence/'+evidence,'GET',undefined,200);
  await expect(i,resultPath+'/graph?limit=2','GET',undefined,200);
  const exp=await expect(i,resultPath+'/exports','POST',{format:'json'},201,{'Idempotency-Key':`j02-${cfg.run}-export-${i}`});
  const bytes=await api(i,exp.download_url);assert.equal(bytes.status,200);assert.equal(bytes.raw_sha256,exp.sha256);assert.equal(bytes.raw_bytes,exp.byte_size);
  cfg.actors[i].export=exp;save();
  const hash=createHash('sha256').update(readFileSync('fixtures/metric-dimensions-hosted-v2/source.zip')).digest('hex');
  const manifest={schema_version:'product-api/v1-draft',source_type:'zip_bundle',byte_size:662,sha256:hash,parts:[{part_number:1,byte_size:662,sha256:hash}],parent_source_id:null};
  const writable=['owner','admin','analyst'].includes(role);
  const upload=await api(i,base()+'/uploads','POST',manifest,{'Idempotency-Key':`j02-${cfg.run}-upload-${i}`});assert.equal(upload.status,writable?201:404);
  assert.equal(caps.allowed_actions.includes('import'),writable);
  return {read_header_claims_evidence_graph:4,export_bytes:bytes.raw_bytes,export_hash_matches:true,upload_status:upload.status};
 });
 await scenario('cross_tenant_and_export_creator_scope',async()=>{
  for(const path of [resultPath,resultPath+'/header',resultPath+'/claims?limit=2',resultPath+'/evidence/'+evidence,resultPath+'/graph?limit=2',base()+'/jobs/'+ids.job,base()+'/sources/'+cfg.source])await expect(1,path,'GET',undefined,404);
  await expect(1,cfg.actors[0].export.download_url,'GET',undefined,404);
  await expect(5,cfg.actors[0].export.download_url,'GET',undefined,404);
  const b=await browser(1);try{
   await b.page.send('Page.navigate',{url:origin+'/#/investigation?'+new URLSearchParams(ids)});
   await b.page.until('document.body.innerText.includes("Workspace access")',10000);
   assert.equal(await b.page.eval(`document.body.innerText.includes(${JSON.stringify(cfg.actors[0].question)})`),false);
   await b.page.shot(root+'/foreign-link-denied.png',true);
  }finally{await b.close();}
  return {http_denials:9,browser_private_title_visible:false};
 });
 await scenario('invitation_natural_expiry_60s',async()=>{
  const until=Date.parse(cfg.expiring.expires_at)+1500;
  while(Date.now()<until)await new Promise(r=>setTimeout(r,Math.min(1000,until-Date.now())));
  const r=await expect(6,'/v1/invitations/accept','POST',{token:cfg.expiring.token},410);assert.equal(r.code,'invitation_expired');
  return {code:r.code,configured_seconds:60,clock_mutation:false};
 });
 await scenario('membership_revoke_no_workspace_preserves_session_and_clears_view',async()=>{
  const before=await expect(0,base()+'/members','GET',undefined,200),target=before.items.find(x=>x.user_id===cfg.actors[5].user_id);
  if(!target.active)await expect(0,base()+'/members/'+target.user_id,'PATCH',{active:true},200,{'If-Match':`"${target.revision}"`});
  const b=await browser(5);try{
   await b.page.send('Page.navigate',{url:origin+'/#/investigation?'+new URLSearchParams(ids)});
   await b.page.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',12000);
   const members=await expect(0,base()+'/members','GET',undefined,200),member=members.items.find(x=>x.user_id===cfg.actors[5].user_id);
   const t=performance.now();await expect(0,base()+'/members/'+member.user_id,'PATCH',{active:false},200,{'If-Match':`"${member.revision}"`});
   await b.page.until(`document.body.innerText.includes('Workspace access')&&!document.body.innerText.includes(${JSON.stringify(cfg.actors[0].question)})&&!document.body.innerText.includes('Sign in to Aletheia')`,4800);
   const elapsed=performance.now()-t;assert.ok(elapsed<=5000);
   const s=await expect(5,'/v1/session','GET',undefined,200);assert.equal(s.onboarding,'needs_workspace');
   await expect(5,resultPath+'/header','GET',undefined,404);
   await expect(5,cfg.actors[5].export.download_url,'GET',undefined,404);
   await b.page.shot(root+'/revoked-needs-workspace.png',true);
   return {revoke_to_private_view_cleared_ms:Math.round(elapsed),session_status:200,onboarding:s.onboarding};
  }finally{await b.close();}
 });
 await scenario('membership_revoke_remaining_workspace_can_continue',async()=>{
  const invitation=await expect(1,'/v1/workspaces/'+cfg.actors[1].workspace+'/invitations','POST',{user_id:cfg.actors[6].user_id,role:'viewer',expires_in_seconds:600},201);
  await expect(6,'/v1/invitations/accept','POST',{token:invitation.token},200);
  const b=await browser(6);try{
   await b.page.send('Page.navigate',{url:origin+'/#/investigation?'+new URLSearchParams(ids)});
   await b.page.until('document.querySelector(".verdict")&&!document.querySelector(".session-cover")',12000);
   const members=await expect(0,base()+'/members','GET',undefined,200),member=members.items.find(x=>x.user_id===cfg.actors[6].user_id);
   const start=performance.now();await expect(0,base()+'/members/'+member.user_id,'PATCH',{active:false},200,{'If-Match':`"${member.revision}"`});
   await b.page.until(`document.body.innerText.includes('Workspace access')&&!document.body.innerText.includes(${JSON.stringify(cfg.actors[0].question)})&&!document.body.innerText.includes('Sign in to Aletheia')`,4800);
   const elapsed=performance.now()-start;assert.ok(elapsed<=5000);
   const s=await expect(6,'/v1/session','GET',undefined,200);assert.equal(s.onboarding,'ready');
   await b.page.clickText('Open workspace');await b.page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")',8000);
   const cap=await expect(6,'/v1/workspaces/'+cfg.actors[1].workspace+'/capabilities','GET',undefined,200);
   await b.page.shot(root+'/revoked-other-workspace.png',true);
   return {revoke_to_view_clear_ms:Math.round(elapsed),session_status:200,remaining_workspace:cap.workspace_id};
  }finally{await b.close();}
 });
 report.status='stage2_pass';save();
}catch(e){report.status='failed';save();console.error(e.message);process.exitCode=1;}
