export const BACKEND_HOST = "127.0.0.1:8765";
export const BACKEND_START_CMD = ".\\.venv\\Scripts\\python.exe -m extension_backend";
export const DISCOVER_TIMEOUT_LABEL = "2 minutes";
export const DISCOVER_TIMEOUT_MS = 120000;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function el(parent, tag, opts = {}) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text != null) node.textContent = String(opts.text);
  if (opts.title) node.title = opts.title;
  if (opts.attrs) {
    for (const [key, value] of Object.entries(opts.attrs)) {
      if (value != null) node.setAttribute(key, String(value));
    }
  }
  parent.appendChild(node);
  return node;
}

export function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function isValidDomain(raw) {
  const value = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0];
  if (!value || value.length > 253) return false;
  return /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/.test(value);
}

export function isValidUuid(value) {
  return UUID_RE.test(String(value || "").trim());
}

export function safeLinkedInProfileUrl(raw) {
  if (!raw) return null;
  let parsed;
  try {
    parsed = new URL(String(raw).trim());
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (parsed.hostname.toLowerCase() !== "www.linkedin.com") return null;
  const match = parsed.pathname.match(/^\/in\/([A-Za-z0-9_-]+)\/?$/);
  if (!match) return null;
  return {
    href: `https://www.linkedin.com/in/${match[1]}`,
    label: `linkedin.com/in/${match[1]}`,
  };
}

export function appendLinkedInLink(parent, rawUrl) {
  const safe = safeLinkedInProfileUrl(rawUrl);
  if (!safe) return null;
  return el(parent, "a", {
    className: "linkedin-link",
    text: `${safe.label} ↗`,
    attrs: { href: safe.href, target: "_blank", rel: "noopener noreferrer" },
  });
}

export function formatPhase(phase) {
  const map = {
    in_progress: "In progress",
    preview_ready: "Preview ready",
    imported: "Imported",
    failed: "Failed",
  };
  return map[phase] || "Idle";
}

export function statusClassForPhase(phase) {
  return String(phase || "idle").replace(/\s+/g, "_").toLowerCase();
}

export function statusClassForRecord(status) {
  const raw = String(status || "").toLowerCase();
  if (raw === "accepted") return "accepted";
  if (raw === "duplicate") return "duplicate";
  if (raw === "possible duplicate" || raw === "possible_duplicate") return "possible";
  if (raw === "skipped" || raw === "rejected" || raw === "failed" || raw === "created") {
    return raw === "created" ? "accepted" : raw;
  }
  return "idle";
}

export function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return String(iso);
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatElapsed(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return "—";
  const total = Math.max(0, Math.floor(Number(ms) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function shortId(id) {
  const value = String(id || "");
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

export async function copyText(text) {
  const value = String(text || "");
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function parseRunHash(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const params = new URLSearchParams(raw.includes("=") ? raw : "");
  const run = params.get("run") || (raw.startsWith("run=") ? raw.slice(4) : "");
  return isValidUuid(run) ? run.trim() : null;
}

export function buildRunHash(runId) {
  return isValidUuid(runId) ? `#run=${runId}` : "";
}

export function csvEscape(value) {
  let text = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  if (/[",\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function buildCsv(headers, rows) {
  const lines = [headers.map(csvEscape).join(",")];
  for (const row of rows) {
    lines.push(row.map(csvEscape).join(","));
  }
  return `${lines.join("\r\n")}\r\n`;
}

export function downloadTextFile(filename, content, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function setStatusBar(root, opts) {
  if (!root) return;
  const state = opts.state || "idle";
  root.dataset.state = state;
  root.setAttribute("aria-live", "polite");
  clearChildren(root);
  const indicator = el(root, "div", { className: "status-indicator" });
  if (state === "working") {
    el(indicator, "span", { className: "spinner", attrs: { "aria-hidden": "true" } });
  } else {
    el(indicator, "span", { className: "status-dot", attrs: { "aria-hidden": "true" } });
  }
  const body = el(root, "div", { className: "status-body" });
  el(body, "p", { className: "status-title", text: opts.title || "" });
  if (opts.detail) el(body, "p", { className: "status-detail", text: opts.detail });
  if (opts.actions && opts.actions.length) {
    const actions = el(body, "div", { className: "status-actions" });
    for (const action of opts.actions) {
      const btn = el(actions, "button", {
        className: action.secondary ? "secondary" : "",
        text: action.label,
        attrs: { type: "button" },
      });
      btn.addEventListener("click", action.onClick);
    }
  }
}

export function setConnectionPill(root, connected) {
  if (!root) return;
  root.className = `conn-pill ${connected ? "ok" : "bad"}`;
  clearChildren(root);
  el(root, "span", { className: "dot", attrs: { "aria-hidden": "true" } });
  el(root, "span", { text: connected ? "Backend connected" : "Backend unreachable" });
}

export function setButtonBusy(button, opts) {
  if (!button) return;
  button.disabled = Boolean(opts.busy);
  clearChildren(button);
  if (opts.busy) {
    el(button, "span", { className: "spinner", attrs: { "aria-hidden": "true" } });
    el(button, "span", { text: opts.busyLabel });
  } else {
    el(button, "span", { text: opts.idleLabel });
  }
}

export function createElapsedTracker(onTick) {
  let startedAt = 0;
  let timer = null;
  return {
    start() {
      startedAt = Date.now();
      if (timer) clearInterval(timer);
      onTick(0);
      timer = setInterval(() => onTick(Date.now() - startedAt), 1000);
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
      return Date.now() - startedAt;
    },
    elapsed() {
      return startedAt ? Date.now() - startedAt : 0;
    },
  };
}

export function describeError(err, handlers = {}) {
  const message = (err && err.message) || String(err || "Something went wrong");
  const status = err && err.status;
  const code = err && err.code;
  if (code === "BACKEND_UNAVAILABLE" || message === "BACKEND_UNAVAILABLE") {
    return {
      state: "error",
      title: `Can't reach the local backend on ${BACKEND_HOST}`,
      detail: `Start it with: ${BACKEND_START_CMD}`,
      actions: [
        handlers.onCopyCommand
          ? { label: "Copy start command", onClick: handlers.onCopyCommand, secondary: true }
          : null,
        handlers.onRetry ? { label: "Retry", onClick: handlers.onRetry } : null,
      ].filter(Boolean),
    };
  }
  if (code === "PAIRING_MISSING") {
    return {
      state: "warning",
      title: "Pairing token not set",
      detail: "Open Options and paste EXTENSION_PAIRING_TOKEN from your project .env file.",
      actions: handlers.onOptions
        ? [{ label: "Open Options", onClick: handlers.onOptions }]
        : [],
    };
  }
  if (status === 401 || status === 403 || code === "UNAUTHORIZED") {
    return {
      state: "error",
      title: "Not authorized",
      detail: "Check the pairing token in Options and EXTENSION_ID in .env.",
      actions: [
        handlers.onOptions
          ? { label: "Open Options", onClick: handlers.onOptions, secondary: true }
          : null,
        handlers.onRetry ? { label: "Retry", onClick: handlers.onRetry } : null,
      ].filter(Boolean),
    };
  }
  if (status === 409) {
    return {
      state: "warning",
      title: "Action not allowed right now",
      detail: message,
      actions: [],
    };
  }
  if (code === "TIMEOUT" || /timed out/i.test(message)) {
    return {
      state: "error",
      title: `Discovery timed out after ${DISCOVER_TIMEOUT_LABEL}`,
      detail: "Nothing was imported. You can retry when ready.",
      actions: handlers.onRetry ? [{ label: "Retry", onClick: handlers.onRetry }] : [],
    };
  }
  return {
    state: "error",
    title: "Something went wrong",
    detail: message,
    actions: handlers.onRetry ? [{ label: "Retry", onClick: handlers.onRetry }] : [],
  };
}

export function svgIcon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("fill", "currentColor");
  const d = {
    trash:
      "M6 2h4l1 1h3v1H2V3h3l1-1zm1 4v6H6V6h1zm3 0v6H9V6h1zM3 5h10l-1 9H4L3 5z",
    copy: "M5 2h7a1 1 0 011 1v8h-1V3H5V2zm-2 2h7a1 1 0 011 1v9a1 1 0 01-1 1H3a1 1 0 01-1-1V5a1 1 0 011-1zm0 1v9h7V5H3z",
    download:
      "M8 2v7.3L10.6 6.7l.7.7L8 11.1 4.7 7.4l.7-.7L7 9.3V2h1zM3 12h10v1H3v-1z",
    chevron: "M4.5 6l3.5 3.5L11.5 6l.7.7-4.2 4.2L3.8 6.7z",
  }[name];
  if (d) path.setAttribute("d", d);
  svg.appendChild(path);
  return svg;
}
