from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timezone
import math

from state import load_state, now_iso, new_id, update_state, default_state
from scan import compute_ratio, list_media_files, probe_media, ffprobe_path

def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


def refresh_item_after_transcode(item, config, output_size=None):
    if output_size is not None:
        item["sizeBytes"] = int(output_size)

    path = Path(item.get("path") or "")
    stat = None
    if path.exists():
        try:
            stat = path.stat()
        except OSError:
            stat = None

    if stat:
        item["sizeBytes"] = stat.st_size
        item["mtime"] = int(stat.st_mtime)
        item["sourceFingerprint"] = f"{stat.st_size}:{int(stat.st_mtime)}"

    if path.exists():
        metadata = probe_media(str(path), config.get("ffprobePath"))
        if metadata:
            item.update(metadata)

    item["scanAt"] = now_iso()
    item["ratio"] = compute_ratio(item, config)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def cleanup_stale_jobs(state, max_age_sec=180, worker_grace_sec=120):
    now = datetime.now(timezone.utc)
    jobs = state.get("jobs") or []
    items = state.get("items") or []
    workers = state.get("workers") or []
    workers_by_id = {worker.get("id"): worker for worker in workers}
    items_by_id = {item.get("id"): item for item in items}
    updated = False

    for job in jobs:
        if job.get("status") not in {"claimed", "running"}:
            continue
        worker = workers_by_id.get(job.get("workerId"))
        worker_recent = False
        if worker:
            last_hb = _parse_iso(worker.get("lastHeartbeatAt"))
            if last_hb:
                hb_age = (now - last_hb).total_seconds()
                worker_recent = hb_age < worker_grace_sec
        if worker_recent:
            continue
        last_update = _parse_iso(job.get("lastUpdateAt")) or _parse_iso(job.get("claimedAt"))
        if not last_update:
            continue
        age = (now - last_update).total_seconds()
        if age < max_age_sec:
            continue
        job["status"] = "failed"
        job["finishedAt"] = now_iso()
        job["error"] = f"Stale job (no updates for {int(age)}s)"
        item = items_by_id.get(job.get("itemId"))
        if item:
            item["status"] = "failed"
            item["ready"] = False
            item["lastError"] = job["error"]
        updated = True

    if updated:
        state["jobs"] = jobs
        state["items"] = items
    return updated


def prune_old_jobs(state, max_age_hours=24, max_jobs=100):
    """Remove old completed/failed jobs to prevent unbounded growth.
    
    Keeps jobs that are:
    - Active (claimed/running)
    - Completed/failed within the last max_age_hours
    - Up to max_jobs most recent completed/failed jobs
    """
    now = datetime.now(timezone.utc)
    jobs = state.get("jobs") or []
    if len(jobs) <= max_jobs:
        return False
    
    max_age_sec = max_age_hours * 3600
    active = []
    finished = []
    
    for job in jobs:
        status = job.get("status")
        if status in {"claimed", "running"}:
            active.append(job)
        else:
            finished.append(job)
    
    # Sort finished jobs by finished time (newest first)
    def get_finished_time(job):
        t = _parse_iso(job.get("finishedAt")) or _parse_iso(job.get("claimedAt"))
        return t or datetime.min.replace(tzinfo=timezone.utc)
    
    finished.sort(key=get_finished_time, reverse=True)
    
    # Keep recent finished jobs (within max_age_hours)
    kept_finished = []
    for job in finished:
        finished_at = get_finished_time(job)
        age = (now - finished_at).total_seconds()
        if age < max_age_sec and len(kept_finished) < max_jobs:
            kept_finished.append(job)
        elif len(kept_finished) < max_jobs // 2:
            # Keep at least some history even if old
            kept_finished.append(job)
    
    new_jobs = active + kept_finished
    if len(new_jobs) < len(jobs):
        state["jobs"] = new_jobs
        return True
    return False


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent / "static" / "index.html")


class EntryRequest(BaseModel):
    path: str
    name: str | None = None
    args: str | None = ""


