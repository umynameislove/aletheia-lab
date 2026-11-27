import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight, ChevronsUpDown, FileCheck2, FolderInput, Home, LayoutList } from "lucide-react";
import type { HostedCapabilities } from "../../service/hosted";
import { DashboardFrame } from "../DashboardFrame";
import { Account, RailFrame, type HostedAccountActions } from "../Rail";
import { TopbarFrame } from "../Topbar";
import { plainClick } from "../primitives";

export type HostedScreen = "start" | "connect" | "investigation" | "results" | "workspaces";
const labels: Record<HostedScreen, string> = {
  start: "Projects", connect: "Connect project", investigation: "Investigation",
  results: "Results & reproduction", workspaces: "Workspace access",
};

export function HostedFrame({ workspace, screen, resultHref, account, children }: {
  workspace: HostedCapabilities | null; screen: HostedScreen; resultHref?: string;
  account: HostedAccountActions; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  useEffect(() => {
    document.title = `${labels[screen]} · Aletheia`;
  }, [screen]);
  const letters = workspace?.workspace_name.trim().split(/\s+/).slice(0, 2).map(word => word[0]?.toUpperCase() ?? "").join("");
  const links: { screen: HostedScreen; Icon: typeof Home; href: string }[] = [
    { screen: "start", Icon: Home, href: "#/start" },
    ...(workspace?.allowed_actions.includes("import") ? [{ screen: "connect" as const, Icon: FolderInput, href: "#/connect" }] : []),
    ...(resultHref ? [{ screen: "investigation" as const, Icon: LayoutList, href: resultHref }] : []),
    ...(workspace ? [{ screen: "results" as const, Icon: FileCheck2, href: "#/results" }] : []),
  ];
  const rail = <RailFrame open={open} onClose={close} account={<Account hosted={account} />}>
    {workspace && <a className="proj" href="#/workspaces" aria-label="Workspaces" onClick={e => { if (plainClick(e)) close(); }}>
      <span className="proj-av" aria-hidden="true">{letters}</span>
      <span className="proj-txt"><span className="proj-name">{workspace.workspace_name}</span><span className="proj-id">{workspace.role}</span></span>
      <ChevronsUpDown size={14} aria-hidden="true" />
    </a>}
    <nav className="nav" aria-label="Main">{links.map(({ screen: item, Icon, href }) => (
      <a className="nav-link" key={item} href={href} aria-current={item === screen ? "page" : undefined} onClick={e => { if (plainClick(e)) close(); }}>
        {item === screen && <span className="nav-ind" aria-hidden="true" />}
        <Icon size={16} strokeWidth={1.75} aria-hidden="true" />{labels[item]}
      </a>
    ))}</nav>
  </RailFrame>;
  return <DashboardFrame rail={rail} topbar={<TopbarFrame onMenu={() => setOpen(true)} label={labels[screen]} mode="Hosted">
    {workspace && screen !== "workspaces" && <><a href="#/start">{workspace.workspace_name}</a><ChevronRight size={12} aria-hidden="true" /></>}
  </TopbarFrame>}>{children}</DashboardFrame>;
}
