import { useEffect, useRef, useState, type FormEvent } from "react";
import { Search } from "lucide-react";
import { HostedRequestDiscarded, type createHostedClient, type HostedClaim, type HostedEvidenceItem, type HostedGraphEdge,
  type HostedGraphNode, type HostedResultHeader, type HostedResultScope } from "../../service/hosted-client";
import type { HostedCapabilities } from "../../service/hosted";
import { isProductError } from "../../service/port";
import { TERM } from "../../vocab";
import { Id, Term } from "../primitives";
import { RefusalNote, refusalOf, type Refusal } from "../Ask";

/* A result too large to open whole (413 response_too_large) in a workspace that offers K10 bounded reads:
   its header with the whole result's exact counts, claims a page at a time, and one claim's evidence on
   request. Nothing is assembled into a ProductView, and a partial read is never drawn as the lineage. */
interface Reader { client: ReturnType<typeof createHostedClient>; caps: HostedCapabilities; scope: HostedResultScope; isCurrent: () => boolean }
const RESTART = new Set(["cursor_invalid", "cursor_expired"]);
const EVIDENCE_STEP = 10;
/* the most graph items (nodes and edges) this view holds; past it, reading stops instead of growing the page */
const GRAPH_HOLD = 1_000;
const NODE: Record<string, string> = { AtomicClaim: "Claim", EvidenceItem: "Evidence", Disposition: "Conclusion", Snapshot: "Snapshot" };
const EDGE: Record<string, string> = { CITES: "cites", OBSERVED_IN: "observed in", ASSIGNED_DISPOSITION: "concludes" };
const count = (n: number) => n.toLocaleString("en");
const ignored = (error: unknown) => error instanceof HostedRequestDiscarded;

export function LargeResult(reader: Reader) {
  const { client, caps, scope, isCurrent } = reader;
  const [header, setHeader] = useState<HostedResultHeader | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  useEffect(() => {
    const stop = new AbortController();
    client.loadResultHeader(caps.workspace_id, caps, scope, stop.signal).then(
      value => { if (isCurrent()) setHeader(value); },
      error => { if (isCurrent() && !ignored(error)) setRefusal(refusalOf(error)); });
    return () => stop.abort();
  }, [client, caps, scope, isCurrent]);
  if (refusal) return <div className="boot"><div><b>This result could not be opened</b><RefusalNote refusal={refusal} /></div></div>;
  if (!header) return <div className="boot" role="status">Loading the result summary…</div>;
  const { result, runtime, counts } = header;
  return (
    <main className="page" id="main"><div className="page-inner large-result">
      <header>
        <p className="eyebrow">{header.display_name}</p>
        <h1>Large result, read in pages</h1>
        <p className="dim">Too large to open whole, so its claims and lineage load a page at a time. Metric details are not shown here, and the lineage is listed, not drawn.</p>
      </header>
      <section className="verdict" aria-label="What this result says">
        <p className="verdict-answer">{result.summary}</p>
        <div className="verdict-facts">
          <div className="fact"><span className="eyebrow">Conclusion</span><Term k={result.disposition ?? "null"} /></div>
          <div className="fact"><span className="eyebrow">Cause</span><Term k={result.causal_status} hollow /></div>
          <div className="fact"><span className="eyebrow">Answered by</span><Term k={TERM[runtime.model] ? runtime.model : runtime.provider} hollow /></div>
        </div>
        {result.caveat && <p className="verdict-caveat">{result.caveat}</p>}
      </section>
      <dl className="kv">
        <dt>Claims</dt><dd>{count(counts.claims)}</dd>
        <dt>Evidence</dt><dd>{count(counts.evidence)}</dd>
        <dt>Turns</dt><dd>{count(counts.turns)}</dd>
        <dt>Lineage</dt><dd>{count(counts.nodes)} nodes, {count(counts.edges)} edges</dd>
        <dt>Result</dt><dd><Id v={header.result_id} /></dd>
        <dt>Snapshot</dt><dd><Id v={header.snapshot_id} /></dd>
      </dl>
      <Claims reader={reader} total={counts.claims} />
      <Graph reader={reader} nodes={counts.nodes} edges={counts.edges} />
    </div></main>
  );
}