class ReadyRequest(BaseModel):
    ready: bool


class ConfigRequest(BaseModel):
    baselineArgs: str | None = None
    targetMbPerMinByHeight: dict | None = None
    ffprobePath: str | None = None
    targetSamplesByHeight: dict | None = None


class ClaimRequest(BaseModel):
    workerId: str | None = None
    workerName: str | None = None


class JobUpdate(BaseModel):
    error: str | None = None
    outputSizeBytes: int | None = None


class JobProgress(BaseModel):
    pct: float | None = None
    etaSec: int | None = None
    logTail: str | None = None


class WorkerHeartbeat(BaseModel):
    workerId: str
    workerName: str | None = None
    workHours: list | None = None
    withinWorkHours: bool | None = None


class EntryUpdate(BaseModel):
    name: str | None = None
    args: str | None = None
    notes: str | None = None


class ItemPathUpdate(BaseModel):
    path: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    state = load_state()
    return JSONResponse(state.get("config") or {})


@app.get("/api/diagnostics")
def diagnostics():
    state = load_state()
    config = state.get("config") or {}
    ffprobe = ffprobe_path(config.get("ffprobePath"))
    return JSONResponse(
        {
            "ffprobe": {
                "found": bool(ffprobe),
                "path": ffprobe or "",
            }
        }
    )


@app.get("/api/scan-status")
def scan_status():
    state = load_state()
    return JSONResponse(state.get("scanStatus") or {})


@app.post("/api/config")
def set_config(payload: ConfigRequest):
    def mutator(state):
        config = state.get("config") or {}
        if payload.baselineArgs is not None:
            config["baselineArgs"] = payload.baselineArgs
        if payload.targetMbPerMinByHeight is not None:
            config["targetMbPerMinByHeight"] = payload.targetMbPerMinByHeight
        if payload.ffprobePath is not None:
            config["ffprobePath"] = payload.ffprobePath
        if payload.targetSamplesByHeight is not None:
            config["targetSamplesByHeight"] = payload.targetSamplesByHeight
        state["config"] = config
        return config

    config = update_state(mutator)
    return JSONResponse(config)


@app.get("/api/entries")
def list_entries():
    state = load_state()
    return JSONResponse(state.get("entries") or [])


@app.post("/api/entries")
def add_entry(payload: EntryRequest):
    path = str(Path(payload.path).resolve())
    name = payload.name or Path(path).name

    def mutator(state):
        entry = {
            "id": new_id("ent"),
            "name": name,
            "path": path,
            "args": payload.args or "",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "lastScanAt": None,
            "notes": "",
        }
        state.setdefault("entries", []).append(entry)
        return entry

    entry = update_state(mutator)
    return JSONResponse(entry)


@app.patch("/api/entries/{entry_id}")
def update_entry(entry_id: str, payload: EntryUpdate):
    def mutator(state):
        entries = state.get("entries") or []
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        if payload.name is not None:
            entry["name"] = payload.name
        if payload.args is not None:
            entry["args"] = payload.args
        if payload.notes is not None:
            entry["notes"] = payload.notes
        entry["updatedAt"] = now_iso()
        return entry

    entry = update_state(mutator)
    return JSONResponse(entry)


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: str):
    def mutator(state):
        entries = state.get("entries") or []
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")

        items = state.get("items") or []
        entry_items = [i for i in items if i.get("entryId") == entry_id]
        if any(i.get("status") == "processing" for i in entry_items):
            raise HTTPException(status_code=409, detail="Entry has processing items")

        remaining_items = [i for i in items if i.get("entryId") != entry_id]
        removed_item_ids = {i.get("id") for i in entry_items}
        jobs = state.get("jobs") or []
        remaining_jobs = [j for j in jobs if j.get("itemId") not in removed_item_ids]

        state["entries"] = [e for e in entries if e.get("id") != entry_id]
        state["items"] = remaining_items
        state["jobs"] = remaining_jobs
        return {"ok": True}

    result = update_state(mutator)
    return JSONResponse(result)


