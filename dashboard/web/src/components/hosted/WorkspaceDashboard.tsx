import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { type HostedCapabilities } from "../../service/hosted";
import { HostedRequestDiscarded, type createHostedClient, type HostedResultHeader } from "../../service/hosted-client";
import { refusalOf, RefusalNote, type Refusal } from "../Ask";
import { Id } from "../primitives";

type Catalog = { projects: string[]; results: HostedResultHeader[]; projectsNext: string | null; resultsNext: string | null };

export function WorkspaceDashboard({ client, caps, resultsOnly }: {
  client: ReturnType<typeof createHostedClient>; caps: HostedCapabilities; resultsOnly: boolean;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<Refusal>(null);
  const [retry, setRetry] = useState(0);
  const [projectsCursor, setProjectsCursor] = useState<string | undefined>();
  const [resultsCursor, setResultsCursor] = useState<string | undefined>();
  const readable = caps.features?.["catalog.read"] === true && caps.features?.["catalog.paged_read"] === true;
  const canConnect = caps.allowed_actions.includes("import");
  const workspace = caps.workspace_id;

  useEffect(() => {
    if (!readable) return;
    const stop = new AbortController();
    setCatalog(null);
    setError(null);
    async function load() {
      try {
        const [projects, results] = await Promise.all([
          resultsOnly ? Promise.resolve({ items: [], next: null }) : client.listProjects(workspace, caps, projectsCursor, stop.signal),
          client.listResults(workspace, caps, resultsCursor, stop.signal),
        ]);
        const headers = await Promise.all(results.items.map(scope =>
          client.loadResultHeader(workspace, caps, scope, stop.signal)));
        if (!stop.signal.aborted) setCatalog({ projects: projects.items, results: headers, projectsNext: projects.next, resultsNext: results.next });
      } catch (failure) {
        if (!stop.signal.aborted && !(failure instanceof HostedRequestDiscarded)) setError(refusalOf(failure));
      }
    }
    void load();
    return () => stop.abort();
  }, [client, caps, readable, workspace, resultsOnly, projectsCursor, resultsCursor, retry]);

  return <div className="page" id="main" tabIndex={-1}>
    <div className="page-inner stack-lg hosted-catalog">
      <header className="page-head">
        <h1>{resultsOnly ? "Results & reproduction" : caps.workspace_name}</h1>
        {canConnect && <div className="actions"><a className="btn btn--primary" href="#/connect">Connect project <ArrowRight size={14} aria-hidden="true" /></a></div>}
      </header>
      {!readable ? <p className="notice">This workspace does not provide a result catalog.</p>
        : error ? <div><RefusalNote refusal={error} /><button className="btn" onClick={() => setRetry(value => value + 1)}>Try again</button></div>
        : !catalog ? <p className="dim" role="status">Loading projects and results…</p>
        : <>
          {!resultsOnly && <section className="panel stack" aria-labelledby="hosted-projects">
            <h2 className="panel-title" id="hosted-projects">Projects</h2>
            {catalog.projects.length ? <ul className="checklist">{catalog.projects.map(project => {
              const name = catalog.results.find(result => result.project_id === project)?.display_name;
              return <li key={project}><b>{name ?? "Project"}</b><Id v={project} /></li>;
            })}</ul> : <p className="body-copy">{canConnect ? "Connect a ZIP to start your first project." : "No projects are available to your account."}</p>}
            <div className="actions">
              {projectsCursor && <button className="btn btn--sm" onClick={() => setProjectsCursor(undefined)}>First page</button>}
              {catalog.projectsNext && <button className="btn btn--sm" onClick={() => setProjectsCursor(catalog.projectsNext!)}>Next projects</button>}
            </div>
          </section>}
          <section className="panel stack" aria-labelledby="hosted-results">
            <h2 className="panel-title" id="hosted-results">Saved results</h2>
            {catalog.results.length ? <ul className="checklist hosted-result-list">{catalog.results.map(row => {
              const route = new URLSearchParams({ workspace, project: row.project_id, snapshot: row.snapshot_id, result: row.result_id });
              if (row.runtime.model === "upload-snapshot-windowed-metric-observation/v3") route.set("paged", "1");
              return <li key={row.result_id}>
                <div><b>{row.display_name}</b><span className="sub-line">{row.counts.claims.toLocaleString()} claims</span><Id v={row.result_id} /></div>
                <a className="btn btn--sm" href={"#/investigation?" + route}>Open result</a>
              </li>;
            })}</ul> : <p className="body-copy">Finished analyses appear here.</p>}
            <div className="actions">
              {resultsCursor && <button className="btn btn--sm" onClick={() => setResultsCursor(undefined)}>First page</button>}
              {catalog.resultsNext && <button className="btn btn--sm" onClick={() => setResultsCursor(catalog.resultsNext!)}>Next results</button>}
            </div>
          </section>
        </>}
    </div>
  </div>;
}
