const statusEl = document.getElementById("status");
const scanStatusEl = document.getElementById("scan-status");
const entryPathEl = document.getElementById("entry-path");
const entryNameEl = document.getElementById("entry-name");
const entryArgsEl = document.getElementById("entry-args");
const addEntryBtn = document.getElementById("add-entry");
const entriesEl = document.getElementById("entries");
const itemsEl = document.getElementById("items");
const configBaselineEl = document.getElementById("config-baseline");
const configFfprobeEl = document.getElementById("config-ffprobe");
const configBucketsEl = document.getElementById("config-buckets");
const saveConfigBtn = document.getElementById("save-config");
const configStatusEl = document.getElementById("config-status");
const clearTargetSamplesBtn = document.getElementById("clear-target-samples");
const configSamplesEl = document.getElementById("config-samples");
const sortSavingsBtn = document.getElementById("sort-savings");
const sortPercentBtn = document.getElementById("sort-percent");
const refreshItemsBtn = document.getElementById("refresh-items");
const workersEl = document.getElementById("workers");
const scanAllBtn = document.getElementById("scan-all");
const jobsEl = document.getElementById("jobs");
const refreshJobsBtn = document.getElementById("refresh-jobs");
const cancelAllBtn = document.getElementById("cancel-all");
const alertsEl = document.getElementById("alerts");

let currentSort = "savingsBytes";

// Request debouncing - prevent overlapping requests
const pendingRequests = {
  workers: false,
  jobs: false,
  items: false,
  scanStatus: false,
  diagnostics: false,
};

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

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(value >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function formatDuration(seconds) {
  if (!seconds) return "-";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function formatTimestamp(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return date.toLocaleString();
}

function formatEta(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function minutesSince(isoString) {
  if (!isoString) return null;
  const time = new Date(isoString).getTime();
  if (!time) return null;
  const diffMs = Date.now() - time;
  return Math.max(0, diffMs / 60000);
}

async function loadConfig() {
  const config = await fetchJson("/api/config");
  configBaselineEl.value = config.baselineArgs || "";
  configFfprobeEl.value = config.ffprobePath || "";
  configBucketsEl.value = JSON.stringify(config.targetMbPerMinByHeight || {}, null, 2);
  const samples = config.targetSamplesByHeight || {};
  const parts = Object.keys(samples)
    .sort((a, b) => parseInt(a, 10) - parseInt(b, 10))
    .map((key) => `${key}p(${(samples[key] || []).length})`);
  configSamplesEl.textContent = parts.length ? `Samples: ${parts.join(", ")}` : "Samples: none";
}

async function loadDiagnostics() {
  if (!alertsEl) return;
  if (pendingRequests.diagnostics) return;
  pendingRequests.diagnostics = true;
  try {
    const diag = await fetchJson("/api/diagnostics");
    const warnings = [];
    if (!diag.ffprobe || !diag.ffprobe.found) {
      warnings.push("FFprobe not found. Scans won't populate duration/ratio. Set FFprobe path in Config.");
    }
    alertsEl.innerHTML = warnings.map((msg) => `<div class="alert warn">${msg}</div>`).join("");
  } catch (_) {
    alertsEl.innerHTML = "";
  } finally {
    pendingRequests.diagnostics = false;
  }
}

async function loadScanStatus() {
  if (!scanStatusEl) return;
  if (pendingRequests.scanStatus) return;
  pendingRequests.scanStatus = true;
  try {
    const scan = await fetchJson("/api/scan-status");
    if (!scan || !scan.active) {
      scanStatusEl.textContent = "Scan: idle";
      return;
    }
    const total = scan.total || 0;
    const done = scan.done || 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const name = scan.entryName ? `${scan.entryName}: ` : "";
    scanStatusEl.textContent = `Scan ${name}${done}/${total} (${pct}%)`;
  } catch (_) {
    scanStatusEl.textContent = "Scan: -";
  } finally {
    pendingRequests.scanStatus = false;
  }
}

async function saveConfig() {
  configStatusEl.textContent = "Saving...";
  try {
    const buckets = JSON.parse(configBucketsEl.value || "{}") || {};
    await fetchJson("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        baselineArgs: configBaselineEl.value,
        ffprobePath: configFfprobeEl.value,
        targetMbPerMinByHeight: buckets,
      }),
    });
    configStatusEl.textContent = "Saved";
  } catch (err) {
    configStatusEl.textContent = err.message || "Error";
  }
}

