# Media Spacesaver Suite — Master Spec (v0)

This document is the source of truth for the initial design. It is intentionally lean and tuned for a single-user LAN setup.

If anything here conflicts with future decisions, update this file first.

## Goals
- Local‑only media transcoding system with a simple server + one or more workers.
- Server owns state and truth; workers just execute jobs.
- Queue is **explicitly opt‑in**: nothing transcodes unless you mark it.
- Per‑file “space‑savings potential” metric to sort by best win.
- Simple, inspectable on‑disk state (JSON).
- Baseline HandBrakeCLI args, with per‑entry overrides as raw args.
- Replace in place, with local worker cache + copy back.

## Non‑Goals (for v0)
- Multi‑tenant, auth, or public deployment.
- Plugin system.
- Automatic requeue on failure.
- Remote path mapping / translation (assume consistent paths on all machines).
- Complex media rules (e.g., per‑track filtering beyond languages).

## Glossary
- **Entry**: a root folder added to the server (movie folder or show folder).
- **Item**: a media file discovered under an entry.
- **Scan / Rescan**: collect metadata for items; update ratios.
- **Ready**: an item explicitly flagged by you for transcoding.
- **Job**: a claimed item being processed by a worker.

## Architecture (Lean)
- **Server**: local HTTP app with web UI + REST API; owns state JSON.
- **Workers**: CLI processes on LAN machines; poll server for jobs and execute HandBrakeCLI.
- **Storage**: shared media folder(s) accessible by all machines (same paths).
- **On‑disk state**: JSON in `data/` within this repo.
- **Compare tool**: standalone local HTTP tool for 10‑sec A/B preview.

## Tooling
- Python environments and installs use `uv` (hard requirement).

## On‑Disk Data Model (JSON)
Single file for simplicity: `data/state.json`.

Top‑level shape (illustrative):
```json
{
  "version": 1,
  "config": {
    "baselineArgs": "-f av_mkv -e x265_10bit --encoder-preset slow -q 20",
    "targetMbPerMinByHeight": {
      "480": 6,
      "720": 10,
      "1080": 16,
      "2160": 32
    },
    "audioLangList": ["eng"],
    "subtitleLangList": ["eng"]
  },
  "entries": [],
  "items": [],
  "jobs": [],
  "workers": []
}
```

### Entry
```
{
  "id": "ent_...",
  "name": "Movies",
  "path": "X:\\Media\\Movies",
  "args": "",                // raw args added on top of baseline
  "createdAt": "...",
  "updatedAt": "...",
  "lastScanAt": "...",
  "notes": ""
}
```

### Item
```
{
  "id": "itm_...",
  "entryId": "ent_...",
  "path": "X:\\Media\\Movies\\Film.mkv",
  "sizeBytes": 0,
  "durationSec": 0,
  "width": 0,
  "height": 0,
  "fps": 0,
  "videoCodec": "hevc",
  "audioCodecs": ["dts", "aac"],
  "subtitleLangs": ["eng"],
  "scanAt": "...",
  "ready": false,
  "status": "idle",           // idle | queued | processing | done | failed
  "lastJobId": "job_...",
  "lastError": "",
  "lastTranscodeAt": "...",
  "transcodeCount": 0,
  "sourceFingerprint": "size+mtime",
  "ratio": {
    "targetBytes": 0,
    "savingsBytes": 0,
    "savingsPct": 0
  }
}
```

### Job
```
{
  "id": "job_...",
  "itemId": "itm_...",
  "workerId": "wrk_...",
  "status": "claimed",         // claimed | running | done | failed
  "claimedAt": "...",
  "startedAt": "...",
  "finishedAt": "...",
  "progress": {
    "pct": 0,
    "etaSec": 0
  },
  "logTail": ""
}
```

### Worker
```
{
  "id": "wrk_...",
  "name": "media-worker-1",
  "lastHeartbeatAt": "...",
  "status": "online",
  "workHours": [
    {"start": "22:00", "end": "06:00"}
  ]
}
```

## Space‑Savings Metric (Core Feature)
This is a heuristic to rank items by likely storage savings.

Inputs:
- `sizeBytes`
- `durationSec`
- `height` (or nearest bucket)
- Config knob: `targetMbPerMinByHeight` (editable)

