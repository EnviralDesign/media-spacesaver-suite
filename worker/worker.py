import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
import socket
from datetime import datetime
from pathlib import Path
from collections import deque

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

DEFAULT_CONFIG = {
    "serverUrl": "http://127.0.0.1:8856",
    "name": "worker-1",
    "workerId": "",
    "cacheDir": str(Path(__file__).resolve().parent / "cache"),
    "handbrakePath": "",
    "workHours": [],
    "pollIntervalSec": 10,
    "uiEnabled": True,
    "uiHost": "0.0.0.0",
    "uiPort": 8857,
}

STATUS_PATH = Path(__file__).resolve().parent / "status.json"


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def load_config(path):
    if not Path(path).exists():
        config = DEFAULT_CONFIG.copy()
        config = _ensure_worker_identity(config)
        config = _ensure_handbrake_path(config)
        save_config(path, config)
        return config
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(data or {})
    merged = _ensure_worker_identity(merged)
    merged = _ensure_handbrake_path(merged)
    save_config(path, merged)
    return merged


def save_config(path, config):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _ensure_worker_identity(config):
    if not config.get("workerId"):
        hostname = socket.gethostname().strip().lower()
        safe_host = re.sub(r"[^a-z0-9]+", "-", hostname).strip("-") or "host"
        config["workerId"] = f"wrk_{safe_host}"
    return config


