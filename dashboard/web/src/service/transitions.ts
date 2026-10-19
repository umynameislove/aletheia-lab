/* Pure client transitions, shared with the dependency-free regression tests. */

/** A single ordering boundary for initial loads, navigation and directly returned results. */
export function resultTransitions<T>(options: {
  read: (id: string | null) => Promise<T>;
  identify: (view: T) => string;
  accept: (view: T) => void;
  reject: (error: unknown) => void;
}) {
  let generation = 0;
  let target: string | null | undefined;
  let current: T | undefined;
  const accept = (view: T) => { current = view; options.accept(view); };

  const load = async (id: string | null) => {
    target = id;
    const request = ++generation;
    // Back to the displayed result must both cancel a pending load and restore a failed boot.
    if (id !== null && current !== undefined && options.identify(current) === id) {
      accept(current);
      return;
    }
    try {
      const view = await options.read(id);
      if (request === generation) accept(view);
    } catch (error) {
      if (request === generation) options.reject(error);
    }
  };

  return {
    load,
    navigate(id: string | null) {
      // Filter/selection changes on the same result do not restart its request.
      if (target === id) return Promise.resolve();
      if (id !== null) return load(id);
      // A confirmed project without a result keeps the displayed view, not its pending request.
      target = null;
      ++generation;
      return Promise.resolve();
    },
    show(view: T) {
      ++generation;
      target = options.identify(view);
      accept(view);
    },
    cancel() { ++generation; target = undefined; },
  };
}

interface ProjectSession {
  activeProjectId?: string;
  deletedConversation: boolean;
  deletedProject: boolean;
  receipt: { project_id: string } | null;
}

/** A receipt only closes the project it names, not another project opened later. */
export function activateProject<T extends ProjectSession>(state: T, projectId: string): T {
  if (state.activeProjectId === projectId) return state;
  const obsolete = state.receipt && state.receipt.project_id !== projectId;
  return obsolete
    ? { ...state, activeProjectId: projectId, deletedConversation: false, deletedProject: false, receipt: null }
    : { ...state, activeProjectId: projectId };
}

/** A late deletion response from the previous screen cannot close the active project. */
export function projectDeleted<T extends ProjectSession>(state: T, receipt: NonNullable<T["receipt"]>): T {
  if (state.activeProjectId && state.activeProjectId !== receipt.project_id) return state;
  return { ...state, deletedConversation: true, deletedProject: true, receipt };
}

interface RunSelection {
  runs: string[];
  baseline: string;
  configs: Record<string, string[]>;
}

/** The reference is a tracked run too; selections and attachments are never capped at two. */
export const declaredRuns = (mapping: Pick<RunSelection, "runs" | "baseline">): string[] =>
  [...new Set([...mapping.runs, mapping.baseline])].filter(Boolean);

export const runMappings = (mapping: RunSelection) => declaredRuns(mapping).map(run_id => ({
  run_id, config_item_ids: mapping.configs[run_id] ?? [],
}));
