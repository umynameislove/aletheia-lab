import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// fonts are bundled with the app — no request leaves the machine at runtime
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { StoreProvider } from "./store";
import { App } from "./App";
import { ServiceProvider } from "./service";
import { remedyFor } from "./service/errors";

/* Shown only while the port answers, or when it answers with a ProductError: the code and the
   safe message, never a stack trace. */
function BootScreen({ boot }: { boot: { status: string; error?: { code: string; safe_message: string } } }) {
  return (
    <div className="boot" role={boot.status === "error" ? "alert" : "status"}>
      {boot.status === "error" && boot.error ? <>
        <span className="mk hollow" aria-hidden="true" />
        <div><b>The product view could not be loaded</b><code className="mono-s">{boot.error.code}</code><p>{remedyFor(boot.error.code).advice ?? ""} {boot.error.safe_message}</p>
          {/* a dead or deleted link must not trap the page: drop it and start from Start, nothing substituted */}
          <button className="btn btn--sm" onClick={() => { location.replace(location.pathname + "#/start"); location.reload(); }}>Start over</button></div>
      </> : <span className="dim">Loading the view…</span>}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <ServiceProvider fallback={boot => <BootScreen boot={boot} />}>
        <App />
      </ServiceProvider>
    </StoreProvider>
  </StrictMode>,
);
