import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// fonts are bundled with the app — no request leaves the machine at runtime
// Latin and Vietnamese for product text, each subset fetched only when on screen (styles/fonts.css);
// other scripts in imported data use the system font. Mono is for IDs.
import "./styles/fonts.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { StoreProvider } from "./store";
import { App } from "./App";
import { ServiceProvider } from "./service";
import { SessionGate } from "./components/SessionGate";
import { RefusalNote } from "./components/Ask";

/* Shown only while the port answers, or when it answers with a ProductError: the code and the
   safe message, never a stack trace. */
function BootScreen({ boot }: { boot: { status: string; error?: { code: string; safe_message: string } } }) {
  return (
    // the error is the shared RefusalNote, which is its own alert; only the loading line is a status
    <div className="boot" role={boot.status === "error" ? undefined : "status"}>
      {boot.status === "error" && boot.error ? <>
        <div><b>This could not be opened</b><RefusalNote refusal={{ code: boot.error.code, message: boot.error.safe_message }} />
          {/* a dead or deleted link must not trap the page: drop it and start from Start, nothing substituted */}
          <div className="actions">
            <button className="btn btn--sm" onClick={() => location.reload()}>Try again</button>
            <button className="btn btn--sm" onClick={() => { location.replace(location.pathname + "#/start"); location.reload(); }}>Start over</button>
          </div></div>
      </> : <span className="dim">Loading…</span>}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <SessionGate>
        <ServiceProvider fallback={boot => <BootScreen boot={boot} />}>
          <App />
        </ServiceProvider>
      </SessionGate>
    </StoreProvider>
  </StrictMode>,
);