function Claims({ reader, total }: { reader: Reader; total: number }) {
  const { client, caps, scope, isCurrent } = reader;
  const [text, setText] = useState("");
  const [items, setItems] = useState<HostedClaim[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [found, setFound] = useState<number | null>(total);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [restarted, setRestarted] = useState(false);
  const query = useRef(""), generation = useRef(0), stop = useRef<AbortController | null>(null);
  useEffect(() => () => stop.current?.abort(), []);

  /* One page for the current search. A continuation keeps that search; a cursor the service no longer
     honours drops every page read so far and starts the same search again, never splicing two sequences. */
  async function load(search: string, after: string | null) {
    const mine = ++generation.current;
    stop.current?.abort(); stop.current = new AbortController();
    setBusy(true); setRefusal(null);
    if (after === null) { setItems([]); setCursor(null); }
    try {
      const page = await client.loadClaimPage(caps.workspace_id, caps, scope, { cursor: after, search }, stop.current.signal);
      if (mine !== generation.current || !isCurrent()) return;
      setItems(read => after === null ? page.items : [...read, ...page.items.filter(c => !read.some(r => r.id === c.id))]);
      setCursor(page.next_cursor);
      if (after === null) setFound(search ? page.total : total); else setRestarted(false);
    } catch (error) {
      if (mine !== generation.current || !isCurrent() || ignored(error)) return;
      if (after !== null && isProductError(error) && RESTART.has(error.code)) { setRestarted(true); void load(search, null); return; }
      setRefusal(refusalOf(error));
    } finally { if (mine === generation.current) setBusy(false); }
  }
  useEffect(() => { void load("", null); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const search = text.trim();
    query.current = search; setRestarted(false); setFound(null);
    void load(search, null);
  };
  const shown = items.length;
  return (
    <section className="claims-sec" aria-labelledby="large-claims">
      <div className="claims-head"><h2 id="large-claims">Claims <span className="count-badge">{count(total)}</span></h2></div>
      <form className="toolbar" role="search" onSubmit={submit}>
        <div className="search">
          <Search size={14} aria-hidden="true" />
          <input className="input" type="search" aria-label="Search claims" placeholder="Search claims by word" maxLength={128}
            value={text} onChange={e => setText(e.target.value)} />
        </div>
        <button className="btn btn--sm" type="submit" aria-disabled={busy || undefined}>Search</button>
      </form>
      {restarted && <p className="notice" role="status">This list changed or timed out, so it started again from the first page.</p>}
      <p className="dim" role="status" aria-live="polite">
        {busy && shown === 0 ? "Loading claims…"
          : query.current && shown === 0 && !cursor ? `No claims match “${query.current}”.`
          : `Showing ${count(shown)}${found === null ? "" : ` of ${count(found)}`}${query.current ? ` matching “${query.current}”` : ""}.`}
      </p>
      <ol className="large-claims">
        {items.map(claim => <ClaimRow key={claim.id} claim={claim} reader={reader} />)}
      </ol>
      <RefusalNote refusal={refusal} />
      {cursor && <div className="actions"><button className="btn" aria-busy={busy} aria-disabled={busy || undefined}
        onClick={() => { if (!busy) void load(query.current, cursor); }}>{busy ? "Loading…" : "Load more claims"}</button></div>}
    </section>
  );
}

function ClaimRow({ claim, reader }: { claim: HostedClaim; reader: Reader }) {
  const ids = [...claim.citation_ids, ...claim.counterevidence_ids];
  const [open, setOpen] = useState(false);
  const [evidence, setEvidence] = useState<HostedEvidenceItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const stop = useRef<AbortController | null>(null);
  useEffect(() => () => stop.current?.abort(), []);
  // evidence is read one item at a time, only for the claim the person opened, a few at a time
  async function more() {
    if (busy) return;
    stop.current = new AbortController(); setBusy(true); setRefusal(null);
    const next = ids.slice(evidence.length, evidence.length + EVIDENCE_STEP);
    try {
      for (const id of next) {
        const item = await reader.client.loadClaimEvidence(reader.caps.workspace_id, reader.caps, reader.scope, id, claim.id, stop.current.signal);
        if (!reader.isCurrent()) return;
        setEvidence(read => [...read, item]);
      }
    } catch (error) { if (reader.isCurrent() && !ignored(error)) setRefusal(refusalOf(error)); }
    finally { setBusy(false); }
  }
  const toggle = () => { setOpen(!open); if (!open && evidence.length === 0) void more(); };
  return (
    <li className="large-claim">
      <p>{claim.text}</p>
      <div className="large-claim-meta">
        <Term k={claim.support} field="support" />
        <Term k={claim.epistemic_state} hollow />
        {ids.length > 0
          ? <button className="btn btn--sm btn--ghost" aria-expanded={open} onClick={toggle}>{open ? "Hide evidence" : `Show evidence (${ids.length})`}</button>
          : <span className="dim">No evidence cited</span>}
      </div>
      {open && <div className="large-evidence">
        <ul>
          {evidence.map(item => (
            <li key={item.id}>
              <span className="eyebrow">{item.relation === "citation" ? "Cited in support" : "Cited against"}</span>
              <b>{item.title}</b>
              <p>{item.redacted ? <span className="dim">Withheld from this view.</span> : item.text}</p>
            </li>
          ))}
        </ul>
        {busy && <p className="dim" role="status">Loading evidence…</p>}
        <RefusalNote refusal={refusal} compact />
        {!busy && evidence.length < ids.length && <button className="btn btn--sm" onClick={() => { void more(); }}>
          Show more evidence ({ids.length - evidence.length} left)</button>}
      </div>}
    </li>
  );
}

/* The lineage, read a page at a time on request and merged by ID (edge pages repeat their endpoint nodes).
   Only a null cursor ends it; an expired cursor drops what was read and starts again, never splicing two
   reads. The union is called whole only when it matches the header's exact counts. */
function Graph({ reader, nodes: totalNodes, edges: totalEdges }: { reader: Reader; nodes: number; edges: number }) {
  const { client, caps, scope, isCurrent } = reader;
  const [read, setRead] = useState<{ nodes: Map<string, HostedGraphNode>; edges: Map<string, HostedGraphEdge> } | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [restarted, setRestarted] = useState(false);
  const generation = useRef(0), stop = useRef<AbortController | null>(null);
  useEffect(() => () => stop.current?.abort(), []);

  async function load(after: string | null, restarting = false) {
    const mine = ++generation.current;
    stop.current?.abort(); stop.current = new AbortController();
    setBusy(true); setRefusal(null);
    try {
      const page = await client.loadGraphPage(caps.workspace_id, caps, scope, { cursor: after }, stop.current.signal);
      if (mine !== generation.current || !isCurrent()) return;
      setRead(held => {
        const nodes = new Map(after === null ? [] : held?.nodes), edges = new Map(after === null ? [] : held?.edges);
        for (const node of page.nodes) nodes.set(node.id, node);
        for (const edge of page.edges) edges.set(edge.id, edge);
        return { nodes, edges };
      });
      setCursor(page.next_cursor);
      if (!restarting) setRestarted(false);
    } catch (error) {
      if (mine !== generation.current || !isCurrent() || ignored(error)) return;
      if (after !== null && !restarting && isProductError(error) && RESTART.has(error.code)) {
        setRestarted(true); setRead(null); setCursor(null); void load(null, true); return;
      }
      setRefusal(refusalOf(error));
    } finally { if (mine === generation.current) setBusy(false); }
  }

  if (totalNodes === 0 && totalEdges === 0) return <section className="large-graph" aria-labelledby="large-graph"><h2 id="large-graph">Lineage</h2>
    <p className="dim">This result has no lineage graph.</p></section>;
  const held = read ? read.nodes.size + read.edges.size : 0, full = held >= GRAPH_HOLD;
  const ended = read !== null && cursor === null;
  const whole = ended && read.nodes.size === totalNodes && read.edges.size === totalEdges;
  const label = (nodeId: string) => { const node = read?.nodes.get(nodeId);
    return node ? <><span>{NODE[node.kind] ?? node.kind}</span> <Id v={node.source_id} /></> : <Id v={nodeId} />; };
  const kinds = read ? [...read.nodes.values()].reduce((by, node) => by.set(NODE[node.kind] ?? node.kind, (by.get(NODE[node.kind] ?? node.kind) ?? 0) + 1), new Map<string, number>()) : null;
  return (
    <section className="large-graph" aria-labelledby="large-graph">
      <h2 id="large-graph">Lineage</h2>
      {restarted && <p className="notice" role="status">The lineage changed or timed out, so it started again from the first page.</p>}
      <p className="dim" role="status" aria-live="polite">
        {read === null ? (busy ? "Loading the lineage…" : "Read the lineage in pages, as a list of how claims, evidence and conclusions connect.")
          : `Read ${count(read.nodes.size)} of ${count(totalNodes)} nodes and ${count(read.edges.size)} of ${count(totalEdges)} edges.`
            + (whole ? " That is the whole lineage." : ended ? " The pages did not add up to these counts, so this is not the whole lineage." : "")}
      </p>
      {kinds && kinds.size > 0 && <p className="large-graph-kinds">{[...kinds].map(([kind, n]) => `${kind} ${count(n)}`).join(", ")}</p>}
      {read && read.edges.size > 0 && <ul className="large-graph-edges">
        {[...read.edges.values()].map(edge => <li key={edge.id}>{label(edge.source)} <b>{EDGE[edge.kind] ?? edge.kind.toLowerCase().replace(/_/g, " ")}</b> {label(edge.target)}</li>)}
      </ul>}
      <RefusalNote refusal={refusal} />
      {full && cursor && <p className="dim">This view holds up to {count(GRAPH_HOLD)} lineage items, so reading stops here. The rest is not loaded.</p>}
      {(read === null || (cursor && !full)) && <div className="actions"><button className="btn" aria-busy={busy} aria-disabled={busy || undefined}
        onClick={() => { if (!busy) void load(read === null ? null : cursor); }}>{busy ? "Loading…" : read === null ? "Read the lineage" : "Load more of the lineage"}</button></div>}
    </section>
  );
}
