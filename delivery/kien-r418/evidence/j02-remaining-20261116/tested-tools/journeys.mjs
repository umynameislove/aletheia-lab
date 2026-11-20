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
try{
 for(let i=0;i<7;i++)await scenario(`oidc_login_actor_${i}_needs_workspace`,async()=>{const b=await browser(i);try{const s=await expect(i,'/v1/session','GET',undefined,200);assert.equal(s.onboarding,'needs_workspace');assert.equal(s.can_create_workspace,true);return {onboarding:s.onboarding};}finally{await b.close();}});
 for(let i=0;i<2;i++)await scenario(`normal_user_first_owner_${i}`,async()=>{const b=await browser(i);try{
  await b.page.set('#workspace-create-title + form input',`J02 ${cfg.run} workspace ${i}`);
  await b.page.clickText('Create workspace');
  await b.page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")',12000);
  const r=await expect(i,'/v1/workspaces','GET',undefined,200);assert.equal(r.items.length,1);cfg.actors[i].workspace=r.items[0].workspace_id;save();
  const cap=await expect(i,`/v1/workspaces/${cfg.actors[i].workspace}/capabilities`,'GET',undefined,200);assert.equal(cap.membership.role,'owner');
  return {workspace_id:cfg.actors[i].workspace,role:'owner',via:'actual WorkspaceManager create form'};
 }finally{await b.close();}});
 for(let i=0;i<2;i++)await scenario(`owned_native_result_${i}`,async()=>{const b=await browser(i);try{return await createResult(b,i);}catch(e){writeFileSync(root+`/failed-native-${i}.json`,JSON.stringify(await b.page.eval('({route:location.hash,text:document.body.innerText.slice(0,5000)})'),null,2));throw e;}finally{await b.close();}});
 for(const [i,role] of [[2,'admin'],[3,'analyst'],[4,'reviewer'],[5,'viewer']])await scenario(`invite_accept_${role}`,async()=>{
  const invite=await expect(0,base()+'/invitations','POST',{user_id:cfg.actors[i].user_id,role,expires_in_seconds:600},201);
  const b=await browser(i);try{
   await b.page.set('#workspace-accept-title + form input',invite.token);await b.page.clickText('Accept invitation');
   await b.page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")',10000);
   const cap=await expect(i,base()+'/capabilities','GET',undefined,200);assert.equal(cap.membership.role,role);
   cfg.actors[i].workspace=ws();save();return {role,via:'actual WorkspaceManager accept form'};
  }finally{await b.close();}
 });
 await scenario('invite_wrong_account_revoked_and_replay',async()=>{
  const invitation=await expect(0,base()+'/invitations','POST',{user_id:cfg.actors[6].user_id,role:'viewer',expires_in_seconds:600},201);
  const wrong=await expect(1,'/v1/invitations/accept','POST',{token:invitation.token},403);assert.equal(wrong.code,'invitation_wrong_account');
  await expect(0,base()+'/invitations/'+invitation.invitation_id,'DELETE',{},200);
  const gone=await expect(6,'/v1/invitations/accept','POST',{token:invitation.token},404);
  cfg.expiring=await expect(0,base()+'/invitations','POST',{user_id:cfg.actors[6].user_id,role:'viewer',expires_in_seconds:60},201);save();
  const accepted=await expect(0,base()+'/invitations','POST',{user_id:cfg.actors[6].user_id,role:'viewer',expires_in_seconds:600},201);
  await expect(6,'/v1/invitations/accept','POST',{token:accepted.token},200);
  const replay=await api(6,'/v1/invitations/accept','POST',{token:accepted.token});assert.equal(replay.status,404);
  return {wrong:wrong.code,revoked:gone.code,replay:replay.body.code};
 });
 await scenario('last_owner_cannot_leave',async()=>{
  const members=await expect(0,base()+'/members','GET',undefined,200);const own=members.items.find(x=>x.user_id===cfg.actors[0].user_id);
  const r=await api(0,base()+'/members/'+own.user_id,'PATCH',{active:false},{'If-Match':`"${own.revision}"`});assert.equal(r.status,403);assert.equal(r.body.code,'permission_denied');return {code:r.body.code};
 });
 report.status='stage1_pass';save();
}catch(e){report.status='failed';save();console.error(e.message);process.exitCode=1;}
