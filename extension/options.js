import { setStatusBar } from "./utils.js";

const input = document.getElementById("pairing-token");
const saveBtn = document.getElementById("save");
const statusBar = document.getElementById("status-bar");

async function load() {
  const stored = await chrome.storage.local.get(["pairingToken"]);
  input.value = stored.pairingToken || "";
  setStatusBar(statusBar, {
    state: stored.pairingToken ? "success" : "warning",
    title: stored.pairingToken ? "Token saved on this browser" : "Token not set",
    detail: stored.pairingToken
      ? "The extension can authorize requests to the local backend."
      : "Paste EXTENSION_PAIRING_TOKEN from .env, then Save.",
  });
}

saveBtn.addEventListener("click", async () => {
  const pairingToken = input.value.trim();
  await chrome.storage.local.set({ pairingToken });
  setStatusBar(statusBar, {
    state: pairingToken ? "success" : "warning",
    title: pairingToken ? "Saved" : "Token cleared",
    detail: pairingToken
      ? "Reload the extension if the backend still rejects requests."
      : "Requests will fail until a pairing token is set.",
  });
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    saveBtn.click();
  }
});

load();
