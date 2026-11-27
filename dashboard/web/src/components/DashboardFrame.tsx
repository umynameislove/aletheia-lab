import type { ReactNode } from "react";

export function DashboardFrame({ rail, topbar, children }: { rail: ReactNode; topbar: ReactNode; children: ReactNode }) {
  return <>
    <a className="skip" href="#main" onClick={event => {
      event.preventDefault();
      document.getElementById("main")?.focus();
    }}>Skip to content</a>
    <div className="app">
      {rail}
      <div className="shell">
        {topbar}
        <main className="content">{children}</main>
      </div>
    </div>
  </>;
}
