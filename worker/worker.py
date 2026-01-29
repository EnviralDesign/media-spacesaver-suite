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
from queue import Queue, Empty

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

DEFAULT_CONFIG = {
    "serverUrl": "http://127.0.0.1:8856",
    "workerId": "",
    "cacheDir": str(Path(__file__).resolve().parent / "cache"),
    "handbrakePath": "",
    "workHours": [],
    "pollIntervalSec": 10,
    "uiEnabled": True,
    "uiHost": "0.0.0.0",
    "uiPort": 8857,
    "ffmpegPath": "",
}

STATUS_PATH = Path(__file__).resolve().parent / "status.json"


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def load_config(path, write_back=True):
    if not Path(path).exists():
        config = DEFAULT_CONFIG.copy()
        config = _ensure_worker_identity(config)
        config = _ensure_handbrake_path(config)
        if write_back:
            save_config(path, config)
        return config
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(data or {})
    merged = _ensure_worker_identity(merged)
    merged = _ensure_handbrake_path(merged)
    if write_back:
        save_config(path, merged)
    return merged


def save_config(path, config):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _config_mtime_ns(path):
    try:
        return Path(path).stat().st_mtime_ns
    except FileNotFoundError:
        return None


def reload_config_if_changed(path, config, last_mtime_ns):
    current_mtime_ns = _config_mtime_ns(path)
    if current_mtime_ns is None:
        return config, last_mtime_ns, False
    if last_mtime_ns is not None and current_mtime_ns == last_mtime_ns:
        return config, last_mtime_ns, False
    try:
        updated = load_config(path, write_back=False)
    except Exception as exc:
        log(f"Config reload failed: {exc}")
        return config, last_mtime_ns, False
    return updated, current_mtime_ns, True


def format_work_hours(work_hours):
    if not work_hours:
        return "24/7"
    pieces = []
    for block in work_hours:
        if not isinstance(block, dict):
            continue
        start = block.get("start")
        end = block.get("end")
        if start and end:
            pieces.append(f"{start}-{end}")
    return ", ".join(pieces) if pieces else "24/7"


def _ensure_worker_identity(config):
    if not config.get("workerId"):
        hostname = socket.gethostname().strip().lower()
        safe_host = re.sub(r"[^a-z0-9]+", "-", hostname).strip("-") or "host"
        config["workerId"] = f"wrk_{safe_host}"
    config["name"] = config["workerId"]
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


def _find_ffmpeg():
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if path:
        return path
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
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


def ffmpeg_path(config=None):
    explicit = None
    if config:
        explicit = config.get("ffmpegPath")
    env_path = os.environ.get("FFMPEG_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    if env_path and Path(env_path).exists():
        return env_path
    path = _find_ffmpeg()
    if not path:
        raise RuntimeError("ffmpeg not found on PATH. Set FFMPEG_PATH or install ffmpeg.")
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


def ensure_mkv_extension(path: Path):
    if path.suffix.lower() == ".mkv":
        return path
    return path.with_suffix(".mkv")


def remux_with_metadata(src_path: Path, ffmpeg, metadata):
    temp_path = src_path.with_suffix(src_path.suffix + ".meta.mkv")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_path),
        "-map",
        "0",
        "-c",
        "copy",
    ]
    for key, value in metadata.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(temp_path))
    run_cmd = subprocess.run(cmd, capture_output=True, text=True)
    if run_cmd.returncode != 0:
        detail = (run_cmd.stderr or run_cmd.stdout or "ffmpeg remux failed").strip()
        raise RuntimeError(detail)
    os.replace(temp_path, src_path)


def update_item_path(server_url, item_id, new_path):
    try:
        requests.post(
            f"{server_url}/api/items/{item_id}/path",
            json={"path": new_path},
            timeout=30,
        )
    except requests.RequestException:
        pass


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


def heartbeat(server_url, worker_id, worker_name, work_hours=None):
    if not worker_id:
        return
    payload = {
        "workerId": worker_id,
        "workerName": worker_name,
        "workHours": work_hours or [],
        "withinWorkHours": within_work_hours(work_hours),
    }
    try:
        requests.post(f"{server_url}/api/workers/heartbeat", json=payload, timeout=30)
    except requests.RequestException:
        pass


def heartbeat_loop(runtime, interval_sec=10):
    while True:
        server_url = runtime.get("server_url")
        worker_id = runtime.get("worker_id")
        worker_name = runtime.get("worker_name")
        work_hours = runtime.get("work_hours")
        if server_url:
            heartbeat(server_url, worker_id, worker_name, work_hours)
        time.sleep(interval_sec)


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
    try:
        post_job_update(server_url, job_id, "progress", payload)
    except requests.RequestException as exc:
        log(f"Progress update failed: {exc}")


def cancel_requested(server_url, job_id):
    try:
        resp = requests.get(f"{server_url}/api/jobs/{job_id}", timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("cancelRequested"))
    except requests.RequestException:
        return False


