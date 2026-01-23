const statusEl = document.getElementById("status");
const fileNameEl = document.getElementById("file-name");
const filePathEl = document.getElementById("file-path");
const baselineEl = document.getElementById("baseline");
const argsStatusEl = document.getElementById("args-status");
const seekSelect = document.getElementById("seek-select");
const seekTimeEl = document.getElementById("seek-time");
const durationEl = document.getElementById("duration");
const sampleStatusEl = document.getElementById("sample-status");
const sampleSizeEl = document.getElementById("sample-size");
const sampleButton = document.getElementById("btn-sample");
const playButton = document.getElementById("btn-play");
const playbackSeek = document.getElementById("seek-playback");
const playbackTimeEl = document.getElementById("playback-time");
const playbackLengthEl = document.getElementById("playback-length");
const loopToggle = document.getElementById("loop");
const wipeSlider = document.getElementById("wipe");
const encodedLayer = document.getElementById("encoded-layer");
const wipeLine = document.getElementById("wipe-line");
const overlay = document.getElementById("overlay");
const videoShell = document.getElementById("video-shell");
const sourceVideo = document.getElementById("video-source");
const originalVideo = document.getElementById("video-original");
const encodedVideo = document.getElementById("video-encoded");

const selectButton = document.getElementById("btn-select");
const saveArgsButton = document.getElementById("btn-save-args");
const zoomFitButton = document.getElementById("zoom-fit");
const zoomActualButton = document.getElementById("zoom-actual");
const previewButton = document.getElementById("btn-preview");
const compareButton = document.getElementById("btn-compare");

const state = {
  duration: 0,
  sampleStart: 0,
  sampleDuration: 10,
  encodedReady: false,
  playing: false,
  hasFile: false,
  syncing: false,
  pendingSeek: null,
  mode: "preview",
  panX: 0,
  panY: 0,
  dragging: false,
  dragStartX: 0,
  dragStartY: 0,
  dragPanX: 0,
  dragPanY: 0,
};

function formatTime(seconds) {
  const sec = Math.max(0, Math.floor(seconds));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  if (!bytes || bytes < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(value >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}

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
      // keep fallback text
    }
    throw new Error(message);
  }
  return res.json();
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setOverlay(text) {
  overlay.textContent = text;
  overlay.style.display = text ? "flex" : "none";
}

function updateWipe(value) {
  encodedLayer.style.clipPath = `inset(0 0 0 ${value}%)`;
  wipeLine.style.left = `${value}%`;
}

function getActiveVideo() {
  if (state.mode === "compare") {
    return originalVideo;
  }
  return sourceVideo;
}

function clampPan(x, y) {
  const video = getActiveVideo();
  if (!video || !video.videoWidth || !video.videoHeight) {
    return { x, y };
  }
  const cw = videoShell.clientWidth;
  const ch = videoShell.clientHeight;
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const minX = Math.min(0, cw - vw);
  const minY = Math.min(0, ch - vh);
  const maxX = 0;
  const maxY = 0;
  return {
    x: Math.min(maxX, Math.max(minX, x)),
    y: Math.min(maxY, Math.max(minY, y)),
  };
}

function setPan(x, y) {
  const clamped = clampPan(x, y);
  state.panX = clamped.x;
  state.panY = clamped.y;
  videoShell.style.setProperty("--pan-x", `${state.panX}px`);
  videoShell.style.setProperty("--pan-y", `${state.panY}px`);
}

function resetPan() {
  setPan(0, 0);
}

function setEncodedVisible(visible) {
  encodedLayer.style.display = visible ? "block" : "none";
  wipeLine.style.display = visible ? "block" : "none";
  wipeSlider.disabled = !visible;
}

function setMode(mode) {
  state.mode = mode;
  if (mode === "preview") {
    videoShell.classList.add("mode-preview");
    videoShell.classList.remove("mode-compare");
    playButton.disabled = true;
    playbackSeek.disabled = true;
  } else {
    videoShell.classList.remove("mode-preview");
    videoShell.classList.add("mode-compare");
    playButton.disabled = !state.encodedReady;
    playbackSeek.disabled = !state.encodedReady;
  }
  resetPan();
}

function updatePlaybackUI() {
  const t = originalVideo.currentTime || 0;
  playbackSeek.value = t;
  playbackTimeEl.textContent = formatTime(t);
}

