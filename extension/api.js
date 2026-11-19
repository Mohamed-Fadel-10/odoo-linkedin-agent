import { DISCOVER_TIMEOUT_MS } from "./utils.js";

const BACKEND = "http://127.0.0.1:8765";
const PAIRING_HEADER = "X-Extension-Pairing-Token";

export async function getPairingToken() {
  const stored = await chrome.storage.local.get(["pairingToken"]);
  return (stored.pairingToken || "").trim();
}

/**
 * @param {string} path
 * @param {{ method?: string, body?: string, timeoutMs?: number, headers?: Record<string, string> }} [options]
 */
export async function apiFetch(path, options = {}) {
  const token = await getPairingToken();
  if (!token) {
    const err = new Error(
      "Pairing token missing. Open extension Options and paste EXTENSION_PAIRING_TOKEN from .env."
    );
    err.code = "PAIRING_MISSING";
    throw err;
  }
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || 8000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${BACKEND}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        [PAIRING_HEADER]: token,
        ...(options.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const msg = body && body.error ? body.error : `HTTP ${response.status}`;
      const err = new Error(msg);
      err.status = response.status;
      err.retryAfterSeconds =
        body && typeof body.retry_after_seconds === "number"
          ? body.retry_after_seconds
          : null;
      err.body = body;
      if (response.status === 401 || response.status === 403) err.code = "UNAUTHORIZED";
      else if (response.status === 429) err.code = "RATE_LIMITED";
      else if (response.status === 404) err.code = "NOT_FOUND";
      else if (response.status === 409) err.code = "CONFLICT";
      else err.code = "API_ERROR";
      throw err;
    }
    return body;
  } catch (err) {
    if (err && err.name === "AbortError") {
      const timeoutErr = new Error(
        path.startsWith("/discover")
          ? "Discovery timed out after 2 minutes. Nothing was imported."
          : "Request timed out talking to the local backend."
      );
      timeoutErr.code = "TIMEOUT";
      throw timeoutErr;
    }
    if (err && err.code) throw err;
    if (err && err.message && /Failed to fetch|NetworkError/i.test(err.message)) {
      const offline = new Error("BACKEND_UNAVAILABLE");
      offline.code = "BACKEND_UNAVAILABLE";
      throw offline;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function checkBackend() {
  await apiFetch("/health", { method: "GET", timeoutMs: 3000 });
}

export function parseLinkedInCompanyUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase();
  if (host !== "linkedin.com" && !host.endsWith(".linkedin.com")) return null;
  const match = parsed.pathname.match(/^\/company\/([A-Za-z0-9_-]+)(?:\/|$)/);
  if (!match) return null;
  const slug = match[1].toLowerCase();
  const suggestedCompany = slug
    .replace(/_/g, "-")
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
  return {
    canonicalUrl: `https://${host}/company/${slug}`,
    slug,
    suggestedCompany: suggestedCompany || slug,
    suggestedDomain: `${slug}.com`,
  };
}

export function openDashboard(params = {}) {
  const url = new URL(chrome.runtime.getURL("dashboard.html"));
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value).length) {
      url.searchParams.set(key, String(value));
    }
  }
  return chrome.tabs.create({ url: url.toString() });
}

export async function deleteRun(runId) {
  return apiFetch(`/runs/${encodeURIComponent(runId)}`, {
    method: "DELETE",
    timeoutMs: 15000,
  });
}

export async function clearRuns() {
  return apiFetch("/runs", { method: "DELETE", timeoutMs: 30000 });
}

export { DISCOVER_TIMEOUT_MS };
