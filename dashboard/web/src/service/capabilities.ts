/* What this build can really do (Q-03.05). Field names follow capabilities_required in the release
   contract, so a hosted GET /capabilities (P04, K-02) can replace localCapabilities without touching
   the screens. Read from the port the app is running on, never from a string in the environment. */

export interface Capabilities {
  deployment_mode: "demo" | "local" | "hosted";
  schema_version: string;
  analysis_backends: string[];
  supported_source_types: string[];
  supported_export_formats: ("json" | "markdown" | "pdf")[];
  allowed_actions: string[];
}

export type Availability = "available" | "local_only" | "gated" | "unsupported";

export function localCapabilities(kind: "demo" | "live"): Capabilities {
  const live = kind === "live";
  return {
    deployment_mode: live ? "local" : "demo",
    schema_version: "p6-product-view/v1",
    analysis_backends: ["mock"],
    supported_source_types: live ? ["local_folder"] : [],
    supported_export_formats: live ? ["json", "markdown", "pdf"] : ["json", "markdown"],
    allowed_actions: ["import", "refresh", "analyze", "follow_up", "export", "delete"],
  };
}

export const FEATURES: Record<string, { availability: Availability; label: string }> = {
  counterfactual: { availability: "gated", label: "gated" },
  prospective: { availability: "unsupported", label: "out of scope" },
};

export const modeWords = (mode: Capabilities["deployment_mode"]) =>
  mode === "local" ? "Local app" : mode === "demo" ? "Demo" : "Hosted";
