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

const b=await browser(0);await b.close();
const members=await expect(0,base()+'/members','GET',undefined,200);
for(const i of [2,4,5,6]){const member=members.items.find(x=>x.user_id===cfg.actors[i].user_id);if(!member.active)await expect(0,base()+'/members/'+member.user_id,'PATCH',{active:true},200,{'If-Match':`"${member.revision}"`});}
if(cfg.raceWorkspace){const path='/v1/workspaces/'+cfg.raceWorkspace;const current=await expect(1,path+'/members','GET',undefined,200);const extra=current.items.find(x=>x.user_id===cfg.actors[5].user_id);if(extra?.active)await expect(1,path+'/members/'+extra.user_id,'PATCH',{active:false},200,{'If-Match':`"${extra.revision}"`});}
const final=await expect(0,base()+'/members','GET',undefined,200);
const prepared={prepared_at_utc:new Date().toISOString(),workspace_A:ws(),workspace_B:cfg.actors[1].workspace,actors:cfg.actors.map((a,i)=>({index:i,username:a.username,user_id:a.user_id,workspace_A:final.items.find(x=>x.user_id===a.user_id)?.role??null,active_A:final.items.find(x=>x.user_id===a.user_id)?.active??false})),host_ip:'192.168.2.10',peer_ip:'192.168.2.7'};
writeFileSync(root+'/bu-ready-fixture.json',JSON.stringify(prepared,null,2));console.log('J02 fixture ready for Bu; no tests rerun.');
