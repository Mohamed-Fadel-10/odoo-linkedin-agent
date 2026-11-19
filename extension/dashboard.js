import { apiFetch, checkBackend, DISCOVER_TIMEOUT_MS, getPairingToken } from "./api.js";
import { showToast } from "./dialogs-toasts.js";
import { createReportView } from "./report.js";
import { createRunView, renderStepper } from "./run-view.js";
import { createRunsList } from "./runs-list.js";
import { resetSelection, setActiveRun, state } from "./state.js";
import {
  BACKEND_START_CMD,
  buildRunHash,
  copyText,
  createElapsedTracker,
  describeError,
  formatElapsed,
  formatPhase,
  isValidDomain,
  parseRunHash,
  setButtonBusy,
  setConnectionPill,
  setStatusBar,
  shortId,
  statusClassForPhase,
} from "./utils.js";

const els = {
  connPill: document.getElementById("conn-pill"),
  statusBar: document.getElementById("status-bar"),
  phaseBadge: document.getElementById("phase-badge"),
  pageTitle: document.getElementById("page-title"),
  pageSubtitle: document.getElementById("page-subtitle"),
  runMeta: document.getElementById("run-meta"),
  runIdShort: document.getElementById("run-id-short"),
  btnCopyRun: document.getElementById("btn-copy-run"),
  stepper: document.getElementById("workflow-stepper"),
  offline: document.getElementById("panel-offline"),
  discover: document.getElementById("panel-discover"),
  loading: document.getElementById("panel-loading"),
  loadingDetail: document.getElementById("loading-detail"),
  preview: document.getElementById("panel-preview"),
  report: document.getElementById("panel-report"),
  empty: document.getElementById("panel-empty"),
  company: document.getElementById("company"),
  domain: document.getElementById("domain"),
  domainError: document.getElementById("domain-error"),
  discoverUrl: document.getElementById("discover-url"),
  rateLimitHint: document.getElementById("rate-limit-hint"),
  btnDiscover: document.getElementById("btn-discover"),
  btnApprove: document.getElementById("btn-approve"),
  btnSelectAll: document.getElementById("btn-select-all"),
  btnDeselectAll: document.getElementById("btn-deselect-all"),
  btnRefreshRuns: document.getElementById("btn-refresh-runs"),
  btnClearHistory: document.getElementById("btn-clear-history"),
  btnOpenOptions: document.getElementById("btn-open-options"),
  btnShowMore: document.getElementById("btn-show-more"),
  btnCopyCmd: document.getElementById("btn-copy-cmd"),
  btnRetryHealth: document.getElementById("btn-retry-health"),
  btnConfirmCancel: document.getElementById("btn-confirm-cancel"),
  btnConfirmApprove: document.getElementById("btn-confirm-approve"),
  offlineCmd: document.getElementById("offline-cmd"),
  recordsList: document.getElementById("records-list"),
  recordsEmpty: document.getElementById("records-empty"),
  selectionCount: document.getElementById("selection-count"),
  filterLabel: document.getElementById("filter-label"),
  countsRow: document.getElementById("counts-row"),
  searchInput: document.getElementById("record-search"),
  sortSelect: document.getElementById("record-sort"),
  showingLabel: document.getElementById("showing-label"),
  reportSummary: document.getElementById("report-summary"),
  reportBody: document.getElementById("report-body"),
  reportMeta: document.getElementById("report-meta"),
  reportBar: document.getElementById("report-bar"),
  btnCsv: document.getElementById("btn-download-csv"),
  btnCopySummary: document.getElementById("btn-copy-summary"),
  runsList: document.getElementById("runs-list"),
  runsEmpty: document.getElementById("runs-empty"),
  approveControls: document.getElementById("approve-controls"),
  confirmBox: document.getElementById("confirm-box"),
  confirmText: document.getElementById("confirm-text"),
  importResult: document.getElementById("import-result"),
};

const runView = createRunView({
  countsRow: els.countsRow,
  listEl: els.recordsList,
  emptyEl: els.recordsEmpty,
  filterLabel: els.filterLabel,
  selectionCount: els.selectionCount,
  searchInput: els.searchInput,
  sortSelect: els.sortSelect,
  showingLabel: els.showingLabel,
  btnSelectAll: els.btnSelectAll,
  btnDeselectAll: els.btnDeselectAll,
  approveControls: els.approveControls,
});

