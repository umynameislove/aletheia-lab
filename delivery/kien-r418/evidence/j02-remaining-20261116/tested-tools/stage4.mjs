import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {execFileSync,spawn} from 'node:child_process';
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
 if(cfg.actors[index].cookies){const check=await api(index,'/v1/session');if(check.status===401){delete cfg.actors[index].cookies;delete cfg.actors[index].csrf;report.fixture_session_refreshes??=[];report.fixture_session_refreshes.push({actor:index,at:new Date().toISOString(),reason:'401 in fixture preflight; acquired a fresh real OIDC session'});save();}}
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
 for(const code of ['session_revoked','membership_revoked'])await scenario('late_'+code+'_after_workspace_switch_is_ignored',async()=>{
  const b=await browser(2);let paused;
  try{
   b.page.on('Fetch.requestPaused',p=>{paused=p;});
   await b.page.send('Fetch.enable',{patterns:[{urlPattern:origin+base()+'/results/'+cfg.actors[0].ids.result,requestStage:'Response'}]});
   await b.page.send('Page.navigate',{url:link(0)});
   let until=Date.now()+10000;while(!paused&&Date.now()<until)await sleep(50);assert.ok(paused);
   await b.page.eval(`location.hash=${JSON.stringify(new URL(link(1)).hash)}`);
   await b.page.until(`document.querySelector('.verdict')&&!document.querySelector('.session-cover')&&document.body?.innerText.includes(${JSON.stringify(cfg.actors[1].question)})`,12000);
   const status=code==='session_revoked'?401:403;
   const problem={schema_version:'product-api/v1-draft',type:'urn:problem:test',title:'Denied',status,code,detail:'Controlled late refusal.',request_id:'j02-late'};
   const released=await b.page.send('Fetch.fulfillRequest',{requestId:paused.requestId,responseCode:status,responseHeaders:[{name:'Content-Type',value:'application/problem+json'},{name:'Cache-Control',value:'no-store'}],body:Buffer.from(JSON.stringify(problem)).toString('base64')});
   await b.page.send('Fetch.disable');await sleep(1000);
   assert.equal(await b.page.eval(`document.body.innerText.includes(${JSON.stringify(cfg.actors[1].question)})&&!document.body.innerText.includes('Sign in to Aletheia')`),true);
   return {controlled_late_status:status,code,old_request_cancelled:!!released.error,current_workspace:cfg.actors[1].workspace,classification:'synthetic late refusal at transport boundary; real current workspace/result'};
  }finally{await b.page.send('Fetch.disable').catch(()=>{});await b.close();}
 });
 await scenario('real_followup_running_then_membership_revoke_fences_publication_and_ui',async()=>{
  const own=await browser(0);await own.close();
  const currentMembers=await expect(0,base()+'/members','GET',undefined,200),target=currentMembers.items.find(x=>x.user_id===cfg.actors[2].user_id);
  if(!target.active)await expect(0,base()+'/members/'+target.user_id,'PATCH',{active:true},200,{'If-Match':`"${target.revision}"`});
  const b=await browser(2);let lock;
  try {
   await openResult(b.page);const id=cfg.actors[0].ids;
   const full=await expect(0,base()+'/results/'+id.result,'GET',undefined,200);
   await b.page.eval(`location.hash += '&claim='+${JSON.stringify(full.view.claims[0].id)}`);
   const composer=JSON.stringify('[aria-label="Ask a follow-up"]');
   await b.page.until(`document.querySelector(${composer})&&!document.querySelector(${composer}).disabled`,6000);
   const before=Number(sql(`SELECT count(*) FROM k05_records WHERE workspace_id='${ws()}' AND project_id='${id.project}';`).trim().split(/\r?\n/).at(-1));
   await b.page.set('[aria-label="Ask a follow-up"]','J02 revoke running '+cfg.run);
   await b.page.click('.composer .btn--primary');
   let job;const deadline=Date.now()+4000;
   while(Date.now()<deadline){
    const lines=sql(`SELECT json_build_object('job_id',job_id,'state',state) FROM j01_jobs WHERE workspace_id='${ws()}' AND creator_id='${cfg.actors[2].user_id}' AND operation='follow_up' ORDER BY created_at DESC LIMIT 1;`).trim().split(/\r?\n/);
    if(lines.some(x=>x.startsWith('{')))job=JSON.parse(lines.find(x=>x.startsWith('{')));
    if(job?.state==='running')break;await sleep(100);
   }
   assert.equal(job?.state,'running','real worker must claim the follow-up before revocation');
   const members=await expect(0,base()+'/members','GET',undefined,200),member=members.items.find(x=>x.user_id===cfg.actors[2].user_id);
   const start=performance.now();await expect(0,base()+'/members/'+member.user_id,'PATCH',{active:false},200,{'If-Match':`"${member.revision}"`});

   await b.page.until('!document.querySelector(".verdict")&&document.body?.innerText.includes("Workspace access")&&!document.body?.innerText.includes("Sign in to Aletheia")',4800);
   const cleared=performance.now()-start;assert.ok(cleared<=5000);
   let terminal;const end=Date.now()+15000;
   while(Date.now()<end){const r=await api(0,base()+'/jobs/'+job.job_id);assert.equal(r.status,200);terminal=r.body;if(terminal.status==='failed')break;await sleep(200);}
   assert.equal(terminal.status,'failed');
   const attempts=await expect(0,base()+'/jobs/'+job.job_id+'/attempts','GET',undefined,200);
   assert.equal(attempts.items.at(-1).failure_kind,'authorization');
   const after=Number(sql(`SELECT count(*) FROM k05_records WHERE workspace_id='${ws()}' AND project_id='${id.project}';`).trim().split(/\r?\n/).at(-1));assert.equal(after,before);
   await b.page.shot(root+'/running-followup-revoked.png',true);
   return {job_id:job.job_id,worker_state_before_revoke:'running',terminal_status:terminal.status,failure_kind:attempts.items.at(-1).failure_kind,new_project_records:after-before,private_view_clear_ms:Math.round(cleared),barrier:'none; worker running state observed in real PostgreSQL before revoke',backend_stub:false};
  }catch(e){writeFileSync(root+'/followup-failure-screen.json',JSON.stringify(await b.page.eval('({route:location.hash,text:document.body?.innerText.slice(0,4000)})'),null,2));throw e;}finally{if(lock)lock.stdin.end('ROLLBACK;\n');await b.close();}
 });
 report.status='stage4_pass';save();
}catch(e){report.status='failed';save();console.error(e.message);process.exitCode=1;}
