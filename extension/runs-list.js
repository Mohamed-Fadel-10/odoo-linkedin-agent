import { apiFetch, clearRuns, deleteRun } from "./api.js";
import { confirmDialog, paragraph, showToast, textNode } from "./dialogs-toasts.js";
import { state } from "./state.js";
import {
  clearChildren,
  el,
  formatPhase,
  relativeTime,
  statusClassForPhase,
  svgIcon,
} from "./utils.js";

const RUNS_PAGE_SIZE = 20;

/**
 * @param {{
 *   listEl: HTMLElement,
 *   emptyEl: HTMLElement,
 *   showMoreBtn: HTMLButtonElement,
 *   clearBtn: HTMLButtonElement,
 *   onSelect: (runId: string) => Promise<void>,
 *   onDeleted: (runId: string | null) => Promise<void>,
 * }} ctx
 */
export function createRunsList(ctx) {
  async function refresh({ append = false } = {}) {
    if (!append) state.runsOffset = 0;
    const body = await apiFetch(
      `/runs?limit=${RUNS_PAGE_SIZE}&offset=${state.runsOffset}`,
      { method: "GET", timeoutMs: 10000 }
    );
    const runs = body.runs || [];
    if (!append) state.loadedRuns = [];
    state.loadedRuns = state.loadedRuns.concat(runs);
    state.runsOffset += runs.length;
    state.hasMoreRuns = runs.length >= RUNS_PAGE_SIZE;
    render();
  }

  function render() {
    clearChildren(ctx.listEl);
    ctx.emptyEl.classList.toggle("hidden", state.loadedRuns.length > 0);
    ctx.showMoreBtn.classList.toggle("hidden", !state.hasMoreRuns);
    ctx.clearBtn.disabled = state.loadedRuns.length === 0;

    const grouped = new Map();
    for (const run of state.loadedRuns) {
      const key = run.company || "(company)";
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(run);
    }

    for (const [company, items] of grouped.entries()) {
      const group = el(ctx.listEl, "div", { className: "company-group" });
      el(group, "p", { className: "company-group-title", text: company });
      for (const run of items) renderRunItem(group, run);
    }
  }

  function countLabel(run) {
    const created = run.import_counts && run.import_counts.Created;
    const accepted =
      (run.counts && (run.counts.accepted ?? run.counts.Accepted)) ??
      (run.import_counts && run.import_counts["Not attempted"]);
    if (run.phase === "imported" && typeof created === "number") {
      return `${created} created`;
    }
    if (typeof accepted === "number") return `${accepted} accepted`;
    return "";
  }

  function renderRunItem(group, run) {
    const row = el(group, "div", {
      className: `run-item${run.run_id === state.activeRunId ? " active" : ""}`,
    });
    const btn = el(row, "button", {
      className: "run-item-main",
      attrs: { type: "button" },
    });
    const titleRow = el(btn, "div", { className: "title-row" });
    el(titleRow, "span", {
      className: `status-dot-inline ${statusClassForPhase(run.phase)}`,
      attrs: { "aria-hidden": "true" },
    });
    const titleBlock = el(titleRow, "div", { className: "title-block" });
    el(titleBlock, "div", {
      className: "title",
      text: run.domain || run.company || "Run",
    });
    el(titleRow, "span", {
      className: `badge ${statusClassForPhase(run.phase)}`,
      text: formatPhase(run.phase),
    });
    const when = relativeTime(run.created_at);
    const count = countLabel(run);
    el(btn, "div", {
      className: "meta",
      text: count ? `${when} · ${count}` : when,
      title: run.created_at ? new Date(run.created_at).toLocaleString() : "",
    });
    btn.addEventListener("click", () => {
      ctx.onSelect(run.run_id).catch(() => {});
    });

    const del = el(row, "button", {
      className: "icon-btn run-delete",
      attrs: {
        type: "button",
        "aria-label": `Delete run for ${run.company || "company"}`,
      },
      title: "Delete this run",
    });
    del.appendChild(svgIcon("trash"));
    const blocked = Boolean(run.discovery_in_progress);
    if (blocked) {
      del.disabled = true;
      del.title = "Cannot delete while discovery is in progress";
    }
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      if (blocked) return;
      confirmDeleteRun(run).catch(() => {});
    });
  }

  async function confirmDeleteRun(run) {
    const company = run.company || "this company";
    const imported = run.phase === "imported";
    const bodyNodes = [
      paragraph(
        textNode(`This removes the stored results for ${company}. `),
        textNode("Contacts already created in Odoo are not deleted.")
      ),
    ];
    if (imported) {
      bodyNodes.push(
        paragraph(
          textNode("Contacts already created in Odoo are not deleted.", {
            bold: true,
          })
        )
      );
    }
    const ok = await confirmDialog({
      title: "Delete this run?",
      bodyNodes,
      confirmLabel: "Delete run",
      danger: true,
    });
    if (!ok) return;
    await deleteRun(run.run_id);
    showToast(`Deleted results for ${company}`);
    await ctx.onDeleted(run.run_id);
  }

  async function confirmClearHistory() {
    const ok = await confirmDialog({
      title: "Clear history?",
      bodyNodes: [
        paragraph(
          textNode(
            "This removes the stored results for all finished runs. Contacts already created in Odoo are not deleted."
          )
        ),
      ],
      confirmLabel: "Clear history",
      danger: true,
    });
    if (!ok) return;
    const result = await clearRuns();
    showToast(`Cleared ${result.deleted || 0} run(s)`);
    await ctx.onDeleted(null);
  }

  ctx.clearBtn.addEventListener("click", () => {
    confirmClearHistory().catch((err) => {
      showToast(err.message || "Clear failed", { tone: "error" });
    });
  });
  ctx.showMoreBtn.addEventListener("click", () => {
    refresh({ append: true }).catch((err) => {
      showToast(err.message || "Load failed", { tone: "error" });
    });
  });

  return { refresh, render };
}
