export const state = {
  /** @type {{ url: string, slug: string, company: string, domain: string } | null} */
  discoverContext: null,
  /** @type {Record<string, any> | null} */
  activeRun: null,
  /** @type {string | null} */
  activeRunId: null,
  /** @type {Array<Record<string, any>>} */
  loadedRuns: [],
  runsOffset: 0,
  hasMoreRuns: false,
  discoverInFlight: false,
  importInFlight: false,
  /** @type {string | null} */
  statusFilter: null,
  searchQuery: "",
  sortKey: "name",
  sortDir: "asc",
  /** @type {Set<string>} */
  selectedIds: new Set(),
  /** @type {Set<string>} */
  expandedIds: new Set(),
  /** @type {ReturnType<typeof setInterval> | null} */
  pollTimer: null,
};

export function resetSelection() {
  state.selectedIds = new Set();
  state.expandedIds = new Set();
  state.statusFilter = null;
  state.searchQuery = "";
}

export function setActiveRun(run) {
  state.activeRun = run;
  state.activeRunId = run ? run.run_id : null;
}
