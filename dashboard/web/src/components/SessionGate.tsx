import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { Lock } from "lucide-react";
import { sessionActions, sessionGuard } from "../service";
import { attachSessionGuard, type SessionGuard, type SessionState } from "../service/session";
import { useStore } from "../store";
import { onWipe } from "../service/wipe";

const COPY: Record<Exclude<SessionState, "active">, { title: string; body: string }> = {
  confirming: { title: "Checking your access", body: "This takes a moment. The screen comes back as soon as it is confirmed." },
  unreachable: { title: "Could not confirm your access", body: "The service did not answer. What was on screen stays hidden until it does. Nothing was changed." },
  expired: { title: "Your session has ended", body: "Sign in again to carry on. Your work on this screen is kept until then." },
  revoked: { title: "You no longer have access", body: "Your access was removed or you signed out. What was on this screen has been cleared from this tab." },
  switched: { title: "Another account is signed in", body: "This browser is now signed in as someone else, so what was on this screen has been cleared from this tab." },
};

/* Stands in front of the app when a session can change. Covered states keep the app mounted but hidden
   and out of the tab order. Revoked and switched take it away, so nothing of the old scope stays in the page. */
export function SessionGate({ children }: { children: ReactNode }) {
  if (!sessionGuard) return <>{children}</>;
  return <Gate guard={sessionGuard}>{children}</Gate>;
}

function Gate({ guard, children }: { guard: SessionGuard; children: ReactNode }) {
  const state = useSyncExternalStore(guard.subscribe, guard.state);
  const { dispatch } = useStore();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => attachSessionGuard(guard), [guard]);
  useEffect(() => onWipe(() => dispatch({ type: "reset" })), [dispatch]);
  useEffect(() => { if (state !== "active") heading.current?.focus(); }, [state]);

  const gone = state === "revoked" || state === "switched";
  const copy = state === "active" ? null : COPY[state];
  return (
    <>
      {!gone && <div className="session-app" hidden={!!copy} {...(copy ? { inert: "" } : {})}>{children}</div>}
      {copy && (
        <div className="session-cover" role="alertdialog" aria-modal="true" aria-labelledby="session-title" aria-describedby="session-body">
          <div className="session-card">
            <Lock size={20} aria-hidden="true" />
            <h1 id="session-title" ref={heading} tabIndex={-1}>{copy.title}</h1>
            <p id="session-body" role={state === "confirming" ? "status" : undefined}>{copy.body}</p>
            <div className="actions">
              {state === "unreachable" && <button className="btn btn--primary" onClick={() => void guard.check(true)}>Try again</button>}
              {state === "expired" && <button className="btn btn--primary" onClick={sessionActions.signIn}>Sign in again</button>}
              {gone && <button className="btn btn--primary" onClick={sessionActions.startOver}>Start over</button>}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
