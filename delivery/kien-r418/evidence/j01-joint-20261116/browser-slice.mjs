import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { launch } from "./cdp.mjs";
const root = "C:/t/j01-closeout-20261116";
const source = "C:/Users/Trung Kiên/.codex/worktrees/product-api-publication/_repo_aletheia_ref/fixtures/metric-dimensions-hosted-v2/source.zip";
const doc = JSON.parse(execFileSync("docker", ["run","--rm","--network","none","--user","0",
 "--mount","type=volume,source=aletheia-k14-stg-da2093353a73-clients,target=/run/load,readonly",
 "--entrypoint","python","aletheia-k14-staging:fa46dd8-da2093353a73","-c",
 'import json; from pathlib import Path; p=Path("/run/load"); print(json.dumps({"password":(p/"password").read_text()}))'],
 {encoding:"utf8",stdio:["ignore","pipe","ignore"]}));
const browser = await launch({port:9372});
const {page} = browser;
const report={status:"running",base_sha:"fa46dd8cbb2239c6f116456894a2009b45846245",source_sha256:createHash("sha256").update(readFileSync(source)).digest("hex"),steps:[]};
let stage="login";
async function timeStep(name, work) {
 stage=name; const start=performance.now(); await work();
 const ms=Math.round(performance.now()-start); report.steps.push({name,ms,status:"pass"}); console.log(name,"PASS",ms,"ms");
}
try {
 await timeStep("real OIDC login",async()=>{
  await page.open("https://localhost:18776/#/workspaces");
  await page.until('document.body?.innerText.includes("Sign in to Aletheia")',12000);
  await page.click('a[href="/v1/auth/start"]');
  await page.until('document.querySelector("#kc-form-login")',12000);
  await page.set("#username","u0");await page.set("#password",doc.password);
  await page.click("#kc-login");
  await page.until('location.origin === "https://localhost:18776" && document.body?.innerText.includes("Workspace access")',16000);
  await page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Open workspace"||b.textContent==="Connect project")',12000);
  const needs=await page.eval('[...document.querySelectorAll("button")].some(b=>b.textContent==="Open workspace")');
  if(needs) await page.clickText("Open workspace");
  await page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Connect project")',12000);
 });
 await timeStep("upload ZIP and preview",async()=>{
  await page.clickText("Connect project");
  await page.until('document.querySelector("#hosted-project-zip")',8000);
  const node=await page.send("DOM.getDocument");
  const input=await page.send("DOM.querySelector",{nodeId:node.result.root.nodeId,selector:"#hosted-project-zip"});
  await page.send("DOM.setFileInputFiles",{nodeId:input.result.nodeId,files:[source]});
  await page.until('[...document.querySelectorAll("button")].some(b=>b.textContent==="Upload and preview"&&!b.disabled)');
  await page.clickText("Upload and preview");
  await page.until('document.querySelector(\'[aria-label="Direction for độ_chính_xác"]\')',20000);
 });
 await timeStep("mapping v2 and durable confirm",async()=>{
  const pick=(code,value)=>page.set("#f-"+code.replace(/[^a-z0-9]+/gi,"-"),value);
  await pick("target.identifier_field","id");
  await page.set('[aria-label="Direction for độ_chính_xác"]',"higher_is_better");
  await page.set('[aria-label="Regression threshold for độ_chính_xác"]',"0.08");
  await pick("baseline_run_id","lần-0");
  await pick("metric_sources[0].class_field","class");
  await pick("metric_sources[0].unit_field","unit");
  await page.set("#def-0-unit","%");
  await page.shot(root+"/mapping-v2.png",true);
  await page.clickText("Confirm mapping and save snapshot");
  await page.until('document.querySelector("#hosted-first-question")',25000);
 });
 await timeStep("analyze to persisted result",async()=>{
  await page.eval('(()=>{const e=document.querySelector("#hosted-first-question");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,"What is observed in these validated metrics?");e.dispatchEvent(new Event("input",{bubbles:true}));})()');
  await page.clickText("Analyze saved snapshot");
  await page.until('document.querySelector(".verdict") && !document.querySelector(".session-cover")',30000);
  await page.shot(root+"/result-first.png",true);
 });
 const ids=await page.eval('Object.fromEntries(new URLSearchParams(location.hash.split("?")[1]))');
 report.ids=ids;
 assert.ok(["workspace","job","project","snapshot","result"].every(k=>typeof ids[k]==="string"&&ids[k].length>0));
 async function read() {
  return await page.eval('(async()=>{const q=new URLSearchParams(location.hash.split("?")[1]);const base="/v1/workspaces/"+q.get("workspace");const r=await fetch(base+"/results/"+q.get("result"));const body=await r.json();const j=await fetch(base+"/jobs/"+q.get("job"));const job=await j.json();return {status:r.status,body,jobStatus:j.status,job};})()');
 }
 const first=await read();
 assert.equal(first.status,200);assert.equal(first.jobStatus,200);assert.equal(first.job.status,"succeeded");
 assert.equal(first.job.workspace_id,ids.workspace);assert.equal(first.job.job_id,ids.job);
 const v=first.body.view;
 assert.equal(v.project.id,ids.project);assert.equal(v.snapshot.id,ids.snapshot);assert.equal(v.result.id,ids.result);
 report.view_sha256=createHash("sha256").update(JSON.stringify(v)).digest("hex");
 report.claims=v.claims.length;report.evidence=v.evidence.length;report.nodes=v.graph.nodes.length;
 report.result_read_status=first.status;report.job_status=first.job.status;
 writeFileSync(root+"/observed-result.json",JSON.stringify(first.body,null,2));
 await timeStep("reload reads same persisted result",async()=>{
  await page.send("Page.reload");
  await page.until('document.querySelector(".verdict") && !document.querySelector(".session-cover")',18000);
  const next=await read();
  assert.equal(next.status,200);
  assert.equal(createHash("sha256").update(JSON.stringify(next.body.view)).digest("hex"),report.view_sha256);
  assert.deepEqual(await page.eval('Object.fromEntries(new URLSearchParams(location.hash.split("?")[1]))'),ids);
 });
 const query=new URLSearchParams(ids);query.set("layout","reading");query.set("claim",v.claims[0].id);
 await page.send("Page.navigate",{url:"https://localhost:18776/#/investigation?"+query});
 await page.until('document.querySelector(".verdict") && !document.querySelector(".session-cover")',8000);
 await page.shot(root+"/layout-reading.png",true);
 report.status="passed_real_staging_slice";
} catch(error) {
 report.status="failed";report.failed_stage=stage;
 report.error_type=error.name;report.message=error.message;
 const state=await page.eval('({route:location.hash.split("?")[0],headings:[...document.querySelectorAll("h1")].map(x=>x.textContent),alerts:[...document.querySelectorAll("[role=alert]")].map(x=>x.innerText.slice(0,180)),inputs:[...document.querySelectorAll("select")].map(x=>({id:x.id,options:[...x.options].map(o=>[o.value,o.text])}))})').catch(()=>null);
 console.log("FAILED",stage,JSON.stringify(state));process.exitCode=1;
} finally {
 delete doc.password;
 writeFileSync(root+"/browser-slice-result.json",JSON.stringify(report,null,2));
 await browser.close();
}