def _find_handbrake():
    env_path = os.environ.get("HANDBRAKECLI_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    path = shutil.which("HandBrakeCLI") or shutil.which("HandBrakeCLI.exe")
    if path:
        return path

    candidates = [
        r"C:\Program Files\HandBrake\HandBrakeCLI.exe",
        r"C:\Program Files (x86)\HandBrake\HandBrakeCLI.exe",
        "/usr/local/bin/HandBrakeCLI",
        "/usr/bin/HandBrakeCLI",
        "/Applications/HandBrakeCLI",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _ensure_handbrake_path(config):
    if config.get("handbrakePath"):
        return config
    found = _find_handbrake()
    if found:
        config["handbrakePath"] = found
        log(f"Detected HandBrakeCLI at {found}")
    return config


def handbrake_path(config):
    explicit = config.get("handbrakePath") or os.environ.get("HANDBRAKECLI_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    path = _find_handbrake()
    if not path:
        raise RuntimeError("HandBrakeCLI not found on PATH. Set handbrakePath in config.json or HANDBRAKECLI_PATH.")
    return path


def split_args(args_str):
    return shlex.split(args_str or "", posix=False)


def detect_extension(args_list, default_ext):
    for i, arg in enumerate(args_list):
        if arg in {"-f", "--format"} and i + 1 < len(args_list):
            fmt = args_list[i + 1].lower()
            if "mkv" in fmt:
                return ".mkv"
            if "mp4" in fmt:
                return ".mp4"
    return default_ext


def within_work_hours(work_hours):
    if not work_hours:
        return True

    now = datetime.now()
    current = now.hour * 60 + now.minute

    for block in work_hours:
        start = block.get("start")
        end = block.get("end")
        if not start or not end:
            continue
        try:
            start_h, start_m = [int(x) for x in start.split(":")]
            end_h, end_m = [int(x) for x in end.split(":")]
        except ValueError:
            continue
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m

        if start_min <= end_min:
            if start_min <= current <= end_min:
                return True
        else:
            if current >= start_min or current <= end_min:
                return True

    return False


def claim_job(server_url, worker_name, worker_id):
    payload = {"workerName": worker_name}
    if worker_id:
        payload["workerId"] = worker_id
    resp = requests.post(f"{server_url}/api/jobs/claim", json=payload, timeout=30)
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def heartbeat(server_url, worker_id, worker_name):
    if not worker_id:
        return
    payload = {"workerId": worker_id, "workerName": worker_name}
    try:
        requests.post(f"{server_url}/api/workers/heartbeat", json=payload, timeout=30)
    except requests.RequestException:
        pass


def post_job_update(server_url, job_id, endpoint, payload=None):
    url = f"{server_url}/api/jobs/{job_id}/{endpoint}"
    resp = requests.post(url, json=payload or {}, timeout=30)
    resp.raise_for_status()


def post_job_progress(server_url, job_id, pct=None, eta_sec=None, log_tail=None):
    payload = {}
    if pct is not None:
        payload["pct"] = pct
    if eta_sec is not None:
        payload["etaSec"] = eta_sec
    if log_tail is not None:
        payload["logTail"] = log_tail
    if not payload:
        return
    post_job_update(server_url, job_id, "progress", payload)


def cancel_requested(server_url, job_id):
    try:
        resp = requests.get(f"{server_url}/api/jobs/{job_id}", timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("cancelRequested"))
    except requests.RequestException:
        return False

def _tail_text(lines, limit=2000):
    joined = "\n".join(lines).strip()
    if len(joined) <= limit:
        return joined
    return joined[-limit:]


def run_handbrake(cmd, server_url, job_id, progress_cb=None):
    last_lines = deque(maxlen=25)
    last_pct = None
    last_update = 0.0
    last_cancel_check = 0.0

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if proc.stdout is None:
        raise RuntimeError("Failed to capture HandBrakeCLI output.")

    for raw_line in proc.stdout:
        line = (raw_line or "").strip()
        if line:
            last_lines.append(line)

        pct = None
        if "Encoding" in line and "%" in line:
            match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%", line)
            if match:
                try:
                    pct = float(match.group(1))
                except ValueError:
                    pct = None

        now = time.time()
        if pct is not None:
            if last_pct is None or abs(pct - last_pct) >= 0.5 or now - last_update > 2:
                post_job_progress(server_url, job_id, pct=round(pct, 1), log_tail=line)
                if progress_cb:
                    progress_cb(round(pct, 1), line)
                last_pct = pct
                last_update = now
        elif line and now - last_update > 5:
            post_job_progress(server_url, job_id, log_tail=line)
            if progress_cb:
                progress_cb(None, line)
            last_update = now

        if now - last_cancel_check > 2:
            last_cancel_check = now
            if cancel_requested(server_url, job_id):
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise RuntimeError("Cancelled by user")

    proc.wait()

    tail = _tail_text(last_lines)
    if proc.returncode != 0:
        raise RuntimeError(tail or "HandBrakeCLI failed")
    return tail


def process_job(job_payload, config):
    job = job_payload["job"]
    item = job_payload["item"]
    args_str = job_payload.get("args") or ""
    args_list = split_args(args_str)

    input_path = Path(item["path"])
    if not input_path.exists():
        raise RuntimeError(f"Input missing: {input_path}")

    cache_dir = Path(config["cacheDir"])
    cache_dir.mkdir(parents=True, exist_ok=True)

    input_suffix = input_path.suffix
    output_ext = detect_extension(args_list, input_suffix)

    local_in = cache_dir / f"{job['id']}_src{input_suffix}"
    local_out = cache_dir / f"{job['id']}_out{output_ext}"

    post_job_progress(config["serverUrl"], job["id"], pct=5, log_tail="Copying source to cache")
    write_status("working", job_id=job["id"], error=None, progress_pct=5, progress_message="Copying source to cache")
    if cancel_requested(config["serverUrl"], job["id"]):
        raise RuntimeError("Cancelled by user")
    shutil.copy2(input_path, local_in)

    cmd = [
        handbrake_path(config),
        "-i",
        str(local_in),
        "-o",
        str(local_out),
    ] + args_list

    post_job_progress(config["serverUrl"], job["id"], pct=15, log_tail="Encoding")
    write_status("working", job_id=job["id"], error=None, progress_pct=15, progress_message="Encoding")

    def local_progress(pct, message):
        write_status(
            "working",
            job_id=job["id"],
            error=None,
            progress_pct=pct,
            progress_message=message,
        )

    tail = run_handbrake(cmd, config["serverUrl"], job["id"], progress_cb=local_progress)

    if cancel_requested(config["serverUrl"], job["id"]):
        raise RuntimeError("Cancelled by user")

    if not local_out.exists():
        matches = list(cache_dir.glob(f"{job['id']}_out*"))
        if len(matches) == 1:
            local_out = matches[0]
        else:
            detail = _tail_text([tail]) if tail else ""
            if detail:
                raise RuntimeError(f"Output missing after encode: {local_out} | {detail}")
            raise RuntimeError(f"Output missing after encode: {local_out}")

    post_job_progress(config["serverUrl"], job["id"], pct=85, log_tail="Copying output to source")
    write_status("working", job_id=job["id"], error=None, progress_pct=85, progress_message="Copying output to source")
    dest_tmp = input_path.with_suffix(input_path.suffix + ".tmp")
    if dest_tmp.exists():
        dest_tmp.unlink()
    try:
        shutil.copy2(local_out, dest_tmp)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Copy failed: {local_out} -> {dest_tmp}") from exc
    try:
        os.replace(dest_tmp, input_path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Replace failed: {dest_tmp} -> {input_path}") from exc

    output_size = local_out.stat().st_size if local_out.exists() else None

    try:
        local_in.unlink()
    except OSError:
        pass
    try:
        local_out.unlink()
    except OSError:
        pass

    post_job_progress(config["serverUrl"], job["id"], pct=100, log_tail="Done")
    write_status("working", job_id=job["id"], error=None, progress_pct=100, progress_message="Done")

    return output_size


def write_status(state, job_id=None, error=None, progress_pct=None, progress_message=None):
    payload = {
        "state": state,
        "jobId": job_id,
        "lastError": error or "",
        "progressPct": progress_pct,
        "progressMessage": progress_message or "",
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def start_ui_server(host, port):
    try:
        import uvicorn
        from ui import app as ui_app
    except Exception as exc:
        log(f"UI disabled (import error): {exc}")
        return None

    config = uvicorn.Config(ui_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    log(f"UI listening on http://{host}:{port}")
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--server", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--ui", action="store_true", help="Force enable UI")
    parser.add_argument("--no-ui", action="store_true", help="Disable UI")
    parser.add_argument("--ui-host", default=None)
    parser.add_argument("--ui-port", type=int, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.server:
        config["serverUrl"] = args.server
    if args.name:
        config["name"] = args.name
    if args.ui_host:
        config["uiHost"] = args.ui_host
    if args.ui_port:
        config["uiPort"] = args.ui_port

    ui_enabled = config.get("uiEnabled", True)
    if args.ui:
        ui_enabled = True
    if args.no_ui:
        ui_enabled = False

    server_url = config["serverUrl"]
    worker_name = config["name"]
    worker_id = config.get("workerId") or None
    last_state = None

    log(f"Worker starting: {worker_name} ({worker_id}) -> {server_url}")
    try:
        hb_path = handbrake_path(config)
        log(f"HandBrakeCLI: {hb_path}")
    except Exception as exc:
        log(f"WARNING: {exc}")
        write_status("idle", job_id=None, error=str(exc))
    if ui_enabled:
        start_ui_server(config.get("uiHost", "127.0.0.1"), int(config.get("uiPort", 8857)))

    while True:
        if not within_work_hours(config.get("workHours")):
            write_status("idle")
            if last_state != "off-hours":
                log("Off-hours, waiting...")
                last_state = "off-hours"
            time.sleep(config.get("pollIntervalSec", 10))
            continue

        try:
            heartbeat(server_url, worker_id, worker_name)
            job_payload = claim_job(server_url, worker_name, worker_id)
            if not job_payload:
                write_status("idle")
                if last_state != "idle":
                    log("Idle, waiting for jobs")
                    last_state = "idle"
                if args.once:
                    return
                time.sleep(config.get("pollIntervalSec", 10))
                continue

            worker_id = job_payload.get("job", {}).get("workerId") or worker_id
            job_id = job_payload["job"]["id"]
            item_path = job_payload.get("item", {}).get("path")
            log(f"Claimed job {job_id} -> {item_path}")
            post_job_update(server_url, job_id, "start")
            write_status("working", job_id=job_id)
            last_state = "working"

            output_size = process_job(job_payload, config)

            post_job_update(server_url, job_id, "complete", {"outputSizeBytes": output_size})
            write_status("idle", job_id=None, error=None)
            log(f"Completed job {job_id} (output {output_size} bytes)")
        except Exception as exc:
            if "job_id" in locals():
                try:
                    post_job_update(server_url, job_id, "fail", {"error": str(exc)})
                except Exception:
                    pass
            write_status("idle", job_id=None, error=str(exc))
            log(f"Job failed: {exc}")
            if args.once:
                raise
            time.sleep(config.get("pollIntervalSec", 10))


if __name__ == "__main__":
    main()