async function addEntry() {
  setStatus("Adding entry...");
  try {
    await fetchJson("/api/entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: entryPathEl.value,
        name: entryNameEl.value || undefined,
        args: entryArgsEl.value || "",
      }),
    });
    entryPathEl.value = "";
    entryNameEl.value = "";
    entryArgsEl.value = "";
    await loadEntries();
    setStatus("Entry added");
  } catch (err) {
    setStatus(err.message || "Failed to add entry");
  }
}

async function deleteEntry(entryId) {
  await fetchJson(`/api/entries/${entryId}`, { method: "DELETE" });
}

function renderEntries(entries) {
  entriesEl.innerHTML = "";
  if (!entries.length) {
    entriesEl.innerHTML = "<div class=\"hint\">No entries yet.</div>";
    return;
  }

  entries.forEach((entry) => {
    const card = document.createElement("div");
    card.className = "entry";
    card.innerHTML = `
      <h4>${entry.name}</h4>
      <div class="path">${entry.path}</div>
      <div class="row">
        <div class="meta">Last scan: ${entry.lastScanAt || "-"}</div>
        <div class="row-actions">
          <button class="btn" data-scan="${entry.id}">Scan</button>
          <button class="btn danger" data-delete-entry="${entry.id}">✕</button>
        </div>
      </div>
    `;
    entriesEl.appendChild(card);
  });
}

async function loadEntries() {
  const entries = await fetchJson("/api/entries");
  renderEntries(entries || []);
}

function badgeFor(status) {
  const cls = status || "idle";
  return `<span class="badge ${cls}">${cls}</span>`;
}

function badgeForJob(job) {
  if (job.cancelRequested && (job.status === "running" || job.status === "claimed")) {
    return `<span class="badge cancelling">cancelling</span>`;
  }
  return badgeFor(job.status);
}

async function toggleReady(itemId, ready) {
  await fetchJson(`/api/items/${itemId}/ready`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ready }),
  });
}

async function resetItem(itemId) {
  await fetchJson(`/api/items/${itemId}/reset`, {
    method: "POST",
  });
}

async function deleteItem(itemId) {
  await fetchJson(`/api/items/${itemId}`, {
    method: "DELETE",
  });
}

async function loadItems() {
  if (pendingRequests.items) return;
  pendingRequests.items = true;
  try {
    const items = await fetchJson(`/api/items?sort=${currentSort}`);
    itemsEl.innerHTML = "";
    if (!items.length) {
      itemsEl.innerHTML = "<tr><td colspan=\"10\" class=\"hint\">No items scanned. Click Scan on an entry.</td></tr>";
      return;
    }

    items.forEach((item) => {
      const ratio = item.ratio || {};
      const row = document.createElement("tr");
      row.innerHTML = `
        <td data-label="Ready"><input type="checkbox" data-ready="${item.id}" ${item.ready ? "checked" : ""} /></td>
        <td data-label="Status">${badgeFor(item.status)}</td>
        <td data-label="Tagged">${item.encodedBySpacesaver ? `<span class="badge tag">MS</span>` : "-"}</td>
        <td data-label="Path" title="${item.path}">${item.path}</td>
        <td data-label="Size">${formatBytes(item.sizeBytes)}</td>
        <td data-label="Duration">${formatDuration(item.durationSec)}</td>
        <td data-label="Res">${item.width || 0}x${item.height || 0}</td>
        <td data-label="Savings">${formatBytes(ratio.savingsBytes || 0)}</td>
        <td data-label="%">${((ratio.savingsPct || 0) * 100).toFixed(1)}%</td>
        <td data-label="Actions">
          <div class="row-actions">
            <button class="btn ghost" data-reset="${item.id}">Reset</button>
            <button class="btn danger" data-delete="${item.id}">✕</button>
          </div>
        </td>
      `;
      itemsEl.appendChild(row);
    });
  } finally {
    pendingRequests.items = false;
  }
}

async function loadWorkers() {
  if (pendingRequests.workers) return;
  pendingRequests.workers = true;
  try {
    const workers = await fetchJson("/api/workers");
    workersEl.innerHTML = "";
    if (!workers.length) {
      workersEl.innerHTML = "<div class=\"hint\">No workers yet.</div>";
      return;
    }

    workers.forEach((worker) => {
      const mins = minutesSince(worker.lastHeartbeatAt);
      const online = mins !== null && mins <= 2;
      const withinHours = worker.withinWorkHours !== false;
      const age = mins === null ? "never" : `${mins.toFixed(1)}m ago`;

      // Determine status: online+inHours = "online", online+outOfHours = "idle", not online = "offline"
      let statusClass = "";
      let statusLabel = "offline";
      if (online) {
        if (withinHours) {
          statusClass = "online";
          statusLabel = "online";
        } else {
          statusClass = "idle";
          statusLabel = "idle (off-hours)";
        }
      }

      const card = document.createElement("div");
      card.className = "entry";
      card.innerHTML = `
        <h4>${worker.name}</h4>
        <div class="path">${worker.id}</div>
        <div class="row">
          <div class="worker-status"><span class="dot ${statusClass}"></span>${statusLabel} (${age})</div>
          <button class="btn danger" data-worker-delete="${worker.id}">✕</button>
        </div>
      `;
      workersEl.appendChild(card);
    });
  } finally {
    pendingRequests.workers = false;
  }
}