const reportView = createReportView({
  summaryEl: els.reportSummary,
  bodyEl: els.reportBody,
  metaEl: els.reportMeta,
  barEl: els.reportBar,
  btnCsv: els.btnCsv,
  btnCopySummary: els.btnCopySummary,
});

const runsList = createRunsList({
  listEl: els.runsList,
  emptyEl: els.runsEmpty,
  showMoreBtn: els.btnShowMore,
  clearBtn: els.btnClearHistory,
  onSelect: (runId) => loadRun(runId),
  onDeleted: afterDelete,
});

const discoverElapsed = createElapsedTracker((ms) => {
  if (!state.discoverInFlight) return;
  const stage = els.loadingDetail.dataset.stage;
  const stageNote = stage ? ` Stage: ${stage}.` : "";
  setStatusBar(els.statusBar, {
    state: "working",
    title: "Discovering employees…",
    detail: `Elapsed ${formatElapsed(ms)}. Nothing is written to Odoo yet.${stageNote}`,
  });
  els.loadingDetail.textContent = `Discovering… elapsed ${formatElapsed(ms)}.${stageNote}`;
});

function showPanels(names) {
  const map = {
    offline: els.offline,
    discover: els.discover,
    loading: els.loading,
    preview: els.preview,
    report: els.report,
    empty: els.empty,
  };
  for (const [key, node] of Object.entries(map)) {
    node.classList.toggle("hidden", !names.includes(key));
  }
}

function setPhase(phase, title, subtitle) {
  els.phaseBadge.textContent = formatPhase(phase);
  els.phaseBadge.className = `badge ${statusClassForPhase(phase)}`;
  if (title) els.pageTitle.textContent = title;
  if (subtitle != null) els.pageSubtitle.textContent = subtitle;
  renderStepper(els.stepper, phase);
}

