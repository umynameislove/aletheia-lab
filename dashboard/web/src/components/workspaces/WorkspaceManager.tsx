import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { type HostedCapabilities, type HostedSession } from "../../service/hosted";
import { ProductError } from "../../service/port";
import {
  createWorkspaceClient, WorkspaceRequestDiscarded, type InviteRole, type WorkspaceFetch,
  type InvitationTarget, type InvitationTargetPage, type PendingInvitationPage, type WorkspacePage,
  type WorkspaceInvitation, type WorkspaceMember, type WorkspaceMemberPage, type WorkspaceRole,
} from "../../service/workspace-client";
import { ConfirmDialog } from "../Modal";
import { Id, TableScroll } from "../primitives";

export interface WorkspaceManagerProps {
  session: HostedSession;
  workspace: HostedCapabilities | null;
  /** Host-approved UX only; the POST endpoint still decides permission. Defaults to false. */
  canCreateWorkspace?: boolean;
  fetch: WorkspaceFetch;
  signal?: AbortSignal;
  isCurrent: () => boolean;
  onSessionRefresh: () => void | Promise<void>;
  onCapabilitiesRefresh: (workspaceId: string) => void | Promise<void>;
  onWorkspaceSelected: (workspaceId: string) => void;
  onAuthError: (error: ProductError) => void;
  onPrivateStateCleared?: () => void;
}

/** The host owns login/session/workspace selection; this screen owns only workspace management. */
export function WorkspaceManager(props: WorkspaceManagerProps) {
  const key = JSON.stringify([props.session.user_id, props.session.context_id, props.session.csrf_token,
    props.workspace?.workspace_id, props.workspace?.role, props.workspace?.membership_revision]);
  const latest = useRef(key);
  latest.current = key;
  return <WorkspaceScreen key={key} {...props} currentContext={() => latest.current === key && (typeof props.isCurrent === "function" && props.isCurrent() === true)} />;
}