@app.post("/api/entries/{entry_id}/scan")
def scan_entry(entry_id: str):
    state = load_state()
    entries = state.get("entries") or []
    entry = next((e for e in entries if e.get("id") == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    config = state.get("config") or {}
    entry_name = entry.get("name") or "Entry"
    entry_path = entry.get("path")
    files = list_media_files(entry_path)
    total = len(files)
    started_at = now_iso()

    update_state(
        lambda state: state.update(
            {
                "scanStatus": {
                    "active": True,
                    "entryId": entry_id,
                    "entryName": entry_name,
                    "total": total,
                    "done": 0,
                    "currentPath": None,
                    "startedAt": started_at,
                    "updatedAt": started_at,
                    "finishedAt": None,
                }
            }
        )
        or state
    )

    items_snapshot = {item.get("path"): item for item in (state.get("items") or [])}
    found_paths = set()
    done = 0

    try:
        for path in files:
            path_str = str(path)
            found_paths.add(path_str)
            done += 1
            try:
                stat = path.stat()
            except OSError:
                update_state(
                    lambda state: state.update(
                        {
                            "scanStatus": {
                                **(state.get("scanStatus") or {}),
                                "active": True,
                                "entryId": entry_id,
                                "entryName": entry_name,
                                "total": total,
                                "done": done,
                                "currentPath": path_str,
                                "updatedAt": now_iso(),
                            }
                        }
                    )
                    or state
                )
                continue

            fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
            existing = items_snapshot.get(path_str) or {}
            needs_scan = existing.get("sourceFingerprint") != fingerprint or not existing.get("scanAt")
            metadata = probe_media(path_str, config.get("ffprobePath")) if needs_scan else {}

            def mutator(state):
                items = state.get("items") or []
                item = next((i for i in items if i.get("path") == path_str), None)
                if not item:
                    item = {
                        "id": new_id("itm"),
                        "entryId": entry_id,
                        "path": path_str,
                        "sizeBytes": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "durationSec": 0,
                        "width": 0,
                        "height": 0,
                        "fps": 0,
                        "videoCodec": None,
                        "audioCodecs": [],
                        "subtitleLangs": [],
                        "encodedBy": "",
                        "encodedBySpacesaver": False,
                        "scanAt": None,
                        "ready": False,
                        "status": "idle",
                        "lastJobId": None,
                        "lastError": "",
                        "lastTranscodeAt": None,
                        "transcodeCount": 0,
                        "sourceFingerprint": fingerprint,
                        "ratio": {"targetBytes": 0, "savingsBytes": 0, "savingsPct": 0},
                    }
                    items.append(item)

                if item.get("sourceFingerprint") != fingerprint or not item.get("scanAt"):
                    item.update(metadata)
                    item["scanAt"] = now_iso()
                    item["sizeBytes"] = stat.st_size
                    item["mtime"] = int(stat.st_mtime)
                    item["sourceFingerprint"] = fingerprint
                    item["ratio"] = compute_ratio(item, config)

                scan_status = state.get("scanStatus") or {}
                scan_status.update(
                    {
                        "active": True,
                        "entryId": entry_id,
                        "entryName": entry_name,
                        "total": total,
                        "done": done,
                        "currentPath": path_str,
                        "updatedAt": now_iso(),
                    }
                )
                state["scanStatus"] = scan_status
                state["items"] = items
                return {"ok": True}

            update_state(mutator)
    finally:
        def finish_mutator(state):
            entries = state.get("entries") or []
            entry = next((e for e in entries if e.get("id") == entry_id), None)
            if entry:
                entry["lastScanAt"] = now_iso()
                entry["updatedAt"] = now_iso()

            scan_status = state.get("scanStatus") or {}
            scan_status.update(
                {
                    "active": False,
                    "entryId": entry_id,
                    "entryName": entry_name,
                    "total": total,
                    "done": done,
                    "currentPath": None,
                    "finishedAt": now_iso(),
                }
            )
            state["scanStatus"] = scan_status
            return {"found": len(found_paths), "entryId": entry_id}

        result = update_state(finish_mutator)

    return JSONResponse(result)


@app.get("/api/items")
def list_items(entryId: str | None = None, sort: str | None = None):
    def mutator(state):
        cleanup_stale_jobs(state)
        prune_old_jobs(state)
        return state

    state = update_state(mutator)
    items = list(state.get("items") or [])
    if entryId:
        items = [item for item in items if item.get("entryId") == entryId]

    if sort == "savingsBytes":
        items.sort(key=lambda x: x.get("ratio", {}).get("savingsBytes", 0), reverse=True)
    elif sort == "savingsPct":
        items.sort(key=lambda x: x.get("ratio", {}).get("savingsPct", 0), reverse=True)

    return JSONResponse(items)


@app.post("/api/items/{item_id}/ready")
def set_ready(item_id: str, payload: ReadyRequest):
    def mutator(state):
        items = state.get("items") or []
        item = next((i for i in items if i.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if item.get("status") == "processing":
            raise HTTPException(status_code=409, detail="Item is processing")
        item["ready"] = payload.ready
        item["status"] = "queued" if payload.ready else "idle"
        return item

    item = update_state(mutator)
    return JSONResponse(item)


@app.post("/api/items/{item_id}/reset")
def reset_item(item_id: str):
    def mutator(state):
        items = state.get("items") or []
        item = next((i for i in items if i.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if item.get("status") == "processing":
            raise HTTPException(status_code=409, detail="Item is processing")
        item["status"] = "idle"
        item["ready"] = False
        item["lastError"] = ""
        return item

    item = update_state(mutator)
    return JSONResponse(item)


@app.post("/api/items/{item_id}/path")
def update_item_path(item_id: str, payload: ItemPathUpdate):
    def mutator(state):
        items = state.get("items") or []
        item = next((i for i in items if i.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if item.get("status") == "processing":
            item["path"] = payload.path
            return item
        item["path"] = payload.path
        return item

    item = update_state(mutator)
    return JSONResponse(item)

@app.delete("/api/items/{item_id}")
def delete_item(item_id: str):
    def mutator(state):
        items = state.get("items") or []
        item = next((i for i in items if i.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if item.get("status") == "processing":
            raise HTTPException(status_code=409, detail="Item is processing")
        state["items"] = [i for i in items if i.get("id") != item_id]
        return {"ok": True}

    result = update_state(mutator)
    return JSONResponse(result)


@app.post("/api/jobs/claim")
def claim_job(payload: ClaimRequest):
    def mutator(state):
        cleanup_stale_jobs(state)
        workers = state.get("workers") or []
        worker = None
        if payload.workerId:
            worker = next((w for w in workers if w.get("id") == payload.workerId), None)
        if not worker and payload.workerName:
            worker = next((w for w in workers if w.get("name") == payload.workerName), None)
        if not worker:
            worker = {
                "id": payload.workerId or new_id("wrk"),
                "name": payload.workerName or "worker",
                "status": "online",
                "lastHeartbeatAt": now_iso(),
                "workHours": [],
            }
            workers.append(worker)
        else:
            worker["lastHeartbeatAt"] = now_iso()
            worker["status"] = "online"

        state["workers"] = workers

        items = state.get("items") or []
        entries = state.get("entries") or []
        item = next((i for i in items if i.get("ready") and i.get("status") == "queued"), None)
        if not item:
            return None

        entry = next((e for e in entries if e.get("id") == item.get("entryId")), None)
        args = state.get("config", {}).get("baselineArgs", "")
        if entry and entry.get("args"):
            args = f"{args} {entry.get('args')}".strip()

        job = {
            "id": new_id("job"),
            "itemId": item.get("id"),
            "workerId": worker.get("id"),
            "status": "claimed",
            "claimedAt": now_iso(),
            "startedAt": None,
            "finishedAt": None,
            "error": "",
            "cancelRequested": False,
            "lastUpdateAt": now_iso(),
        }
        state.setdefault("jobs", []).append(job)

        item["status"] = "processing"
        item["lastJobId"] = job["id"]
        item["lastError"] = ""

        state["items"] = items

        return {
            "job": job,
            "item": item,
            "entry": entry,
            "args": args,
        }

    result = update_state(mutator)
    if not result:
        return Response(status_code=204)
    return JSONResponse(result)


@app.get("/api/jobs")
def list_jobs():
    def mutator(state):
        cleanup_stale_jobs(state)
        prune_old_jobs(state)
        return state

    state = update_state(mutator)
    jobs = state.get("jobs") or []
    items_by_id = {item.get("id"): item for item in (state.get("items") or [])}
    workers_by_id = {worker.get("id"): worker for worker in (state.get("workers") or [])}
    enriched = []
    for job in jobs:
        payload = dict(job)
        item = items_by_id.get(job.get("itemId"))
        if item:
            payload["itemPath"] = item.get("path")
            payload["itemStatus"] = item.get("status")
        worker = workers_by_id.get(job.get("workerId"))
        if worker:
            payload["workerName"] = worker.get("name")
        enriched.append(payload)
    return JSONResponse(enriched)

@app.post("/api/jobs/cancel-all")
def cancel_all_jobs():
    def mutator(state):
        jobs = state.get("jobs") or []
        active = 0
        for job in jobs:
            if job.get("status") in {"claimed", "running"}:
                job["cancelRequested"] = True
                progress = job.get("progress") or {}
                progress["logTail"] = "Cancel requested"
                job["progress"] = progress
                job["lastUpdateAt"] = now_iso()
                active += 1
        return {"ok": True, "cancelRequested": active}

    result = update_state(mutator)
    return JSONResponse(result)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    state = load_state()
    job = next((j for j in (state.get("jobs") or []) if j.get("id") == job_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)

class TargetSample(BaseModel):
    height: int
    mbPerMin: float


@app.post("/api/targets")
def add_target_sample(payload: TargetSample):
    def mutator(state):
        config = state.get("config") or {}
        samples = config.get("targetSamplesByHeight") or {}
        height_key = str(payload.height)
        bucket = list(samples.get(height_key) or [])
        bucket.append(float(payload.mbPerMin))
        samples[height_key] = bucket

        avg = sum(bucket) / len(bucket)
        targets = config.get("targetMbPerMinByHeight") or {}
        targets[height_key] = round(avg, 1)

        config["targetSamplesByHeight"] = samples
        config["targetMbPerMinByHeight"] = targets
        state["config"] = config

        return {"height": payload.height, "count": len(bucket), "avg": round(avg, 1)}

    result = update_state(mutator)
    return JSONResponse(result)


@app.post("/api/targets/clear")
def clear_target_samples():
    def mutator(state):
        config = state.get("config") or {}
        config["targetSamplesByHeight"] = {}
        config["targetMbPerMinByHeight"] = default_state()["config"]["targetMbPerMinByHeight"]
        state["config"] = config
        return {"ok": True}

    result = update_state(mutator)
    return JSONResponse(result)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    def mutator(state):
        jobs = state.get("jobs") or []
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.get("status") in {"claimed", "running"}:
            job["cancelRequested"] = True
            return {"ok": False, "cancelRequested": True}
        state["jobs"] = [j for j in jobs if j.get("id") != job_id]
        items = state.get("items") or []
        for item in items:
            if item.get("lastJobId") == job_id:
                item["lastJobId"] = None
        state["items"] = items
        return {"ok": True}

    result = update_state(mutator)
    return JSONResponse(result)


@app.get("/api/workers")
def list_workers():
    state = load_state()
    return JSONResponse(state.get("workers") or [])


@app.delete("/api/workers/{worker_id}")
def delete_worker(worker_id: str):
    def mutator(state):
        workers = state.get("workers") or []
        worker = next((w for w in workers if w.get("id") == worker_id), None)
        if not worker:
            raise HTTPException(status_code=404, detail="Worker not found")
        state["workers"] = [w for w in workers if w.get("id") != worker_id]
        return {"ok": True}

    result = update_state(mutator)
    return JSONResponse(result)


@app.post("/api/workers/heartbeat")
def worker_heartbeat(payload: WorkerHeartbeat):
    def mutator(state):
        workers = state.get("workers") or []
        worker = next((w for w in workers if w.get("id") == payload.workerId), None)
        if not worker:
            worker = {
                "id": payload.workerId,
                "name": payload.workerName or "worker",
                "status": "online",
                "lastHeartbeatAt": now_iso(),
                "workHours": payload.workHours or [],
                "withinWorkHours": payload.withinWorkHours if payload.withinWorkHours is not None else True,
            }
            workers.append(worker)
        else:
            if payload.workerName:
                worker["name"] = payload.workerName
            worker["status"] = "online"
            worker["lastHeartbeatAt"] = now_iso()
            if payload.workHours is not None:
                worker["workHours"] = payload.workHours
            if payload.withinWorkHours is not None:
                worker["withinWorkHours"] = payload.withinWorkHours
        state["workers"] = workers
        return worker

    worker = update_state(mutator)
    return JSONResponse(worker)


@app.post("/api/jobs/{job_id}/start")
def job_start(job_id: str):
    def mutator(state):
        jobs = state.get("jobs") or []
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job["status"] = "running"
        job["startedAt"] = now_iso()
        job["lastUpdateAt"] = now_iso()
        return job

    job = update_state(mutator)
    return JSONResponse(job)


@app.post("/api/jobs/{job_id}/progress")
def job_progress(job_id: str, payload: JobProgress):
    def mutator(state):
        jobs = state.get("jobs") or []
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return None
        progress = job.get("progress") or {}
        if payload.pct is not None:
            if math.isfinite(payload.pct):
                progress["pct"] = payload.pct
        if payload.etaSec is not None:
            progress["etaSec"] = payload.etaSec
        if payload.logTail is not None:
            log_tail = str(payload.logTail)
            if len(log_tail) > 200:
                log_tail = log_tail[:200] + "..."
            progress["logTail"] = log_tail
        job["progress"] = progress
        job["lastUpdateAt"] = now_iso()
        return job

    job = update_state(mutator)
    if not job:
        return Response(status_code=204)
    return JSONResponse(job)


@app.post("/api/jobs/{job_id}/complete")
def job_complete(job_id: str, payload: JobUpdate):
    def mutator(state):
        jobs = state.get("jobs") or []
        items = state.get("items") or []
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        item = next((i for i in items if i.get("id") == job.get("itemId")), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        config = state.get("config") or {}
        job["status"] = "done"
        job["finishedAt"] = now_iso()
        job["lastUpdateAt"] = now_iso()
        item["status"] = "done"
        item["ready"] = False
        item["lastError"] = ""
        item["lastTranscodeAt"] = now_iso()
        item["transcodeCount"] = int(item.get("transcodeCount") or 0) + 1
        refresh_item_after_transcode(item, config, payload.outputSizeBytes)
        return job

    job = update_state(mutator)
    return JSONResponse(job)


@app.post("/api/jobs/{job_id}/fail")
def job_fail(job_id: str, payload: JobUpdate):
    def mutator(state):
        jobs = state.get("jobs") or []
        items = state.get("items") or []
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        item = next((i for i in items if i.get("id") == job.get("itemId")), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        job["status"] = "failed"
        job["finishedAt"] = now_iso()
        job["error"] = payload.error or ""
        job["lastUpdateAt"] = now_iso()

        item["status"] = "failed"
        item["lastError"] = payload.error or ""
        item["ready"] = False
        return job

    job = update_state(mutator)
    return JSONResponse(job)


if __name__ == "__main__":
    import uvicorn
    host = "0.0.0.0"
    port = 8856
    log("Server starting...")
    log(f"UI listening on http://{host}:{port}")
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=True,
        log_level="warning",
        access_log=False,
    )