function showError(err) {
  const described = describeError(err, {
    onRetry: () => init().catch((e) => showError(e)),
    onOptions: () => chrome.runtime.openOptionsPage(),
    onCopyCommand: () => copyText(BACKEND_START_CMD),
  });
  setStatusBar(els.statusBar, described);
  showToast(described.detail || described.title, { tone: "error" });
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function loadRun(runId) {
  const detail = await apiFetch(`/runs/${encodeURIComponent(runId)}`, {
    method: "GET",
    timeoutMs: 10000,
  });
  resetSelection();
  for (const id of detail.selected_ids || []) state.selectedIds.add(id);
  setActiveRun(detail);
  history.replaceState({}, "", buildRunHash(runId));
  await chrome.storage.local.set({ lastRunId: runId });
  els.runMeta.classList.remove("hidden");
  els.runIdShort.textContent = shortId(runId);
  setPhase(
    detail.phase,
    detail.company || "Run",
    detail.domain || ""
  );
  if (detail.phase === "imported") {
    showPanels(["report"]);
    reportView.render(detail);
  } else {
    showPanels(["preview", "report"]);
    runView.render();
    reportView.render(detail);
  }
  setStatusBar(els.statusBar, {
    state: "idle",
    title: detail.phase === "imported" ? "Import complete" : "Preview ready",
    detail:
      typeof detail.duration_ms === "number"
        ? `Duration ${formatElapsed(detail.duration_ms)}`
        : "Select Accepted contacts to import.",
  });
  runsList.render();
}

async function afterDelete(deletedId) {
  await runsList.refresh();
  if (deletedId && state.activeRunId === deletedId) {
    setActiveRun(null);
    history.replaceState({}, "", " ");
    if (state.loadedRuns[0]) await loadRun(state.loadedRuns[0].run_id);
    else {
      showPanels(["empty"]);
      setPhase("idle", "Dashboard", "No runs left.");
      els.runMeta.classList.add("hidden");
    }
  } else if (!deletedId) {
    setActiveRun(null);
    history.replaceState({}, "", " ");
    if (state.loadedRuns[0]) await loadRun(state.loadedRuns[0].run_id);
    else {
      showPanels(["empty"]);
      setPhase("idle", "Dashboard", "History cleared.");
      els.runMeta.classList.add("hidden");
    }
  }
}

function validateDiscoverDomain() {
  const value = els.domain.value.trim();
  const ok = isValidDomain(value);
  els.domain.classList.toggle("invalid", Boolean(value) && !ok);
  els.domainError.classList.toggle("hidden", ok || !value);
  els.domainError.textContent = ok || !value ? "" : "Enter a valid domain like example.com";
  const companyOk = Boolean(els.company.value.trim());
  els.btnDiscover.disabled = state.discoverInFlight || !ok || !companyOk;
  return ok && companyOk;
}

function setupDiscoverForm(ctx) {
  state.discoverContext = ctx;
  els.company.value = ctx.company || "";
  els.domain.value = ctx.domain || "";
  els.discoverUrl.textContent = ctx.url ? `LinkedIn URL: ${ctx.url}` : "";
  showPanels(["discover"]);
  setPhase("idle", ctx.company || "New discovery", "Confirm domain, then Discover.");
  els.runMeta.classList.add("hidden");
  validateDiscoverDomain();
}

async function startDiscover() {
  if (!state.discoverContext || state.discoverInFlight) return;
  if (!validateDiscoverDomain()) return;
  const company = els.company.value.trim();
  const domain = els.domain.value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0];
  state.discoverContext.company = company;
  state.discoverContext.domain = domain;
  await chrome.storage.local.set({
    [`domain:${state.discoverContext.slug}`]: domain,
  });

  state.discoverInFlight = true;
  els.loadingDetail.dataset.stage = "";
  setButtonBusy(els.btnDiscover, {
    busy: true,
    busyLabel: "Discovering…",
    idleLabel: "Discover employees",
  });
  showPanels(["loading"]);
  setPhase("in_progress", company, "Discovery in progress…");
  discoverElapsed.start();

  const stagePoll = setInterval(() => {
    if (!state.discoverContext?.slug || !state.discoverInFlight) return;
    apiFetch(
      `/discovery-jobs/latest?slug=${encodeURIComponent(state.discoverContext.slug)}`,
      { method: "GET", timeoutMs: 4000 }
    )
      .then((latest) => {
        if (latest.job?.stage) els.loadingDetail.dataset.stage = String(latest.job.stage);
      })
      .catch(() => {});
  }, 2000);

  try {
    const preview = await apiFetch("/discover", {
      method: "POST",
      timeoutMs: DISCOVER_TIMEOUT_MS,
      body: JSON.stringify({
        url: state.discoverContext.url,
        company,
        domain,
      }),
    });
    discoverElapsed.stop();
    if (preview.job_id) {
      await chrome.storage.local.set({ lastJobId: preview.job_id });
    }
    if (preview.concurrent && preview.status === "running" && preview.job_id) {
      await pollJob(preview.job_id);
      return;
    }
    await runsList.refresh();
    await loadRun(preview.run_id);
    if (preview.reused) {
      showToast(`Showing results from ${preview.age_minutes ?? "?"} min ago`);
    } else if (typeof preview.duration_ms === "number") {
      showToast(`Discovery finished in ${formatElapsed(preview.duration_ms)}`);
    }
  } catch (err) {
    discoverElapsed.stop();
    if (err?.code === "BACKEND_UNAVAILABLE") {
      showPanels(["offline"]);
      setConnectionPill(els.connPill, false);
    } else showPanels(["discover"]);
    showError(err);
  } finally {
    clearInterval(stagePoll);
    state.discoverInFlight = false;
    setButtonBusy(els.btnDiscover, {
      busy: false,
      busyLabel: "Discovering…",
      idleLabel: "Discover employees",
    });
    validateDiscoverDomain();
  }
}

async function pollJob(jobId) {
  stopPolling();
  state.discoverInFlight = true;
  showPanels(["loading"]);
  setPhase("in_progress", "Discovery", "Discovery in progress…");
  discoverElapsed.start();
  const tick = async () => {
    try {
      const job = await apiFetch(`/discovery-jobs/${jobId}`, {
        method: "GET",
        timeoutMs: 8000,
      });
      if (job.stage) els.loadingDetail.dataset.stage = String(job.stage);
      if (job.status === "succeeded" && job.run_id) {
        stopPolling();
        discoverElapsed.stop();
        state.discoverInFlight = false;
        await runsList.refresh();
        await loadRun(job.run_id);
        return;
      }
      if (job.status === "failed") {
        stopPolling();
        discoverElapsed.stop();
        state.discoverInFlight = false;
        showPanels(["discover"]);
        showError(new Error(job.error || "Discovery failed"));
      }
    } catch (err) {
      stopPolling();
      discoverElapsed.stop();
      state.discoverInFlight = false;
      showError(err);
    }
  };
  await tick();
  state.pollTimer = setInterval(tick, 2500);
}

function hideConfirm() {
  els.confirmBox.classList.add("hidden");
}