function seekVideo(video, timeSec) {
  return new Promise((resolve) => {
    if (Math.abs((video.currentTime || 0) - timeSec) < 0.01) {
      video.currentTime = timeSec;
      resolve();
      return;
    }
    const onSeeked = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      resolve();
    };
    const cleanup = () => {
      clearTimeout(timeout);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    };
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError);
    video.currentTime = timeSec;
    const timeout = setTimeout(() => {
      cleanup();
      resolve();
    }, 1000);
  });
}

async function syncSeek(targetTime) {
  if (!state.encodedReady) return;
  if (state.syncing) {
    state.pendingSeek = targetTime;
    return;
  }
  state.syncing = true;
  state.pendingSeek = null;

  const wasPlaying = !originalVideo.paused;
  originalVideo.pause();

  await seekVideo(originalVideo, targetTime);
  await seekVideo(encodedVideo, targetTime);
  updatePlaybackUI();

  if (wasPlaying) {
    await originalVideo.play();
  }

  state.syncing = false;
  if (state.pendingSeek !== null) {
    const next = state.pendingSeek;
    state.pendingSeek = null;
    syncSeek(next);
  }
}

function startFrameSync() {
  if (!originalVideo.requestVideoFrameCallback) {
    return;
  }

  const onFrame = (_now, metadata) => {
    if (!state.encodedReady || originalVideo.paused) {
      return;
    }
    const target = metadata?.mediaTime || originalVideo.currentTime || 0;
    const drift = Math.abs((encodedVideo.currentTime || 0) - target);
    if (drift > 0.02) {
      encodedVideo.currentTime = target;
    }
    originalVideo.requestVideoFrameCallback(onFrame);
  };

  originalVideo.requestVideoFrameCallback(onFrame);
}

async function loadConfig() {
  const config = await fetchJson("/api/config");
  baselineEl.value = config.baselineArgs || "";
  state.sampleDuration = config.sampleSeconds || 10;
  playbackSeek.max = state.sampleDuration;
  playbackLengthEl.textContent = formatTime(state.sampleDuration);
}

async function selectFile() {
  setStatus("Opening file dialog...");
  try {
    const data = await fetchJson("/api/select-file", { method: "POST" });
    if (!data.selected) {
      setStatus("No file selected");
      return;
    }
    state.hasFile = true;
    fileNameEl.textContent = data.name;
    filePathEl.textContent = data.path;
    seekSelect.disabled = false;
    sampleButton.disabled = false;
    encodedVideo.removeAttribute("src");
    originalVideo.removeAttribute("src");
    sourceVideo.src = `/media/source?ts=${Date.now()}`;
    sourceVideo.load();
    state.encodedReady = false;
    compareButton.disabled = true;
    setEncodedVisible(false);
    setMode("preview");
    setOverlay("Loading metadata...");
    setStatus("File loaded");
  } catch (err) {
    setStatus("Failed to select file");
    console.error(err);
  }
}

async function saveArgs() {
  argsStatusEl.textContent = "Saving...";
  try {
    await fetchJson("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baselineArgs: baselineEl.value }),
    });
    argsStatusEl.textContent = "Saved";
  } catch (err) {
    argsStatusEl.textContent = "Error";
    console.error(err);
  }
}

async function createSample() {
  if (!state.hasFile) return;
  sampleButton.disabled = true;
  setStatus("Encoding sample...");
  sampleStatusEl.textContent = "Encoding...";
  sampleSizeEl.textContent = "";
  const start = parseFloat(seekSelect.value || "0");
  try {
    const data = await fetchJson("/api/sample", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timestampSec: start }),
    });
    state.sampleStart = data.sampleStart;
    state.sampleDuration = data.sampleDuration;
    playbackSeek.max = state.sampleDuration;
    playbackLengthEl.textContent = formatTime(state.sampleDuration);

    originalVideo.src = `/media/original?ts=${Date.now()}`;
    originalVideo.load();
    encodedVideo.src = `/media/encoded?ts=${Date.now()}`;
    encodedVideo.load();
    state.encodedReady = true;
    compareButton.disabled = false;
    setEncodedVisible(true);
    setMode("compare");
    sampleStatusEl.textContent = "Ready";
    if (data.sourceSizeBytes && data.encodedSizeBytes) {
      const savings = data.sourceSizeBytes - data.encodedSizeBytes;
      const pct = data.sourceSizeBytes > 0 ? (savings / data.sourceSizeBytes) * 100 : 0;
      sampleSizeEl.textContent = `Size: ${formatBytes(data.sourceSizeBytes)} → ${formatBytes(data.encodedSizeBytes)} (${pct.toFixed(1)}% saved)`;
    }
    setStatus("Sample ready");
  } catch (err) {
    const msg = err?.message ? err.message : "Failed";
    sampleStatusEl.textContent = msg;
    sampleSizeEl.textContent = "";
    setStatus("Sample failed");
    console.error(err);
  } finally {
    sampleButton.disabled = false;
  }
}

