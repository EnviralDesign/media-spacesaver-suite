import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config.json"
CACHE_DIR = ROOT / "data" / "compare_cache"

STATE = {
    "selected_path": None,
    "selected_name": None,
    "sample_original_path": None,
    "encoded_path": None,
    "busy": False,
    "last_error": None,
}
STATE_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "baselineArgs": "-f av_mkv -e x265_10bit --encoder-preset slow -q 20 --audio-lang-list eng --first-audio -E copy --subtitle-lang-list eng --first-subtitle",
    "sampleSeconds": 10,
    "handbrakePath": "",
    "ffmpegPath": "",
    "serverUrl": "http://127.0.0.1:8856",
}


def load_config():
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(data or {})
    return merged


def save_config(config):
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def split_args(args_str):
    return shlex.split(args_str or "", posix=False)


def validate_args(args_list):
    forbidden = {"-i", "--input", "-o", "--output"}
    for arg in args_list:
        if arg in forbidden:
            raise HTTPException(status_code=400, detail="baseline args must not include -i/--input or -o/--output")


def detect_extension(args_list):
    for i, arg in enumerate(args_list):
        if arg in {"-f", "--format"} and i + 1 < len(args_list):
            fmt = args_list[i + 1].lower()
            if "mkv" in fmt:
                return ".mkv"
            if "mp4" in fmt:
                return ".mp4"
    return ".mp4"


def ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def handbrake_path(config):
    explicit = (config or {}).get("handbrakePath") or os.environ.get("HANDBRAKECLI_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        raise HTTPException(status_code=500, detail=f"HandBrakeCLI not found at {path}")
    path = shutil.which("HandBrakeCLI")
    if not path:
        raise HTTPException(status_code=500, detail="HandBrakeCLI not found on PATH. Set handbrakePath in config.json or HANDBRAKECLI_PATH.")
    return path


def resolve_handbrake(config):
    explicit = (config or {}).get("handbrakePath") or os.environ.get("HANDBRAKECLI_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        return None
    return shutil.which("HandBrakeCLI") or shutil.which("HandBrakeCLI.exe")


def ffmpeg_path(config):
    explicit = (config or {}).get("ffmpegPath") or os.environ.get("FFMPEG_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        raise HTTPException(status_code=500, detail=f"ffmpeg not found at {path}")
    path = shutil.which("ffmpeg")
    if not path:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg not found on PATH. Set ffmpegPath in config.json or FFMPEG_PATH.",
        )
    return path


def resolve_ffmpeg(config):
    explicit = (config or {}).get("ffmpegPath") or os.environ.get("FFMPEG_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        return None
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def run_cmd(cmd, default_error="Command failed"):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or default_error
        if len(detail) > 2000:
            detail = detail[:2000] + "..."
        raise RuntimeError(detail)


class SampleRequest(BaseModel):
    timestampSec: float


class ConfigRequest(BaseModel):
    baselineArgs: str | None = None
    serverUrl: str | None = None


app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config():
    return JSONResponse(load_config())


@app.get("/api/diagnostics")
def diagnostics():
    config = load_config()
    handbrake = resolve_handbrake(config)
    ffmpeg = resolve_ffmpeg(config)
    return JSONResponse(
        {
            "handbrake": {"found": bool(handbrake), "path": handbrake or ""},
            "ffmpeg": {"found": bool(ffmpeg), "path": ffmpeg or ""},
        }
    )


@app.post("/api/config")
def set_config(payload: ConfigRequest):
    config = load_config()
    if payload.baselineArgs is not None:
        args_list = split_args(payload.baselineArgs)
        validate_args(args_list)
        config["baselineArgs"] = payload.baselineArgs
    if payload.serverUrl is not None:
        config["serverUrl"] = payload.serverUrl
    save_config(config)
    return JSONResponse(config)


@app.post("/api/select-file")
def select_file():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"tkinter not available: {exc}")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select a video file",
        filetypes=[
            ("Video files", "*.mkv;*.mp4;*.mov;*.m4v;*.avi"),
            ("All files", "*.*"),
        ],
    )
    root.destroy()

    if not path:
        return JSONResponse({"selected": False})

    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    with STATE_LOCK:
        STATE["selected_path"] = str(file_path)
        STATE["selected_name"] = file_path.name
        STATE["sample_original_path"] = None
        STATE["encoded_path"] = None
        STATE["last_error"] = None

    return JSONResponse(
        {
            "selected": True,
            "name": file_path.name,
            "path": str(file_path),
            "sizeBytes": file_path.stat().st_size,
        }
    )


@app.get("/api/state")
def get_state():
    with STATE_LOCK:
        return JSONResponse(
            {
                "selected": STATE["selected_path"] is not None,
                "name": STATE["selected_name"],
                "encodedReady": STATE["encoded_path"] is not None,
                "sampleReady": STATE["sample_original_path"] is not None,
                "lastError": STATE["last_error"],
            }
        )


@app.post("/api/sample")
def sample(payload: SampleRequest):
    with STATE_LOCK:
        if STATE["busy"]:
            raise HTTPException(status_code=409, detail="Sample already in progress")
        if not STATE["selected_path"]:
            raise HTTPException(status_code=400, detail="No file selected")
        STATE["busy"] = True
        STATE["last_error"] = None

    try:
        config = load_config()
        args_list = split_args(config.get("baselineArgs", ""))
        validate_args(args_list)

        start_sec = max(0, int(payload.timestampSec))
        duration_sec = int(config.get("sampleSeconds", 10))

        ensure_cache_dir()
        safe_stem = Path(STATE["selected_name"]).stem
        stamp = int(time.time())
        input_suffix = Path(STATE["selected_path"]).suffix or ".mkv"
        source_path = CACHE_DIR / f"{safe_stem}_src_{stamp}{input_suffix}"
        output_ext = detect_extension(args_list)
        output_path = CACHE_DIR / f"{safe_stem}_enc_{stamp}{output_ext}"

        ff_cmd = [
            ffmpeg_path(config),
            "-hide_banner",
            "-y",
            "-ss",
            str(start_sec),
            "-i",
            STATE["selected_path"],
            "-t",
            str(duration_sec),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(source_path),
        ]
        print("ffmpeg:", subprocess.list2cmdline(ff_cmd))
        run_cmd(ff_cmd, "ffmpeg failed")

        hb_cmd = [
            handbrake_path(config),
            "-i",
            str(source_path),
            "-o",
            str(output_path),
        ] + args_list

        print("HandBrakeCLI:", subprocess.list2cmdline(hb_cmd))
        run_cmd(hb_cmd, "HandBrakeCLI failed")

        with STATE_LOCK:
            STATE["sample_original_path"] = str(source_path)
            STATE["encoded_path"] = str(output_path)

        source_size = source_path.stat().st_size if source_path.exists() else 0
        encoded_size = output_path.stat().st_size if output_path.exists() else 0

        return JSONResponse(
            {
                "ok": True,
                "encodedPath": str(output_path),
                "sampleStart": start_sec,
                "sampleDuration": duration_sec,
                "sourceSizeBytes": source_size,
                "encodedSizeBytes": encoded_size,
            }
        )
    except Exception as exc:
        with STATE_LOCK:
            STATE["last_error"] = str(exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        with STATE_LOCK:
            STATE["busy"] = False


def range_response(path: Path, request: Request):
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(path, media_type=content_type)

    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        return FileResponse(path, media_type=content_type)

    start = int(match.group(1)) if match.group(1) else 0
    end = int(match.group(2)) if match.group(2) else file_size - 1
    end = min(end, file_size - 1)
    length = end - start + 1

    def file_iter():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 1024 * 1024
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }

    return StreamingResponse(file_iter(), status_code=206, headers=headers, media_type=content_type)


@app.get("/media/source")
def media_source(request: Request):
    with STATE_LOCK:
        if not STATE["selected_path"]:
            raise HTTPException(status_code=404, detail="No file selected")
        path = Path(STATE["selected_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original file missing")
    return range_response(path, request)


@app.get("/media/original")
def media_original(request: Request):
    with STATE_LOCK:
        if not STATE["sample_original_path"]:
            raise HTTPException(status_code=404, detail="No sample clip")
        path = Path(STATE["sample_original_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sample clip missing")
    return range_response(path, request)


@app.get("/media/encoded")
def media_encoded(request: Request):
    with STATE_LOCK:
        if not STATE["encoded_path"]:
            raise HTTPException(status_code=404, detail="No encoded sample")
        path = Path(STATE["encoded_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Encoded file missing")
    return range_response(path, request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8855, reload=True)
