import {
  checkBackend,
  getPairingToken,
  openDashboard,
  parseLinkedInCompanyUrl,
} from "./api.js";
import {
  BACKEND_START_CMD,
  copyText,
  describeError,
  isValidDomain,
  setButtonBusy,
  setConnectionPill,
  setStatusBar,
} from "./utils.js";

const els = {
  pageKind: document.getElementById("page-kind"),
  connPill: document.getElementById("conn-pill"),
  statusBar: document.getElementById("status-bar"),
  unsupported: document.getElementById("panel-unsupported"),
  offline: document.getElementById("panel-offline"),
  token: document.getElementById("panel-token"),
  form: document.getElementById("panel-form"),
  company: document.getElementById("company"),
  domain: document.getElementById("domain"),
  domainError: document.getElementById("domain-error"),
  discoverForm: document.getElementById("discover-form"),
  discover: document.getElementById("btn-discover"),
  openDashboard: document.getElementById("btn-open-dashboard"),
  openDashboardForm: document.getElementById("btn-open-dashboard-form"),
  openDashboardToken: document.getElementById("btn-open-dashboard-token"),
  openOptionsOffline: document.getElementById("btn-open-options-offline"),
  openOptionsToken: document.getElementById("btn-open-options-token"),
  retryHealth: document.getElementById("btn-retry-health"),
  copyCmd: document.getElementById("btn-copy-cmd"),
  offlineCmd: document.getElementById("offline-cmd"),
};

/** @type {{ url: string, slug: string, company: string, domain: string } | null} */
let pageContext = null;
let launching = false;

function showOnly(panel) {
  for (const key of ["unsupported", "offline", "token", "form"]) {
    els[key].classList.toggle("hidden", els[key] !== panel);
  }
}

function openOptions() {
  chrome.runtime.openOptionsPage();
}

function showStatusFromError(err) {
  setStatusBar(
    els.statusBar,
    describeError(err, {
      onRetry: () => init().catch(showStatusFromError),
      onOptions: openOptions,
      onCopyCommand: () => copyText(BACKEND_START_CMD),
    })
  );
}

function validateDomainField() {
  const value = els.domain.value.trim();
  const ok = isValidDomain(value);
  els.domain.classList.toggle("invalid", Boolean(value) && !ok);
  els.domainError.classList.toggle("hidden", ok || !value);
  els.domainError.textContent = ok || !value ? "" : "Enter a valid domain like example.com";
  const companyOk = Boolean(els.company.value.trim());
  els.discover.disabled = launching || !ok || !companyOk;
  return ok && companyOk;
}

function currentFormContext() {
  if (!pageContext) return null;
  const company = els.company.value.trim();
  const domain = els.domain.value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0];
  return {
    ...pageContext,
    company: company || pageContext.company,
    domain: domain || pageContext.domain,
  };
}

async function launchDashboard({ autodiscover = false } = {}) {
  if (launching) return;
  const ctx = currentFormContext();
  if (!ctx) {
    await openDashboard();
    window.close();
    return;
  }
  if (!ctx.company || !isValidDomain(ctx.domain)) {
    validateDomainField();
    setStatusBar(els.statusBar, {
      state: "warning",
      title: "Check company and domain",
      detail: "Company is required and domain must look like example.com.",
    });
    return;
  }
  launching = true;
  setButtonBusy(els.discover, {
    busy: true,
    busyLabel: "Opening…",
    idleLabel: "Discover employees",
  });
  try {
    await chrome.storage.local.set({ [`domain:${ctx.slug}`]: ctx.domain });
    await openDashboard({
      url: ctx.url,
      slug: ctx.slug,
      company: ctx.company,
      domain: ctx.domain,
      autodiscover: autodiscover ? "1" : "",
    });
    window.close();
  } catch (err) {
    launching = false;
    setButtonBusy(els.discover, {
      busy: false,
      busyLabel: "Opening…",
      idleLabel: "Discover employees",
    });
    showStatusFromError(err);
  }
}

async function init() {
  els.offlineCmd.textContent = BACKEND_START_CMD;
  setStatusBar(els.statusBar, {
    state: "working",
    title: "Checking…",
    detail: "Reading the active tab and local backend health.",
  });

  const token = await getPairingToken();
  if (!token) {
    showOnly(els.token);
    els.pageKind.textContent = "Setup required";
    showStatusFromError(
      Object.assign(new Error("PAIRING_MISSING"), { code: "PAIRING_MISSING" })
    );
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const parsed = parseLinkedInCompanyUrl(tab?.url || "");

  try {
    await checkBackend();
    setConnectionPill(els.connPill, true);
  } catch (err) {
    setConnectionPill(els.connPill, false);
    showOnly(els.offline);
    showStatusFromError(err);
    return;
  }

  if (!parsed) {
    showOnly(els.unsupported);
    els.pageKind.textContent = "Not a company page";
    setStatusBar(els.statusBar, {
      state: "idle",
      title: "Ready",
      detail: "Open a LinkedIn company page, or open the dashboard.",
    });
    return;
  }

  const remembered = await chrome.storage.local.get([`domain:${parsed.slug}`]);
  pageContext = {
    url: parsed.canonicalUrl,
    slug: parsed.slug,
    company: parsed.suggestedCompany,
    domain: remembered[`domain:${parsed.slug}`] || parsed.suggestedDomain,
  };
  els.company.value = pageContext.company;
  els.domain.value = pageContext.domain;
  showOnly(els.form);
  els.pageKind.textContent = "LinkedIn company page";
  setStatusBar(els.statusBar, {
    state: "idle",
    title: "Ready to discover",
    detail: "Confirm the domain, then Discover employees.",
  });
  validateDomainField();
}

els.openDashboard.addEventListener("click", () => {
  openDashboard().then(() => window.close()).catch(showStatusFromError);
});
els.openDashboardToken.addEventListener("click", () => {
  openDashboard().then(() => window.close()).catch(showStatusFromError);
});
els.openDashboardForm.addEventListener("click", () => {
  launchDashboard({ autodiscover: false }).catch(showStatusFromError);
});
els.openOptionsOffline.addEventListener("click", openOptions);
els.openOptionsToken.addEventListener("click", openOptions);
els.retryHealth.addEventListener("click", () => init().catch(showStatusFromError));
els.copyCmd.addEventListener("click", async () => {
  const ok = await copyText(BACKEND_START_CMD);
  setStatusBar(els.statusBar, {
    state: ok ? "success" : "warning",
    title: ok ? "Command copied" : "Could not copy",
    detail: BACKEND_START_CMD,
  });
});
els.company.addEventListener("input", validateDomainField);
els.domain.addEventListener("input", validateDomainField);
els.discoverForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateDomainField()) return;
  launchDashboard({ autodiscover: true }).catch(showStatusFromError);
});

init().catch(showStatusFromError);
