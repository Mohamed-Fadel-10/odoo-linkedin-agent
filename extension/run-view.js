import { state } from "./state.js";
import {
  appendLinkedInLink,
  clearChildren,
  copyText,
  el,
  statusClassForRecord,
  svgIcon,
} from "./utils.js";
import { showToast } from "./dialogs-toasts.js";

/**
 * @param {{
 *   countsRow: HTMLElement,
 *   listEl: HTMLElement,
 *   emptyEl: HTMLElement,
 *   filterLabel: HTMLElement,
 *   selectionCount: HTMLElement,
 *   searchInput: HTMLInputElement,
 *   sortSelect: HTMLSelectElement,
 *   showingLabel: HTMLElement,
 *   btnSelectAll: HTMLButtonElement,
 *   btnDeselectAll: HTMLButtonElement,
 *   approveControls: HTMLElement,
 * }} els
 */
export function createRunView(els) {
  function filteredRecords() {
    const run = state.activeRun;
    if (!run) return [];
    let rows = run.records || [];
    if (state.statusFilter) {
      rows = rows.filter(
        (r) => String(r.status || "").toLowerCase() === state.statusFilter
      );
    }
    const q = state.searchQuery.trim().toLowerCase();
    if (q) {
      rows = rows.filter((r) => {
        const blob = `${r.name || ""} ${r.email || ""} ${r.title || ""}`.toLowerCase();
        return blob.includes(q);
      });
    }
    const dir = state.sortDir === "desc" ? -1 : 1;
    const key = state.sortKey === "status" ? "status" : "name";
    return [...rows].sort((a, b) => {
      const av = String(a[key] || "").toLowerCase();
      const bv = String(b[key] || "").toLowerCase();
      return av < bv ? -dir : av > bv ? dir : 0;
    });
  }

  function render() {
    const run = state.activeRun;
    if (!run) return;
    renderCountChips(run);
    const rows = filteredRecords();
    els.showingLabel.textContent = `Showing ${rows.length} of ${(run.records || []).length}`;
    els.filterLabel.textContent = state.statusFilter
      ? `Filter: ${state.statusFilter}`
      : "All statuses";
    clearChildren(els.listEl);
    els.emptyEl.classList.toggle("hidden", rows.length > 0);
    for (const record of rows) renderRow(record);
    updateSelectionCount(rows);
    const imported = run.phase === "imported";
    els.approveControls.classList.toggle("hidden", imported);
  }

  function renderCountChips(run) {
    clearChildren(els.countsRow);
    const counts = run.processing_counts || {};
    const entries = [
      ["Accepted", counts.Accepted],
      ["Duplicate", counts.Duplicate],
      ["Possible Duplicate", counts["Possible Duplicate"]],
      ["Skipped", counts.Skipped],
      ["Rejected", counts.Rejected],
    ];
    const all = el(els.countsRow, "button", {
      className: `chip${state.statusFilter ? "" : " active"}`,
      text: `All ${counts.Total || (run.records || []).length}`,
      attrs: { type: "button" },
    });
    all.addEventListener("click", () => {
      state.statusFilter = null;
      render();
    });
    for (const [label, value] of entries) {
      if (value == null) continue;
      const key = label.toLowerCase();
      const chip = el(els.countsRow, "button", {
        className: `chip ${statusClassForRecord(label)}${
          state.statusFilter === key ? " active" : ""
        }`,
        text: `${label} ${value}`,
        attrs: { type: "button" },
      });
      chip.addEventListener("click", () => {
        state.statusFilter = key;
        render();
      });
    }
  }

  function renderRow(record) {
    const expanded = state.expandedIds.has(record.record_id);
    const row = el(els.listEl, "div", {
      className: `record-row${record.selectable ? "" : " disabled"}`,
    });
    const checkbox = el(row, "input", {
      attrs: { type: "checkbox", "data-record-id": record.record_id },
    });
    checkbox.disabled =
      !record.selectable || state.activeRun?.phase === "imported" || state.importInFlight;
    checkbox.checked = Boolean(
      record.selectable && state.selectedIds.has(record.record_id)
    );
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedIds.add(record.record_id);
      else state.selectedIds.delete(record.record_id);
      updateSelectionCount(filteredRecords());
    });

    const main = el(row, "div", { className: "record-main" });
    el(main, "div", { className: "record-name", text: record.name || "(no name)" });
    if (record.title) el(main, "div", { className: "record-title", text: record.title });

    const contact = el(row, "div", { className: "record-main" });
    const emailRow = el(contact, "div", { className: "email-row" });
    el(emailRow, "div", {
      className: "record-email",
      text: record.email || "(no email)",
    });
    if (record.email) {
      const copyBtn = el(emailRow, "button", {
        className: "icon-btn",
        attrs: { type: "button", "aria-label": "Copy email" },
        title: "Copy email",
      });
      copyBtn.appendChild(svgIcon("copy"));
      copyBtn.addEventListener("click", async () => {
        const ok = await copyText(record.email);
        showToast(ok ? "Email copied" : "Copy failed", {
          tone: ok ? "success" : "error",
        });
      });
    }
    appendLinkedInLink(contact, record.linkedin_url);

    const statusCol = el(row, "div", { className: "record-status-col" });
    el(statusCol, "span", {
      className: `badge ${statusClassForRecord(record.status)}`,
      text: record.status || "Unknown",
    });
    if (record.reason && String(record.status || "").toLowerCase() !== "accepted") {
      el(statusCol, "div", {
        className: "record-reason",
        text: record.reason,
        title: record.reason,
      });
    }

    const expand = el(row, "button", {
      className: "icon-btn expand-btn",
      attrs: {
        type: "button",
        "aria-expanded": expanded ? "true" : "false",
        "aria-label": "Show details",
      },
    });
    expand.appendChild(svgIcon("chevron"));
    expand.addEventListener("click", () => {
      if (state.expandedIds.has(record.record_id)) {
        state.expandedIds.delete(record.record_id);
      } else {
        state.expandedIds.add(record.record_id);
      }
      render();
    });

    if (expanded) {
      const detail = el(els.listEl, "div", { className: "record-detail" });
      addDetail(detail, "Department", record.department);
      addDetail(detail, "Seniority", record.seniority);
      addDetail(detail, "Email confidence", record.email_confidence);
      addDetail(detail, "Verification", record.email_verification);
      addDetail(detail, "Twitter", record.twitter);
      addDetail(detail, "Sources", record.sources_count);
      if (record.import_status && record.import_status !== "not_attempted") {
        addDetail(detail, "Import", record.import_status);
      }
    }
  }

  function addDetail(parent, label, value) {
    if (value == null || value === "") return;
    el(parent, "div", {
      className: "detail-line",
      text: `${label}: ${value}`,
    });
  }

  function updateSelectionCount(rows) {
    const eligible = rows.filter((r) => r.selectable);
    const selected = eligible.filter((r) => state.selectedIds.has(r.record_id)).length;
    els.selectionCount.textContent = `Selected ${selected} of ${eligible.length} eligible`;
  }

  function selectAllFiltered() {
    for (const r of filteredRecords()) {
      if (r.selectable) state.selectedIds.add(r.record_id);
    }
    render();
  }

  function deselectAllFiltered() {
    for (const r of filteredRecords()) state.selectedIds.delete(r.record_id);
    render();
  }

  els.btnSelectAll.addEventListener("click", selectAllFiltered);
  els.btnDeselectAll.addEventListener("click", deselectAllFiltered);
  els.searchInput.addEventListener("input", () => {
    state.searchQuery = els.searchInput.value;
    render();
  });
  els.sortSelect.addEventListener("change", () => {
    const [key, dir] = String(els.sortSelect.value || "name:asc").split(":");
    state.sortKey = key;
    state.sortDir = dir || "asc";
    render();
  });

  return { render, filteredRecords };
}

export function renderStepper(root, phase) {
  clearChildren(root);
  const steps = [
    { id: "discover", label: "Discover" },
    { id: "review", label: "Review" },
    { id: "approve", label: "Approve" },
    { id: "imported", label: "Imported" },
  ];
  let active = 1;
  if (phase === "imported") active = 3;
  else if (phase === "preview_ready") active = 1;
  else if (phase === "in_progress") active = 0;
  else if (phase === "failed") active = 0;
  const list = el(root, "ol", {
    className: `stepper${phase === "failed" ? " failed" : ""}`,
    attrs: { "aria-label": "Workflow" },
  });
  steps.forEach((step, index) => {
    const li = el(list, "li", {
      className: `step${index <= active ? " done" : ""}${index === active ? " current" : ""}`,
      text: step.label,
    });
    if (phase === "failed" && index === 0) li.classList.add("failed-step");
  });
}
