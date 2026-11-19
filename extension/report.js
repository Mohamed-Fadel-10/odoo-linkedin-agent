import {
  buildCsv,
  clearChildren,
  copyText,
  downloadTextFile,
  el,
  formatElapsed,
  relativeTime,
  shortId,
  statusClassForRecord,
  svgIcon,
} from "./utils.js";
import { showToast } from "./dialogs-toasts.js";

const CARD_KEYS = [
  ["Created", "Created"],
  ["Duplicate", "Duplicate"],
  ["Possible Duplicate", "Possible Duplicate"],
  ["Skipped", "Skipped"],
  ["Rejected", "Rejected"],
  ["Failed", "Failed"],
  ["Not attempted", "Not attempted"],
];

/**
 * @param {{
 *   summaryEl: HTMLElement,
 *   bodyEl: HTMLElement,
 *   metaEl: HTMLElement,
 *   barEl: HTMLElement,
 *   btnCsv: HTMLButtonElement,
 *   btnCopySummary: HTMLButtonElement,
 * }} els
 */
export function createReportView(els) {
  /** @type {Record<string, any> | null} */
  let current = null;

  function render(run) {
    current = run;
    clearChildren(els.summaryEl);
    clearChildren(els.bodyEl);
    clearChildren(els.metaEl);
    clearChildren(els.barEl);
    if (!run) return;

    renderMeta(run);
    const counts = mergeCounts(run);
    renderCards(counts);
    renderBar(counts);
    renderTable(run);
  }

  function mergeCounts(run) {
    const processing = run.processing_counts || {};
    const imports = run.import_counts || {};
    const imported = run.phase === "imported";
    return {
      Created: imported ? imports.Created || 0 : 0,
      Duplicate: imported
        ? imports.Duplicate || 0
        : processing.Duplicate || 0,
      "Possible Duplicate": imported
        ? imports["Possible Duplicate"] || 0
        : processing["Possible Duplicate"] || 0,
      Skipped: imported
        ? imports.Skipped || 0
        : processing.Skipped || 0,
      Rejected: processing.Rejected || 0,
      Failed: imported ? imports.Failed || 0 : 0,
      "Not attempted": imported
        ? imports["Not attempted"] || 0
        : processing.Accepted || 0,
    };
  }

  function renderMeta(run) {
    el(els.metaEl, "div", {
      className: "meta-item",
      text: `${run.company || "—"} · ${run.domain || "—"}`,
    });
    el(els.metaEl, "div", {
      className: "meta-item muted",
      text: run.created_at
        ? `${relativeTime(run.created_at)} · ${new Date(run.created_at).toLocaleString()}`
        : "",
    });
    el(els.metaEl, "div", {
      className: "meta-item muted",
      text: `Duration ${formatElapsed(run.duration_ms)}`,
    });
    const idRow = el(els.metaEl, "div", { className: "meta-item id-row" });
    el(idRow, "span", { className: "mono", text: shortId(run.run_id) });
    const copyBtn = el(idRow, "button", {
      className: "icon-btn",
      attrs: { type: "button", "aria-label": "Copy run id" },
      title: "Copy run id",
    });
    copyBtn.appendChild(svgIcon("copy"));
    copyBtn.addEventListener("click", async () => {
      const ok = await copyText(run.run_id);
      showToast(ok ? "Run id copied" : "Copy failed", {
        tone: ok ? "success" : "error",
      });
    });
  }

  function renderCards(counts) {
    for (const [label] of CARD_KEYS) {
      const card = el(els.summaryEl, "div", { className: "stat-card" });
      el(card, "div", { className: "stat-value", text: String(counts[label] || 0) });
      el(card, "div", { className: "stat-label", text: label });
    }
  }

  function renderBar(counts) {
    const total = CARD_KEYS.reduce((sum, [k]) => sum + (counts[k] || 0), 0) || 1;
    const colors = {
      Created: "var(--ok)",
      Duplicate: "var(--warn)",
      "Possible Duplicate": "#c2410c",
      Skipped: "var(--idle)",
      Rejected: "var(--danger)",
      Failed: "#7f1d1d",
      "Not attempted": "var(--primary)",
    };
    for (const [label] of CARD_KEYS) {
      const value = counts[label] || 0;
      if (!value) continue;
      const seg = el(els.barEl, "div", {
        className: "bar-seg",
        title: `${label}: ${value}`,
        attrs: {
          style: `width:${(value / total) * 100}%;background:${colors[label]}`,
        },
      });
      void seg;
    }
  }

  function renderTable(run) {
    const table = el(els.bodyEl, "table", { className: "report-table" });
    const thead = el(table, "thead");
    const headRow = el(thead, "tr");
    for (const label of [
      "Name",
      "Email",
      "Phone",
      "LinkedIn",
      "Status",
      "Outcome / reason",
      "Odoo id",
    ]) {
      el(headRow, "th", { text: label });
    }
    const tbody = el(table, "tbody");
    for (const record of run.records || []) {
      const tr = el(tbody, "tr");
      el(tr, "td", { text: record.name || "" });
      el(tr, "td", { text: record.email || "" });
      el(tr, "td", { text: record.phone || "—" });
      el(tr, "td", { text: record.linkedin_url || "—" });
      const statusTd = el(tr, "td");
      el(statusTd, "span", {
        className: `badge ${statusClassForRecord(record.status)}`,
        text: record.status || "",
      });
      const outcome =
        record.import_status && record.import_status !== "not_attempted"
          ? record.import_status
          : record.reason || "—";
      el(tr, "td", {
        className: record.import_status === "failed" ? "record-reason" : "",
        text: outcome,
      });
      el(tr, "td", {
        className: "mono",
        text: record.partner_id != null ? String(record.partner_id) : "—",
      });
    }
  }

  function downloadCsv() {
    if (!current) return;
    const headers = [
      "name",
      "email",
      "phone",
      "linkedin_url",
      "status",
      "outcome_or_reason",
      "partner_id",
    ];
    const rows = (current.records || []).map((r) => [
      r.name,
      r.email,
      r.phone || "",
      r.linkedin_url || "",
      r.status,
      r.import_status && r.import_status !== "not_attempted"
        ? r.import_status
        : r.reason || "",
      r.partner_id != null ? r.partner_id : "",
    ]);
    const csv = buildCsv(headers, rows);
    const company = String(current.company || "run")
      .replace(/[^\w.-]+/g, "_")
      .slice(0, 40);
    const date = current.created_at
      ? new Date(current.created_at).toISOString().slice(0, 10)
      : "undated";
    downloadTextFile(`${company}_${date}.csv`, csv);
    showToast("CSV downloaded");
  }

  async function copySummary() {
    if (!current) return;
    const counts = mergeCounts(current);
    const lines = [
      `${current.company} (${current.domain})`,
      `Run ${current.run_id}`,
      `Phase ${current.phase}`,
      ...CARD_KEYS.map(([label]) => `${label}: ${counts[label] || 0}`),
    ];
    const ok = await copyText(lines.join("\n"));
    showToast(ok ? "Summary copied" : "Copy failed", {
      tone: ok ? "success" : "error",
    });
  }

  els.btnCsv.addEventListener("click", downloadCsv);
  els.btnCopySummary.addEventListener("click", () => {
    copySummary().catch(() => {});
  });

  return { render };
}