def terminate_process(proc):
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
        return
    except Exception:
        pass

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
    else:
        try:
            proc.kill()
        except Exception:
            pass


def copy_with_cancel(src, dst, server_url, job_id, label, pct_start=None, pct_span=None):
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    total = src_path.stat().st_size if src_path.exists() else 0
    copied = 0
    chunk_size = 8 * 1024 * 1024
    last_update = 0.0
    start_time = time.time()

    with src_path.open("rb") as r, dst_path.open("wb") as w:
        while True:
            chunk = r.read(chunk_size)
            if not chunk:
                break
            w.write(chunk)
            copied += len(chunk)
            now = time.time()
            if total > 0 and now - last_update > 0.5:
                pct = (copied / total) * 100
                msg = f"{label} {pct:.1f}%"
                elapsed = max(0.001, now - start_time)
                rate = copied / elapsed
                eta_sec = int((total - copied) / rate) if rate > 0 else None
                if pct_start is not None and pct_span is not None:
                    overall = pct_start + (pct / 100.0) * pct_span
                    post_job_progress(server_url, job_id, pct=round(overall, 1), eta_sec=eta_sec, log_tail=msg)
                    write_status(
                        "working",
                        job_id=job_id,
                        error=None,
                        progress_pct=round(overall, 1),
                        progress_message=msg,
                        progress_eta_sec=eta_sec,
                    )
                else:
                    post_job_progress(server_url, job_id, eta_sec=eta_sec, log_tail=msg)
                    write_status(
                        "working",
                        job_id=job_id,
                        error=None,
                        progress_pct=None,
                        progress_message=msg,
                        progress_eta_sec=eta_sec,
                    )
                last_update = now
            if cancel_requested(server_url, job_id):
                raise RuntimeError("Cancelled by user")

    try:
        shutil.copystat(src_path, dst_path)
    except OSError:
        pass

    if total and copied < total:
        raise RuntimeError(f"Copy incomplete ({label})")


def clean_cache_dir(cache_dir):
    if not cache_dir.exists():
        return
    for path in cache_dir.iterdir():
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _tail_text(lines, limit=2000):
    joined = "\n".join(lines).strip()
    if len(joined) <= limit:
        return joined
    return joined[-limit:]