function playPause() {
  if (!state.encodedReady || state.mode !== "compare") return;
  if (originalVideo.paused) {
    originalVideo.play();
    state.playing = true;
    playButton.textContent = "Pause";
    setStatus("Playing");
    startFrameSync();
  } else {
    originalVideo.pause();
    state.playing = false;
    playButton.textContent = "Play";
    setStatus("Paused");
  }
}

function handleOriginalTimeUpdate() {
  if (!state.encodedReady || state.mode !== "compare") return;
  if (state.syncing) return;
  const t = originalVideo.currentTime || 0;
  if (Math.abs(encodedVideo.currentTime - t) > 0.02) {
    encodedVideo.currentTime = t;
  }
  updatePlaybackUI();

  if (t >= state.sampleDuration - 0.05) {
    if (loopToggle.checked) {
      originalVideo.currentTime = 0;
      encodedVideo.currentTime = 0;
    } else {
      originalVideo.pause();
      playButton.textContent = "Play";
    }
  }
}

function seekPlayback(value) {
  const time = parseFloat(value);
  syncSeek(time);
}

function updateSelectTime(value) {
  const t = parseFloat(value);
  sourceVideo.currentTime = t;
  seekTimeEl.textContent = formatTime(t);
}

sourceVideo.addEventListener("loadedmetadata", () => {
  state.duration = sourceVideo.duration || 0;
  seekSelect.max = state.duration;
  durationEl.textContent = formatTime(state.duration);
  seekTimeEl.textContent = formatTime(seekSelect.value || 0);
  setOverlay("");
  resetPan();
});

sourceVideo.addEventListener("error", () => {
  setOverlay("Original preview failed (codec unsupported?)");
});

encodedVideo.addEventListener("loadedmetadata", () => {
  encodedVideo.currentTime = 0;
  originalVideo.currentTime = 0;
  updatePlaybackUI();
  resetPan();
  if (encodedVideo.muted) {
    encodedVideo.play().then(() => encodedVideo.pause()).catch(() => {});
  }
});

originalVideo.addEventListener("timeupdate", handleOriginalTimeUpdate);

selectButton.addEventListener("click", selectFile);
saveArgsButton.addEventListener("click", saveArgs);
sampleButton.addEventListener("click", createSample);
playButton.addEventListener("click", playPause);
previewButton.addEventListener("click", () => setMode("preview"));
compareButton.addEventListener("click", () => {
  if (state.encodedReady) {
    setMode("compare");
  }
});

seekSelect.addEventListener("input", (e) => updateSelectTime(e.target.value));
playbackSeek.addEventListener("input", (e) => seekPlayback(e.target.value));

wipeSlider.addEventListener("input", (e) => updateWipe(e.target.value));
zoomFitButton.addEventListener("click", () => {
  videoShell.classList.remove("actual");
  videoShell.classList.add("fit");
  resetPan();
});
zoomActualButton.addEventListener("click", () => {
  videoShell.classList.remove("fit");
  videoShell.classList.add("actual");
  resetPan();
});

videoShell.addEventListener("pointerdown", (event) => {
  if (!videoShell.classList.contains("actual")) {
    return;
  }
  state.dragging = true;
  state.dragStartX = event.clientX;
  state.dragStartY = event.clientY;
  state.dragPanX = state.panX;
  state.dragPanY = state.panY;
  videoShell.setPointerCapture(event.pointerId);
});

videoShell.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  const dx = event.clientX - state.dragStartX;
  const dy = event.clientY - state.dragStartY;
  setPan(state.dragPanX + dx, state.dragPanY + dy);
});

function endDrag(event) {
  if (!state.dragging) return;
  state.dragging = false;
  try {
    videoShell.releasePointerCapture(event.pointerId);
  } catch (_) {
    // ignore
  }
}

videoShell.addEventListener("pointerup", endDrag);
videoShell.addEventListener("pointercancel", endDrag);

window.addEventListener("load", async () => {
  updateWipe(wipeSlider.value);
  setEncodedVisible(false);
  setMode("preview");
  resetPan();
  setOverlay("Select a file to begin");
  await loadConfig();
});
