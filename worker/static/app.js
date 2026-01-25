const statusEl = document.getElementById("status");
const statusLineEl = document.getElementById("status-line");
const statusProgressEl = document.getElementById("status-progress");
const statusJobEl = document.getElementById("status-job");
const statusErrorEl = document.getElementById("status-error");
const serverEl = document.getElementById("cfg-server");
const idEl = document.getElementById("cfg-id");
const cacheEl = document.getElementById("cfg-cache");
const handbrakeEl = document.getElementById("cfg-handbrake");
const ffmpegEl = document.getElementById("cfg-ffmpeg");
const hoursListEl = document.getElementById("hours-list");
const addHourBtn = document.getElementById("add-hour");
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

function formatEta(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function loadConfig() {
  const config = await fetchJson("/api/config");
  serverEl.value = config.serverUrl || "";
  idEl.value = config.workerId || "";
  cacheEl.value = config.cacheDir || "";
  handbrakeEl.value = config.handbrakePath || "";
  ffmpegEl.value = config.ffmpegPath || "";
  renderHours(config.workHours || []);
  pollEl.value = config.pollIntervalSec || 10;
}

function renderHours(hours) {
  if (!hoursListEl) return;
  hoursListEl.innerHTML = "";
  const list = Array.isArray(hours) ? hours : [];
  if (!list.length) {
    hoursListEl.innerHTML = "<div class=\"hint\">No hours set (worker runs 24/7).</div>";
    return;
  }
  list.forEach((block, idx) => {
    const row = document.createElement("div");
    row.className = "hours-row";
    row.innerHTML = `
      <input class="time-input" type="time" data-start="${idx}" value="${block.start || ""}" />
      <span class="hours-sep">→</span>
      <input class="time-input" type="time" data-end="${idx}" value="${block.end || ""}" />
      <button class="btn danger" data-remove="${idx}" type="button">✕</button>
    `;
    hoursListEl.appendChild(row);
  });

  hoursListEl.querySelectorAll("button[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = parseInt(btn.getAttribute("data-remove"), 10);
      const next = list.filter((_, i) => i !== index);
      renderHours(next);
    });
  });
}

function collectHours() {
  if (!hoursListEl) return [];
  const rows = hoursListEl.querySelectorAll(".hours-row");
  const hours = [];
  rows.forEach((row) => {
    const start = row.querySelector("input[data-start]")?.value || "";
    const end = row.querySelector("input[data-end]")?.value || "";
    if (start && end) {
      hours.push({ start, end });
    }
  });
  return hours;
}

async function saveConfig() {
  saveStatusEl.textContent = "Saving...";
  try {
    await fetchJson("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serverUrl: serverEl.value,
        cacheDir: cacheEl.value,
        handbrakePath: handbrakeEl.value,
        ffmpegPath: ffmpegEl.value,
        workHours: collectHours(),
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
      const eta = formatEta(status.progressEtaSec);
      const etaText = eta ? ` ETA ${eta}` : "";
      statusProgressEl.textContent = `Progress: ${status.progressPct}% ${status.progressMessage ? `(${status.progressMessage})` : ""}${etaText}`;
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
    if (!diag.ffmpeg || !diag.ffmpeg.found) {
      warnings.push("ffmpeg not found. Set FFmpeg path in config or FFMPEG_PATH.");
    }
    alertsEl.innerHTML = warnings.map((msg) => `<div class="alert warn">${msg}</div>`).join("");
  } catch (_) {
    alertsEl.innerHTML = "";
  }
}

saveBtn.addEventListener("click", saveConfig);
addHourBtn.addEventListener("click", () => {
  const current = collectHours();
  current.push({ start: "22:00", end: "06:00" });
  renderHours(current);
});

async function init() {
  await loadConfig();
  await loadStatus();
  await loadDiagnostics();
}

init();
setInterval(loadStatus, 2000);
setInterval(loadDiagnostics, 5000);