function WorkspaceScreen(props: WorkspaceManagerProps & { currentContext: () => boolean }) {
  const inputs = useRef(props);
  inputs.current = props;
  const mounted = useRef(false), controller = useRef<AbortController | null>(null), busyRef = useRef(false);
  const lostRef = useRef(false), heading = useRef<HTMLHeadingElement>(null), validating = useRef(false);
  const [lost, setLost] = useState(false), [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState<ProductError | null>(null);
  const [notice, setNotice] = useState("");
  const [name, setName] = useState(""), [acceptToken, setAcceptToken] = useState("");
  const [inviteEmail, setInviteEmail] = useState(""), [inviteRole, setInviteRole] = useState<InviteRole>("viewer");
  const [inviteExpiry, setInviteExpiry] = useState("86400"), [issued, setIssued] = useState<WorkspaceInvitation | null>(null);
  const [issuedName, setIssuedName] = useState("");
  const [targets, setTargets] = useState<InvitationTargetPage | null>(null), [target, setTarget] = useState<InvitationTarget | null>(null);
  const [lookupPending, setLookupPending] = useState(false), [targetCursor, setTargetCursor] = useState<string | undefined>();
  const [targetPrevious, setTargetPrevious] = useState<(string | undefined)[]>([]), targetRequest = useRef(0);
  const [directory, setDirectory] = useState<WorkspacePage | null>(null), [directoryCursor, setDirectoryCursor] = useState<string | undefined>();
  const [directoryPrevious, setDirectoryPrevious] = useState<(string | undefined)[]>([]), directoryRequest = useRef(0);
  const [pending, setPending] = useState<PendingInvitationPage | null>(null), [pendingCursor, setPendingCursor] = useState<string | undefined>();
  const [pendingPrevious, setPendingPrevious] = useState<(string | undefined)[]>([]), pendingRequest = useRef(0);
  const [members, setMembers] = useState<WorkspaceMemberPage | null>(null), [cursor, setCursor] = useState<string | undefined>();
  const [previous, setPrevious] = useState<(string | undefined)[]>([]);
  const pageRequest = useRef(0);
  const [confirm, setConfirm] = useState<{ title: string; action: string; run: () => void } | null>(null);
  const workspace = props.workspace, manager = workspace?.role === "owner" || workspace?.role === "admin";
  const current = () => mounted.current && !lostRef.current && inputs.current.currentContext() && !inputs.current.signal?.aborted;

  function clearPrivate() {
    lostRef.current = true;
    setLost(true); setMembers(null); setDirectory(null); setPending(null); setTargets(null); setTarget(null);
    setIssued(null); setIssuedName(""); setName(""); setAcceptToken(""); setInviteEmail("");
    setConfirm(null); setError(null); setNotice(""); setChecking(false);
    inputs.current.onPrivateStateCleared?.();
    queueMicrotask(() => heading.current?.focus());
  }

  const client = useMemo(() => createWorkspaceClient({
    session: props.session, workspaceId: props.workspace?.workspace_id ?? null,
    fetch: (url, init) => inputs.current.fetch(url, { ...init, signal: controller.current?.signal ?? inputs.current.signal }),
    isCurrent: current,
    onAuthError: value => { clearPrivate(); inputs.current.onAuthError(value); },
  }), []);

  useLayoutEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    mounted.current = true;
    controller.current = new AbortController();
    const abort = () => { controller.current?.abort(); clearPrivate(); };
    inputs.current.signal?.addEventListener("abort", abort, { once: true });
    if (inputs.current.signal?.aborted) abort();
    return () => {
      mounted.current = false; controller.current?.abort();
      props.signal?.removeEventListener("abort", abort);
      inputs.current.onPrivateStateCleared?.();
    };
  }, [props.signal]);

  function report(value: unknown) {
    if (!current() || value instanceof WorkspaceRequestDiscarded) return;
    const failure = value instanceof ProductError ? value : new ProductError("unavailable", "The workspace could not be confirmed. Refresh before trying again.");
    if (["session_expired", "session_revoked", "membership_revoked"].includes(failure.code)) {
      clearPrivate(); inputs.current.onAuthError(failure); return;
    }
    setError(failure);
  }

  async function load(page?: string) {
    const request = ++pageRequest.current;
    try {
      const answer = await client.members({ limit: 50, cursor: page });
      if (current() && request === pageRequest.current) { setMembers(answer); setCursor(page); }
    } catch (failure) { if (request === pageRequest.current) report(failure); }
  }

  async function loadDirectory(page?: string) {
    const request = ++directoryRequest.current;
    try {
      const answer = await client.workspaces({ limit: 50, cursor: page });
      if (current() && request === directoryRequest.current) { setDirectory(answer); setDirectoryCursor(page); }
    } catch (failure) { if (request === directoryRequest.current) report(failure); }
  }

  async function loadPending(page?: string) {
    const request = ++pendingRequest.current;
    try {
      const answer = await client.pendingInvitations({ limit: 50, cursor: page });
      if (current() && request === pendingRequest.current) { setPending(answer); setPendingCursor(page); }
    } catch (failure) { if (request === pendingRequest.current) report(failure); }
  }

  async function lookup(page?: string) {
    const request = ++targetRequest.current;
    setLookupPending(true); setTarget(null); setError(null);
    try {
      const answer = await client.lookupInvitationTargets(inviteEmail.trim(), { limit: 20, cursor: page });
      if (current() && request === targetRequest.current) { setTargets(answer); setTargetCursor(page); }
    } catch (failure) { if (request === targetRequest.current) report(failure); }
    finally { if (current() && request === targetRequest.current) setLookupPending(false); }
  }

  async function revalidate(cover = false) {
    if (validating.current || !current()) return;
    validating.current = true;
    if (cover) setChecking(true);
    try {
      await Promise.all([inputs.current.onSessionRefresh(), ...(workspace ? [inputs.current.onCapabilitiesRefresh(workspace.workspace_id)] : [])]);
      if (current()) setChecking(false);
    } catch (failure) { report(failure); if (current()) setChecking(true); }
    finally { validating.current = false; }
  }

  // Timed and on-return access checks belong to the host's session guard (SessionGate), which covers this screen
  // until session and capabilities answer; a second timer here only repeated those reads.
  useEffect(() => {
    void loadDirectory();
    if (manager) { void load(); void loadPending(); }
  }, []);

  async function change<T>(label: string, work: () => Promise<T>, after?: (value: T) => void, selected?: (value: T) => string) {
    if (!current() || busyRef.current) return;
    busyRef.current = true; setBusy(label); setError(null); setNotice("");
    try {
      const value = await work();
      if (!current()) return;
      const target = selected?.(value) ?? workspace?.workspace_id;
      const refreshes = [Promise.resolve(inputs.current.onSessionRefresh())];
      // a workspace being selected has its capabilities read by the selection itself
      if (target && !selected) refreshes.push(Promise.resolve(inputs.current.onCapabilitiesRefresh(target)));
      after?.(value);
      if (selected && target) inputs.current.onWorkspaceSelected(target);
      await Promise.all(refreshes);
      if (current()) {
        setNotice("The server confirmed the change.");
        setPrevious([]); setDirectoryPrevious([]); setPendingPrevious([]);
        await loadDirectory();
        if (manager && current()) await Promise.all([load(), loadPending()]);
      }
    } catch (failure) {
      report(failure);
      if (current() && failure instanceof ProductError && ["revision_conflict", "outcome_unknown", "permission_denied", "csrf_invalid"].includes(failure.code)) {
        await revalidate(true);
        if (current() && manager) { setPrevious([]); setPendingPrevious([]); await Promise.all([load(), loadPending()]); }
      }
    } finally { busyRef.current = false; if (mounted.current) setBusy(null); }
  }

  const mutate = (member: WorkspaceMember, update: { role?: WorkspaceRole; active?: boolean }) => {
    void change("Updating membership", () => client.changeMember(member.user_id, update, member.revision), value => {
      if (value.user_id === props.session.user_id && !value.active) {
        clearPrivate(); inputs.current.onAuthError(new ProductError("membership_revoked", "You have left this workspace."));
      }
    });
  };

  return <div className="page" aria-busy={busy !== null || checking}>
    <div className="page-inner page-inner--narrow">
      <header className="page-head"><h1 ref={heading} tabIndex={-1}>Workspace access</h1>
        <p className="lede">{lost ? "Your workspace access changed. Choose a workspace or sign in again." : "Create a workspace, accept an invitation, or manage access."}</p></header>
      {lost ? null : <>
        {error && <div className="notice notice--stop" role="alert"><p>{error.safe_message}</p></div>}
        {notice && <p role="status">{notice}</p>}
        {checking && <div className="notice" role="status"><p>Checking your access…</p>
          <button className="btn" onClick={() => void revalidate(true)}>Try again</button></div>}
        <div hidden={checking} {...(checking ? { inert: "" } : {})} className="stack-lg" style={{ marginTop: "var(--s5)" }}>
          <section className="panel" aria-labelledby="workspace-choose-title">
            <h2 id="workspace-choose-title" className="panel-title">Your workspaces</h2>
            {!directory ? <p role="status">Loading workspaces…</p> : !directory.items.length
              ? <p className="body-copy">No workspaces on this page. Accept an invitation or create your first workspace below.</p>
              : <ul className="checklist">{directory.items.map(value => <li key={value.workspace_id}>
                <div><b>{value.workspace_name}</b><span className="sub-line">{value.membership.role} · <Id v={value.workspace_id} /></span></div>
                <button className="btn btn--sm" disabled={!!busy || value.workspace_id === workspace?.workspace_id} onClick={() => {
                  void change("Selecting workspace", () => client.readWorkspace(value.workspace_id), undefined, selected => selected.workspace_id);
                }}>{value.workspace_id === workspace?.workspace_id ? "Current workspace" : "Open workspace"}</button>
              </li>)}</ul>}
            <PageButtons label="workspace" back={!!directoryPrevious.length} next={!!directory?.next_cursor} disabled={!!busy}
              onBack={() => { const page = directoryPrevious[directoryPrevious.length - 1]; setDirectoryPrevious(directoryPrevious.slice(0, -1)); void loadDirectory(page); }}
              onNext={() => { if (directory?.next_cursor) { setDirectoryPrevious([...directoryPrevious, directoryCursor]); void loadDirectory(directory.next_cursor); } }}
              onFirst={() => { setDirectoryPrevious([]); void loadDirectory(); }} />
          </section>

          {!workspace && <section className="panel" aria-labelledby="workspace-create-title">
            <h2 id="workspace-create-title" className="panel-title">Start a workspace</h2>
            {props.canCreateWorkspace === true ? <form onSubmit={event => {
              event.preventDefault(); void change("Creating workspace", () => client.create(name.trim()), () => setName(""), value => value.workspace_id);
            }}><label className="fld"><span className="fld-label">Workspace name</span>
              <input className="input" value={name} maxLength={256} required autoComplete="off" onChange={event => setName(event.target.value)} /></label>
              <div className="actions"><button className="btn btn--primary" disabled={!!busy || !name.trim()}>Create workspace</button></div>
            </form> : <p className="body-copy">Workspace creation is not enabled for your account. Ask a workspace manager for an invitation.</p>}
          </section>}

          <section className="panel" aria-labelledby="workspace-accept-title">
            <h2 id="workspace-accept-title" className="panel-title">Accept an invitation</h2>
            <form onSubmit={event => { event.preventDefault(); void change("Accepting invitation", () => client.acceptInvitation(acceptToken),
              () => setAcceptToken(""), value => value.workspace_id); }}>
              <label className="fld"><span className="fld-label">Invitation token</span><input className="input mono-in" type="password"
                autoComplete="off" value={acceptToken} minLength={43} maxLength={128} required onChange={event => setAcceptToken(event.target.value)} /></label>
              <div className="actions"><button className="btn" disabled={!!busy}>Accept invitation</button></div>
            </form>
          </section>

          {workspace && <section className="panel" aria-labelledby="workspace-current-title">
            <h2 id="workspace-current-title" className="panel-title">{workspace.workspace_name}</h2>
            <dl className="kv"><dt>Your role</dt><dd>{workspace.role}</dd><dt>Workspace</dt><dd><Id v={workspace.workspace_id} /></dd></dl>
            <div className="actions"><button className="btn" disabled={!!busy} onClick={() => void revalidate(true)}>Refresh access</button>
              <button className="btn btn--danger" disabled={!!busy} onClick={() => setConfirm({ title: "Leave this workspace?", action: "Leave workspace",
                run: () => mutate({ workspace_id: workspace.workspace_id, user_id: props.session.user_id, role: workspace.role,
                  display_name: props.session.display_name, active: true, revision: workspace.membership_revision }, { active: false }) })}>Leave workspace</button></div>
          </section>}

          {manager && <section className="panel" aria-labelledby="workspace-members-title">
            <h2 id="workspace-members-title" className="panel-title">Members</h2>
            <p className="body-copy">Roles and ownership changes take effect on the server. An active owner must remain.</p>
            <TableScroll label="Workspace members"><table className="tbl"><thead><tr><th>Account</th><th>Role</th><th>Status</th><th>Access</th></tr></thead>
              <tbody>{members?.items.map(member => <MemberRow key={member.user_id + ":" + member.revision} member={member}
                currentUser={props.session.user_id} owner={workspace?.role === "owner"} disabled={!!busy}
                onChange={role => mutate(member, { role })} onActive={active => setConfirm({
                  title: active ? "Restore workspace access?" : "Remove workspace access?", action: active ? "Restore access" : "Remove access",
                  run: () => mutate(member, { active }),
                })} onTransfer={() => setConfirm({ title: "Transfer workspace ownership?", action: "Transfer ownership",
                  run: () => { void change("Transferring ownership", () => client.transferOwner(member.user_id, member.revision)); },
                })} />)}</tbody></table></TableScroll>
            {!members && <p role="status">Loading members…</p>}
            <div className="pager"><span>{members ? `${members.items.length} members on this page` : ""}</span><div className="pager-btns">
              <button className="btn btn--sm" disabled={!!busy || !previous.length} onClick={() => {
                const prior = previous[previous.length - 1]; setPrevious(previous.slice(0, -1)); void load(prior);
              }}>Previous page</button>
              <button className="btn btn--sm" disabled={!!busy || !members?.next_cursor} onClick={() => {
                if (members?.next_cursor) { setPrevious([...previous, cursor]); void load(members.next_cursor); }
              }}>Next page</button>
              <button className="btn btn--sm" disabled={!!busy} onClick={() => { setPrevious([]); setError(null); void load(); }}>First page</button>
            </div></div>
          </section>}

          {manager && <section className="panel" aria-labelledby="workspace-invite-title">
            <h2 id="workspace-invite-title" className="panel-title">Invite a member</h2>
            <p className="body-copy">Look up the exact verified email, then choose the account to invite. An email match never joins an account automatically.</p>
            <form onSubmit={event => { event.preventDefault(); setTargetPrevious([]); void lookup(); }}>
              <label className="fld"><span className="fld-label">Verified email address</span><input className="input" type="email" value={inviteEmail} required
                maxLength={254} autoComplete="off" onChange={event => {
                  setInviteEmail(event.target.value); targetRequest.current++; setTargets(null); setTarget(null); setLookupPending(false); setTargetPrevious([]);
                }} /></label>
              <div className="actions"><button className="btn" disabled={!!busy || lookupPending}>Find accounts</button></div>
            </form>
            {lookupPending && <p role="status">Finding matching accounts…</p>}
            {targets && <>
              {!targets.items.length ? <p className="body-copy">No eligible account was found on this page. Check the address or ask your administrator.</p>
                : <fieldset><legend className="form-h">Choose an account</legend>{targets.items.map(value => <label className="fld-note" key={value.user_id}>
                  <input type="radio" name="workspace-invite-target" value={value.user_id} checked={target?.user_id === value.user_id}
                    disabled={!!busy || lookupPending} onChange={() => setTarget(value)} />
                  <span>{value.display_name}<span className="sub-line"><Id v={value.user_id} /></span></span>
                </label>)}</fieldset>}
              <PageButtons label="account match" back={!!targetPrevious.length} next={!!targets.next_cursor} disabled={!!busy || lookupPending}
                onBack={() => { const page = targetPrevious[targetPrevious.length - 1]; setTargetPrevious(targetPrevious.slice(0, -1)); void lookup(page); }}
                onNext={() => { if (targets.next_cursor) { setTargetPrevious([...targetPrevious, targetCursor]); void lookup(targets.next_cursor); } }}
                onFirst={() => { setTargetPrevious([]); void lookup(); }} />
            </>}
            <form onSubmit={event => {
              event.preventDefault(); if (!target) return;
              const selected = target;
              void change("Creating invitation", () => client.invite(selected.user_id, inviteRole, Number(inviteExpiry)), value => {
                setIssued(value); setIssuedName(selected.display_name); setInviteEmail(""); setTargets(null); setTarget(null); targetRequest.current++;
              });
            }}><div className="form-grid" style={{ marginTop: "var(--s4)" }}>
              <label className="fld"><span className="fld-label">Role</span><select className="select" value={inviteRole} onChange={event => setInviteRole(event.target.value as InviteRole)}>
                {["admin", "analyst", "reviewer", "viewer"].map(role => <option key={role} value={role}>{role}</option>)}</select></label>
              <label className="fld"><span className="fld-label">Expires after (seconds)</span><input className="input" type="number" min={60} max={604800}
                value={inviteExpiry} required onChange={event => setInviteExpiry(event.target.value)} /></label>
            </div><div className="actions"><button className="btn" disabled={!!busy || !target || lookupPending}>Create invitation</button></div></form>
            {issued && <div className="notice" role="status"><div><p>Invitation ready for {issuedName || issued.user_id}. Expires {new Date(issued.expires_at).toLocaleString()}.</p>
              <label className="fld"><span className="fld-label">Invitation token</span><input className="input mono-in" type="password" readOnly autoComplete="off"
                value={issued.token} onFocus={event => event.currentTarget.select()} /></label>
              <p className="fld-note">Copy the selected token and share it with the invited account through your usual private channel.</p>
              <div className="actions"><button className="btn btn--danger" disabled={!!busy} onClick={() => {
                void change("Revoking invitation", () => client.revokeInvitation(issued.invitation_id), () => setIssued(null));
              }}>Revoke invitation</button><button className="btn" onClick={() => setIssued(null)}>Clear token</button></div>
            </div></div>}
          </section>}

          {manager && <section className="panel" aria-labelledby="workspace-pending-title">
            <h2 id="workspace-pending-title" className="panel-title">Pending invitations</h2>
            {!pending ? <p role="status">Loading invitations…</p> : !pending.items.length ? <p className="body-copy">No pending invitations on this page.</p>
              : <ul className="checklist">{pending.items.map(value => <li key={value.invitation_id}>
                <div><b>{value.display_name}</b><span className="sub-line">{value.role} · expires {new Date(value.expires_at).toLocaleString()}</span>
                  <span className="sub-line"><Id v={value.user_id} /></span></div>
                <button className="btn btn--sm btn--danger" disabled={!!busy} onClick={() => setConfirm({
                  title: "Revoke invitation?", action: "Revoke invitation", run: () => {
                    void change("Revoking invitation", () => client.revokeInvitation(value.invitation_id), () => {
                      if (issued?.invitation_id === value.invitation_id) { setIssued(null); setIssuedName(""); }
                    });
                  },
                })}>Revoke invitation</button>
              </li>)}</ul>}
            <PageButtons label="invitation" back={!!pendingPrevious.length} next={!!pending?.next_cursor} disabled={!!busy}
              onBack={() => { const page = pendingPrevious[pendingPrevious.length - 1]; setPendingPrevious(pendingPrevious.slice(0, -1)); void loadPending(page); }}
              onNext={() => { if (pending?.next_cursor) { setPendingPrevious([...pendingPrevious, pendingCursor]); void loadPending(pending.next_cursor); } }}
              onFirst={() => { setPendingPrevious([]); void loadPending(); }} />
          </section>}
          {busy && <p role="status">{busy}…</p>}
        </div>
      </>}
      {confirm && !lost && <ConfirmDialog title={confirm.title} action={confirm.action} onCancel={() => setConfirm(null)} onConfirm={() => {
        const action = confirm.run; setConfirm(null); action();
      }}>This changes who can access the workspace. The server will check current permissions and ownership.</ConfirmDialog>}
    </div>
  </div>;
}

