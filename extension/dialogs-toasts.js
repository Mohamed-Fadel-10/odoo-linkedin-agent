import { clearChildren, el } from "./utils.js";

let toastRoot = null;
let toastTimer = null;

export function ensureToastRoot() {
  if (toastRoot) return toastRoot;
  toastRoot = document.getElementById("toast-root");
  if (!toastRoot) {
    toastRoot = el(document.body, "div", {
      className: "toast-root",
      attrs: { id: "toast-root", "aria-live": "polite", role: "status" },
    });
  }
  return toastRoot;
}

export function showToast(message, { tone = "success" } = {}) {
  const root = ensureToastRoot();
  clearChildren(root);
  const toast = el(root, "div", {
    className: `toast toast-${tone}`,
    text: message,
  });
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.remove();
  }, 3200);
}

/**
 * Confirm via native <dialog>. Returns true if confirmed.
 * @param {{ title: string, bodyNodes: Node[], confirmLabel?: string, danger?: boolean }} opts
 */
export function confirmDialog(opts) {
  return new Promise((resolve) => {
    let dialog = document.getElementById("confirm-dialog");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "confirm-dialog";
      dialog.className = "confirm-dialog";
      document.body.appendChild(dialog);
    }
    clearChildren(dialog);
    el(dialog, "h3", { text: opts.title || "Confirm" });
    const body = el(dialog, "div", { className: "dialog-body" });
    for (const node of opts.bodyNodes || []) body.appendChild(node);
    const actions = el(dialog, "div", { className: "dialog-actions" });
    const cancel = el(actions, "button", {
      className: "secondary",
      text: "Cancel",
      attrs: { type: "button", value: "cancel" },
    });
    const ok = el(actions, "button", {
      className: opts.danger ? "danger" : "",
      text: opts.confirmLabel || "Delete",
      attrs: { type: "button", value: "confirm" },
    });

    const finish = (value) => {
      dialog.close();
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(false));
    ok.addEventListener("click", () => finish(true));
    dialog.addEventListener(
      "cancel",
      (event) => {
        event.preventDefault();
        finish(false);
      },
      { once: true }
    );
    dialog.showModal();
    ok.focus();
  });
}

export function textNode(text, { bold = false } = {}) {
  if (bold) {
    const strong = document.createElement("strong");
    strong.textContent = text;
    return strong;
  }
  return document.createTextNode(text);
}

export function paragraph(...parts) {
  const p = document.createElement("p");
  for (const part of parts) {
    if (typeof part === "string") p.appendChild(document.createTextNode(part));
    else if (part) p.appendChild(part);
  }
  return p;
}