async function loadJobs() {
  if (pendingRequests.jobs) return;
  pendingRequests.jobs = true;
  try {
    const jobs = await fetchJson("/api/jobs");
    jobsEl.innerHTML = "";
    if (!jobs.length) {
      jobsEl.innerHTML = "<tr><td colspan=\"11\" class=\"hint\">No jobs yet.</td></tr>";
      return;
    }

    const sorted = jobs.slice().sort((a, b) => {
      const at = new Date(a.claimedAt || 0).getTime();
      const bt = new Date(b.claimedAt || 0).getTime();
      return bt - at;
    });

    sorted.forEach((job) => {
      const pct = job.progress && job.progress.pct !== undefined ? `${job.progress.pct}%` : "-";
      let msg = job.progress && job.progress.logTail ? job.progress.logTail : "-";
      const eta = job.progress && job.progress.etaSec !== undefined ? formatEta(job.progress.etaSec) : "-";
      if (job.cancelRequested) {
        msg = "Cancel requested";
      }
      const row = document.createElement("tr");
      row.innerHTML = `
        <td data-label="Status">${badgeForJob(job)}</td>
        <td data-label="Progress">${pct}</td>
        <td data-label="ETA">${eta}</td>
        <td data-label="Worker">${job.workerName || job.workerId || "-"}</td>
        <td data-label="Item" title="${job.itemPath || job.itemId || ""}">${job.itemPath || job.itemId || "-"}</td>
        <td data-label="Claimed">${formatTimestamp(job.claimedAt)}</td>
        <td data-label="Started">${formatTimestamp(job.startedAt)}</td>
        <td data-label="Finished">${formatTimestamp(job.finishedAt)}</td>
        <td data-label="Message" class="message-cell" title="${msg}">${msg}</td>
        <td data-label="Error" class="error-cell">${job.error || "-"}</td>
        <td data-label="Actions">
          <button class="btn danger" data-job-delete="${job.id}">✕</button>
        </td>
      `;
      jobsEl.appendChild(row);
    });
  } finally {
    pendingRequests.jobs = false;
  }
}

function setupTabs(container, storageKey) {
  if (!container) return;
  const buttons = Array.from(container.querySelectorAll("[data-tab]"));
  const panels = Array.from(container.querySelectorAll("[data-panel]"));
  if (!buttons.length || !panels.length) return;

  const activate = (name) => {
    const target = name || buttons[0].dataset.tab;
    buttons.forEach((btn) => {
      const active = btn.dataset.tab === target;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.setAttribute("tabindex", active ? "0" : "-1");
    });
    panels.forEach((panel) => {
      const active = panel.dataset.panel === target;
      panel.classList.toggle("active", active);
      panel.setAttribute("aria-hidden", active ? "false" : "true");
    });
    if (storageKey) {
      localStorage.setItem(storageKey, target);
    }
  };

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => activate(btn.dataset.tab));
  });

  const saved = storageKey ? localStorage.getItem(storageKey) : null;
  const initial = buttons.some((btn) => btn.dataset.tab === saved) ? saved : buttons[0].dataset.tab;
  activate(initial);
}

sortSavingsBtn.addEventListener("click", () => {
  currentSort = "savingsBytes";
  loadItems();
});

sortPercentBtn.addEventListener("click", () => {
  currentSort = "savingsPct";
  loadItems();
});

refreshItemsBtn.addEventListener("click", () => loadItems());
refreshJobsBtn.addEventListener("click", () => loadJobs());
cancelAllBtn.addEventListener("click", async () => {
  setStatus("Cancelling jobs...");
  try {
    const result = await fetchJson("/api/jobs/cancel-all", { method: "POST" });
    await loadJobs();
    setStatus(result.cancelRequested ? `Cancel requested (${result.cancelRequested})` : "No active jobs");
  } catch (err) {
    setStatus(err.message || "Failed to cancel jobs");
  }
});
scanAllBtn.addEventListener("click", async () => {
  setStatus("Scanning all...");
  try {
    const entries = await fetchJson("/api/entries");
    for (const entry of entries) {
      await fetchJson(`/api/entries/${entry.id}/scan`, { method: "POST" });
    }
    await loadEntries();
    await loadItems();
    setStatus("Scan complete");
  } catch (err) {
    setStatus(err.message || "Scan failed");
  }
});