def parse_eta_seconds(text):
    if not text:
        return None
    match = re.search(r"ETA\s+(\d{1,2}):(\d{2}):(\d{2})", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    match = re.search(r"ETA\s+(\d{1,2})h(\d{1,2})m(?:([0-9]{1,2})s)?", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    match = re.search(r"ETA\s+(\d{1,2})m([0-9]{1,2})s", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return minutes * 60 + seconds

    return None


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
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if proc.stdout is None:
        raise RuntimeError("Failed to capture HandBrakeCLI output.")

    queue = Queue()
    sentinel = object()

    def reader():
        try:
            for raw_line in proc.stdout:
                queue.put(raw_line)
        except Exception as exc:
            queue.put(f"[reader error] {exc}")
        finally:
            queue.put(sentinel)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    reader_done = False
    while True:
        try:
            raw_line = queue.get(timeout=0.2)
        except Empty:
            raw_line = None

        if raw_line is sentinel:
            reader_done = True
            raw_line = None

        if raw_line:
            line = (raw_line or "").strip()
            if line:
                last_lines.append(line)

            pct = None
            eta_sec = None
            if "Encoding" in line and "%" in line:
                match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%", line)
                if match:
                    try:
                        pct = float(match.group(1))
                    except ValueError:
                        pct = None
                eta_sec = parse_eta_seconds(line)

            now = time.time()
            if pct is not None:
                if last_pct is None or abs(pct - last_pct) >= 0.5 or now - last_update > 2:
                    msg = f"Encoding {pct:.1f}%"
                    post_job_progress(server_url, job_id, pct=round(pct, 1), eta_sec=eta_sec, log_tail=msg)
                    if progress_cb:
                        progress_cb(round(pct, 1), msg, eta_sec)
                    last_pct = pct
                    last_update = now
            elif line and now - last_update > 5:
                post_job_progress(server_url, job_id, log_tail=line)
                if progress_cb:
                    progress_cb(None, line, None)
                last_update = now

        now = time.time()
        if now - last_cancel_check > 1:
            last_cancel_check = now
            if cancel_requested(server_url, job_id):
                terminate_process(proc)
                raise RuntimeError("Cancelled by user")

        if proc.poll() is not None and reader_done and queue.empty():
            break

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
    clean_cache_dir(cache_dir)

    input_suffix = input_path.suffix
    output_ext = detect_extension(args_list, input_suffix)

    local_in = cache_dir / f"{job['id']}_src{input_suffix}"
    local_out = cache_dir / f"{job['id']}_out{output_ext}"

    post_job_progress(config["serverUrl"], job["id"], pct=5, log_tail="Copying source to cache")
    write_status("working", job_id=job["id"], error=None, progress_pct=5, progress_message="Copying source to cache")
    if cancel_requested(config["serverUrl"], job["id"]):
        raise RuntimeError("Cancelled by user")
    try:
        copy_with_cancel(input_path, local_in, config["serverUrl"], job["id"], "Copying source", pct_start=2, pct_span=10)
    except Exception:
        if local_in.exists():
            try:
                local_in.unlink()
            except OSError:
                pass
        raise

    cmd = [
        handbrake_path(config),
        "-i",
        str(local_in),
        "-o",
        str(local_out),
    ] + args_list

    post_job_progress(config["serverUrl"], job["id"], pct=15, log_tail="Encoding")
    write_status("working", job_id=job["id"], error=None, progress_pct=15, progress_message="Encoding")

    def local_progress(pct, message, eta_sec=None):
        write_status(
            "working",
            job_id=job["id"],
            error=None,
            progress_pct=pct,
            progress_message=message,
            progress_eta_sec=eta_sec,
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

    # Standardize on MKV output path
    dest_path = ensure_mkv_extension(input_path)

    post_job_progress(config["serverUrl"], job["id"], pct=85, log_tail="Copying output to source")
    write_status("working", job_id=job["id"], error=None, progress_pct=85, progress_message="Copying output to source")
    dest_tmp = dest_path.with_suffix(dest_path.suffix + ".tmp")
    if dest_tmp.exists():
        dest_tmp.unlink()
    try:
        copy_with_cancel(local_out, dest_tmp, config["serverUrl"], job["id"], "Copying output", pct_start=85, pct_span=10)
    except Exception:
        if dest_tmp.exists():
            try:
                dest_tmp.unlink()
            except OSError:
                pass
        raise
    if cancel_requested(config["serverUrl"], job["id"]):
        if dest_tmp.exists():
            try:
                dest_tmp.unlink()
            except OSError:
                pass
        raise RuntimeError("Cancelled by user")
    try:
        os.replace(dest_tmp, dest_path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Replace failed: {dest_tmp} -> {dest_path}") from exc

    # Add metadata tag (ffmpeg remux)
    try:
        post_job_progress(config["serverUrl"], job["id"], pct=96, log_tail="Tagging metadata")
        write_status("working", job_id=job["id"], error=None, progress_pct=96, progress_message="Tagging metadata")
        remux_with_metadata(
            dest_path,
            ffmpeg_path(config),
            {
                "encoded_by": "MediaSpacesaver",
                "comment": "spacesaver=1",
            },
        )
    except Exception as exc:
        raise RuntimeError(f"Metadata tagging failed: {exc}") from exc

    # If extension changed, remove original and update server path
    if dest_path != input_path:
        try:
            if input_path.exists():
                input_path.unlink()
        except OSError:
            pass
        update_item_path(config["serverUrl"], item.get("id"), str(dest_path))

    output_size = dest_path.stat().st_size if dest_path.exists() else None

    for path in (local_in, local_out):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    post_job_progress(config["serverUrl"], job["id"], pct=100, log_tail="Done")
    write_status("working", job_id=job["id"], error=None, progress_pct=100, progress_message="Done")

    return output_size


def write_status(state, job_id=None, error=None, progress_pct=None, progress_message=None, progress_eta_sec=None):
    payload = {
        "state": state,
        "jobId": job_id,
        "lastError": error or "",
        "progressPct": progress_pct,
        "progressMessage": progress_message or "",
        "progressEtaSec": progress_eta_sec,
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
    parser.add_argument("--ui", action="store_true", help="Force enable UI")
    parser.add_argument("--no-ui", action="store_true", help="Disable UI")
    parser.add_argument("--ui-host", default=None)
    parser.add_argument("--ui-port", type=int, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.server:
        config["serverUrl"] = args.server
    # Always derive name from workerId
    config["name"] = config.get("workerId") or config.get("name")
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
    worker_name = config.get("workerId") or config.get("name")
    worker_id = config.get("workerId") or None
    runtime = {
        "server_url": server_url,
        "worker_name": worker_name,
        "worker_id": worker_id,
        "work_hours": config.get("workHours"),
    }
    last_config_mtime_ns = _config_mtime_ns(args.config)
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
    if worker_id:
        threading.Thread(
            target=heartbeat_loop,
            args=(runtime, 10),
            daemon=True,
        ).start()

    while True:
        config, last_config_mtime_ns, changed = reload_config_if_changed(args.config, config, last_config_mtime_ns)
        if changed:
            server_url = config["serverUrl"]
            worker_name = config.get("workerId") or config.get("name")
            worker_id = config.get("workerId") or None
            runtime["server_url"] = server_url
            runtime["worker_name"] = worker_name
            runtime["worker_id"] = worker_id
            runtime["work_hours"] = config.get("workHours")
            log(
                "Config reloaded: "
                f"workHours={format_work_hours(config.get('workHours'))}, "
                f"pollIntervalSec={config.get('pollIntervalSec', 10)}"
            )

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
