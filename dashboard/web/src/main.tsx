import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// fonts are bundled with the app — no request leaves the machine at runtime
// Latin and Vietnamese for product text; other scripts in imported data use the system font. Mono is for IDs.
import "@fontsource/ibm-plex-sans/vietnamese-400.css";
import "@fontsource/ibm-plex-sans/vietnamese-500.css";
import "@fontsource/ibm-plex-sans/vietnamese-600.css";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-ext-400.css";
import "@fontsource/ibm-plex-sans/latin-ext-500.css";
import "@fontsource/ibm-plex-sans/latin-ext-600.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { StoreProvider } from "./store";
import { App } from "./App";
import { ServiceProvider } from "./service";
import { SessionGate } from "./components/SessionGate";
import { remedyFor } from "./service/errors";

/* Shown only while the port answers, or when it answers with a ProductError: the code and the
   safe message, never a stack trace. */
function BootScreen({ boot }: { boot: { status: string; error?: { code: string; safe_message: string } } }) {
  return (
    <div className="boot" role={boot.status === "error" ? "alert" : "status"}>
      {boot.status === "error" && boot.error ? <>
        <span className="mk hollow" aria-hidden="true" />
        <div><b>This could not be opened</b><p>{remedyFor(boot.error.code).advice ?? ""} {boot.error.safe_message}</p><code className="mono-s">{boot.error.code}</code>
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
