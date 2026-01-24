const statusEl = document.getElementById("status");
const statusLineEl = document.getElementById("status-line");
const statusProgressEl = document.getElementById("status-progress");
const statusJobEl = document.getElementById("status-job");
const statusErrorEl = document.getElementById("status-error");
const serverEl = document.getElementById("cfg-server");
const nameEl = document.getElementById("cfg-name");
const idEl = document.getElementById("cfg-id");
const cacheEl = document.getElementById("cfg-cache");
const handbrakeEl = document.getElementById("cfg-handbrake");
const hoursEl = document.getElementById("cfg-hours");
const pollEl = document.getElementById("cfg-poll");
const saveBtn = document.getElementById("save-config");
const saveStatusEl = document.getElementById("save-status");
const alertsEl = document.getElementById("alerts");

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    let message = text || `Request failed: ${res.status}`;
    try {
      const parsed = JSON.parse(text);
      if (parsed && parsed.detail) {
        message = parsed.detail;
      }
    } catch (_) {
      // noop
    }
    throw new Error(message);
  }
  return res.json();
}

function setStatus(text) {
  statusEl.textContent = text;
}

async function loadConfig() {
  const config = await fetchJson("/api/config");
  serverEl.value = config.serverUrl || "";
  nameEl.value = config.name || "";
  idEl.value = config.workerId || "";
  cacheEl.value = config.cacheDir || "";
  handbrakeEl.value = config.handbrakePath || "";
  hoursEl.value = JSON.stringify(config.workHours || [], null, 2);
  pollEl.value = config.pollIntervalSec || 10;
}

async function saveConfig() {
  saveStatusEl.textContent = "Saving...";
  try {
    const workHours = JSON.parse(hoursEl.value || "[]");
    await fetchJson("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serverUrl: serverEl.value,
        name: nameEl.value,
        cacheDir: cacheEl.value,
        handbrakePath: handbrakeEl.value,
        workHours,
        pollIntervalSec: parseInt(pollEl.value || "10", 10),
      }),
    });
    saveStatusEl.textContent = "Saved (restart worker to apply)";
  } catch (err) {
    saveStatusEl.textContent = err.message || "Error";
  }
}

async function loadStatus() {
  try {
    const status = await fetchJson("/api/status");
    statusLineEl.textContent = `State: ${status.state}`;
    if (status.progressPct !== null && status.progressPct !== undefined) {
      statusProgressEl.textContent = `Progress: ${status.progressPct}% ${status.progressMessage ? `(${status.progressMessage})` : ""}`;
    } else if (status.progressMessage) {
      statusProgressEl.textContent = `Progress: ${status.progressMessage}`;
    } else {
      statusProgressEl.textContent = "";
    }
    statusJobEl.textContent = status.jobId ? `Job: ${status.jobId}` : "";
    statusErrorEl.textContent = status.lastError ? `Last error: ${status.lastError}` : "";
    setStatus(status.state === "working" ? "Working" : "Idle");
  } catch (err) {
    setStatus("Offline");
  }
}

async function loadDiagnostics() {
  if (!alertsEl) return;
  try {
    const diag = await fetchJson("/api/diagnostics");
    const warnings = [];
    if (!diag.handbrake || !diag.handbrake.found) {
      warnings.push("HandBrakeCLI not found. Set HandBrake path in config or HANDBRAKECLI_PATH.");
    }
    alertsEl.innerHTML = warnings.map((msg) => `<div class="alert warn">${msg}</div>`).join("");
  } catch (_) {
    alertsEl.innerHTML = "";
  }
}

saveBtn.addEventListener("click", saveConfig);

async function init() {
  await loadConfig();
  await loadStatus();
  await loadDiagnostics();
}

init();
setInterval(loadStatus, 2000);
setInterval(loadDiagnostics, 5000);