Computation:
```
durationMin = durationSec / 60
targetMbPerMin = bucket(height)
targetBytes = durationMin * targetMbPerMin * 1024 * 1024
savingsBytes = sizeBytes - targetBytes
savingsPct = savingsBytes / sizeBytes
```

Sorting:
- Primary: `savingsBytes` (desc)
- Secondary: `savingsPct` (desc)

Notes:
- If `savingsBytes <= 0`, the ratio still exists but item falls to bottom.
- This is **not** a guarantee of output size; it’s a triage tool.

## Workflow (High Level)
1) **Add Entry** (folder).
2) **Scan Entry**:
   - Enumerate media files.
   - Extract metadata (duration, resolution, codecs).
   - Compute ratio.
3) **Review + Mark Ready**:
   - Sort by ratio.
   - Toggle ready on selected items.
4) **Worker Claims Job**:
   - Worker requests a job.
   - Server atomically marks item `processing` and creates job.
5) **Worker Executes**:
   - Copy source file to local cache.
   - Transcode to local output.
   - Copy output back to original folder.
   - Replace in place (atomic rename).
6) **Finalize**:
   - Mark item `done` or `failed`.
   - Store error + logs.
   - Rescan item metadata to update ratio.

## Worker Behavior
- Workers poll `POST /api/jobs/claim`.
- Server returns **one** job or `204 No Content`.
- Worker will only claim jobs during its configured work hours.
- Workers send progress updates from `HandBrakeCLI --json`.
- No auto requeue. Failures stay failed until you re‑queue.

## HandBrakeCLI Conventions
Baseline args (server config):
- `-f av_mkv`
- `-e x265_10bit`
- `--encoder-preset slow`
- `-q 20`
- `--audio-lang-list eng --first-audio -E copy`
- `--subtitle-lang-list eng --first-subtitle`
- `--crop 0:0:0:0`

Per‑entry `args` are appended after baseline.

### Scan Strategy (v0)
- Prefer `HandBrakeCLI --scan` output parsing.
- If JSON scan output is available in your version, use it.
- If scan parsing is too brittle, add optional `ffprobe` support later.

## API (Minimal)
Server:
- `POST /api/entries` → add entry
- `GET /api/entries`
- `POST /api/entries/{id}/scan`
- `GET /api/items?sort=savingsBytes`
- `POST /api/items/{id}/ready` → set true/false
- `POST /api/jobs/claim` → worker claims 1 ready item
- `POST /api/jobs/{id}/progress`
- `POST /api/jobs/{id}/complete`
- `POST /api/jobs/{id}/fail`
- `POST /api/workers/heartbeat`

## UI (MVP)
- Entries list + scan button.
- Items table: sortable by `savingsBytes` and `savingsPct`.
- Bulk select + “Mark Ready”.
- Job status and error visibility.
- Config screen: baseline args + target MB/min table.

## Compare Tool (Standalone)
Purpose: quickly test baseline args on a 10‑sec segment with a visual A/B compare UI.

Location: `tools/compare/` (standalone; can be integrated later).

Config:
- Uses its own config file with the **same args format** as the server baseline.
- Stores temp outputs in `data/compare_cache/` (gitignored).

Flow:
1) Pick a file (drag‑and‑drop or browse).
2) Scrub to a timestamp; click “Sample Here”.
3) Tool runs HandBrakeCLI for a 10‑sec segment using baseline args.
4) UI shows an A/B “wipe” slider to compare original vs encoded.
5) Controls: play/pause, scrub, zoom fit / zoom 1:1.

CLI command sketch:
```
HandBrakeCLI -i "<input>" -o "<output>" --start-at seconds:<t> --stop-at seconds:10 <baseline args...>
```

Notes:
- This tool is optional but recommended before large batch runs.
- No need to be pretty; functional controls are the priority.

## Failure Handling
- On worker failure: mark item `failed`, store `lastError`, keep in list.
- Manual UI action to reset item back to `idle` or `ready`.

## Open Questions / Decisions Later
- FFprobe vs HandBrake scan parsing.
- Optional per‑codec target sizes (HEVC vs AVC).
- Option to archive originals before replace (copy to `.bak`).