function showConfirm() {
  const n = [...state.selectedIds].length;
  els.confirmText.textContent = `Import ${n} contact(s) into Odoo? This creates new contacts only.`;
  els.confirmBox.classList.remove("hidden");
}

async function approveImport() {
  if (!state.activeRun || state.activeRun.phase === "imported" || state.importInFlight) {
    return;
  }
  const selectedIds = [...state.selectedIds];
  if (!selectedIds.length) {
    showToast("Select at least one Accepted contact", { tone: "error" });
    return;
  }
  state.importInFlight = true;
  hideConfirm();
  setButtonBusy(els.btnApprove, {
    busy: true,
    busyLabel: "Importing…",
    idleLabel: `Approve import (${selectedIds.length})`,
  });
  try {
    await apiFetch("/approve", {
      method: "POST",
      timeoutMs: 120000,
      body: JSON.stringify({
        run_id: state.activeRun.run_id,
        selected_ids: selectedIds,
      }),
    });
    showToast("Import finished");
    await runsList.refresh();
    await loadRun(state.activeRun.run_id);
  } catch (err) {
    showError(err);
  } finally {
    state.importInFlight = false;
    setButtonBusy(els.btnApprove, {
      busy: false,
      busyLabel: "Importing…",
      idleLabel: "Approve import",
    });
  }
}

async function init() {
  els.offlineCmd.textContent = BACKEND_START_CMD;
  const token = await getPairingToken();
  if (!token) {
    setConnectionPill(els.connPill, false);
    showError(Object.assign(new Error("PAIRING_MISSING"), { code: "PAIRING_MISSING" }));
    showPanels(["empty"]);
    return;
  }
  try {
    await checkBackend();
    setConnectionPill(els.connPill, true);
  } catch (err) {
    setConnectionPill(els.connPill, false);
    showPanels(["offline"]);
    showError(err);
    return;
  }

  await runsList.refresh();
  const params = new URLSearchParams(window.location.search);
  const hashRun = parseRunHash(window.location.hash);
  const stored = await chrome.storage.local.get(["lastRunId", "lastJobId"]);

  if (params.get("company") && params.get("url")) {
    setupDiscoverForm({
      url: params.get("url") || "",
      slug: params.get("slug") || "",
      company: params.get("company") || "",
      domain: params.get("domain") || "",
    });
    if (params.get("autodiscover") === "1") await startDiscover();
    return;
  }

  if (params.get("job_id") || stored.lastJobId) {
    const jobId = params.get("job_id") || stored.lastJobId;
    try {
      const job = await apiFetch(`/discovery-jobs/${jobId}`, {
        method: "GET",
        timeoutMs: 8000,
      });
      if (job.status === "running") {
        await pollJob(jobId);
        return;
      }
      if (job.run_id) {
        await loadRun(job.run_id);
        return;
      }
    } catch {
      /* fall through */
    }
  }

  const runId = hashRun || params.get("run_id") || stored.lastRunId;
  if (runId) {
    try {
      await loadRun(runId);
      return;
    } catch {
      /* fall through */
    }
  }
  if (state.loadedRuns[0]) await loadRun(state.loadedRuns[0].run_id);
  else {
    showPanels(["empty"]);
    setPhase("idle", "Dashboard", "Open a LinkedIn company page to start.");
  }
}

els.btnDiscover.addEventListener("click", () => startDiscover().catch(showError));
els.company.addEventListener("input", validateDiscoverDomain);
els.domain.addEventListener("input", validateDiscoverDomain);
els.btnApprove.addEventListener("click", showConfirm);
els.btnConfirmCancel.addEventListener("click", hideConfirm);
els.btnConfirmApprove.addEventListener("click", () => approveImport().catch(showError));
els.btnRefreshRuns.addEventListener("click", () =>
  runsList.refresh().catch(showError)
);
els.btnOpenOptions.addEventListener("click", () => chrome.runtime.openOptionsPage());
els.btnCopyCmd.addEventListener("click", () => copyText(BACKEND_START_CMD));
els.btnRetryHealth.addEventListener("click", () => init().catch(showError));
els.btnCopyRun.addEventListener("click", async () => {
  if (!state.activeRunId) return;
  const ok = await copyText(state.activeRunId);
  showToast(ok ? "Run id copied" : "Copy failed", { tone: ok ? "success" : "error" });
});
window.addEventListener("hashchange", () => {
  const runId = parseRunHash(window.location.hash);
  if (runId && runId !== state.activeRunId) loadRun(runId).catch(showError);
});

init().catch(showError);