addEntryBtn.addEventListener("click", addEntry);
saveConfigBtn.addEventListener("click", saveConfig);
clearTargetSamplesBtn.addEventListener("click", async () => {
  configStatusEl.textContent = "Resetting target table...";
  try {
    await fetchJson("/api/targets/clear", { method: "POST" });
    await loadConfig();
    configStatusEl.textContent = "Target table reset";
  } catch (err) {
    configStatusEl.textContent = err.message || "Failed to reset target table";
  }
});

// Event delegation - attach once, handle dynamically created elements
// This prevents memory leaks from repeatedly attaching listeners on each refresh

entriesEl.addEventListener("click", async (e) => {
  const scanBtn = e.target.closest("button[data-scan]");
  if (scanBtn) {
    const entryId = scanBtn.getAttribute("data-scan");
    setStatus("Scanning...");
    try {
      await fetchJson(`/api/entries/${entryId}/scan`, { method: "POST" });
      await loadEntries();
      await loadItems();
      setStatus("Scan complete");
    } catch (err) {
      setStatus(err.message || "Scan failed");
    }
    return;
  }

  const deleteBtn = e.target.closest("button[data-delete-entry]");
  if (deleteBtn) {
    const entryId = deleteBtn.getAttribute("data-delete-entry");
    setStatus("Removing entry...");
    try {
      await deleteEntry(entryId);
      await loadEntries();
      await loadItems();
      await loadJobs();
      setStatus("Entry removed");
    } catch (err) {
      setStatus(err.message || "Failed to remove entry");
    }
    return;
  }
});

itemsEl.addEventListener("change", async (e) => {
  const input = e.target.closest("input[data-ready]");
  if (!input) return;
  const itemId = input.getAttribute("data-ready");
  setStatus("Updating...");
  try {
    await toggleReady(itemId, input.checked);
    await loadItems();
    setStatus("Updated");
  } catch (err) {
    setStatus(err.message || "Failed to update");
  }
});

itemsEl.addEventListener("click", async (e) => {
  const resetBtn = e.target.closest("button[data-reset]");
  if (resetBtn) {
    const itemId = resetBtn.getAttribute("data-reset");
    setStatus("Resetting...");
    try {
      await resetItem(itemId);
      await loadItems();
      setStatus("Reset");
    } catch (err) {
      setStatus(err.message || "Failed to reset");
    }
    return;
  }

  const deleteBtn = e.target.closest("button[data-delete]");
  if (deleteBtn) {
    const itemId = deleteBtn.getAttribute("data-delete");
    setStatus("Removing...");
    try {
      await deleteItem(itemId);
      await loadItems();
      setStatus("Removed");
    } catch (err) {
      setStatus(err.message || "Failed to remove");
    }
    return;
  }
});

workersEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-worker-delete]");
  if (!btn) return;
  const workerId = btn.getAttribute("data-worker-delete");
  setStatus("Removing worker...");
  try {
    await fetchJson(`/api/workers/${workerId}`, { method: "DELETE" });
    await loadWorkers();
    setStatus("Worker removed");
  } catch (err) {
    setStatus(err.message || "Failed to remove worker");
  }
});

jobsEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-job-delete]");
  if (!btn) return;
  const jobId = btn.getAttribute("data-job-delete");
  setStatus("Removing job...");
  try {
    const result = await fetchJson(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (result.cancelRequested) {
      setStatus("Cancel requested");
      await loadJobs();
      return;
    }
    await loadJobs();
    setStatus("Removed");
  } catch (err) {
    setStatus(err.message || "Failed to remove job");
  }
});

async function init() {
  try {
    setupTabs(document.querySelector("[data-tabs='sidebar']"), "sidebarTab");
    setupTabs(document.querySelector("[data-tabs='content']"), "contentTab");
    await loadConfig();
    await loadEntries();
    await loadItems();
    await loadWorkers();
    await loadJobs();
    await loadDiagnostics();
    await loadScanStatus();
  } catch (err) {
    setStatus(err.message || "Load failed");
  }
}

init();
setInterval(loadWorkers, 1000);
setInterval(loadJobs, 1000);
setInterval(loadItems, 1000);
setInterval(loadDiagnostics, 5000);
setInterval(loadScanStatus, 1000);