function MemberRow({ member, owner, currentUser, disabled, onChange, onActive, onTransfer }: {
  member: WorkspaceMember; owner: boolean; currentUser: string; disabled: boolean;
  onChange: (role: WorkspaceRole) => void; onActive: (active: boolean) => void; onTransfer: () => void;
}) {
  const [selected, setSelected] = useState(member.role);
  const editable = owner || member.role !== "owner";
  return <tr><td><span>{member.display_name ?? member.user_id}</span><span className="sub-line"><Id v={member.user_id} /></span>
    {member.user_id === currentUser && <span className="sub-line">You</span>}</td>
    <td><select className="select" aria-label={"Role for " + member.user_id} value={selected} disabled={disabled || !editable}
      onChange={event => setSelected(event.target.value as WorkspaceRole)}>
      <option value="owner" disabled>owner (transfer required)</option>
      {["admin", "analyst", "reviewer", "viewer"].map(role => <option key={role} value={role}>{role}</option>)}</select>
      <button className="btn btn--sm" disabled={disabled || !editable || selected === member.role} onClick={() => onChange(selected)}>Save role</button></td>
    <td>{member.active ? "Active" : "Removed"}</td><td><div className="actions" style={{ marginTop: 0 }}>
      <button className="btn btn--sm btn--danger" disabled={disabled || !editable || (!member.active && member.role === "owner")}
        onClick={() => onActive(!member.active)}>{member.active ? "Remove access" : "Restore access"}</button>
      {owner && member.active && member.user_id !== currentUser && <button className="btn btn--sm" disabled={disabled} onClick={onTransfer}>Transfer ownership</button>}
    </div></td></tr>;
}

function PageButtons({ label, back, next, disabled, onBack, onNext, onFirst }: {
  label: string; back: boolean; next: boolean; disabled: boolean; onBack: () => void; onNext: () => void; onFirst: () => void;
}) {
  return <div className="pager"><div className="pager-btns">
    <button className="btn btn--sm" disabled={disabled || !back} onClick={onBack}>Previous {label} page</button>
    <button className="btn btn--sm" disabled={disabled || !next} onClick={onNext}>Next {label} page</button>
    <button className="btn btn--sm" disabled={disabled} onClick={onFirst}>First {label} page</button>
  </div></div>;
}
