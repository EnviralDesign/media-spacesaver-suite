import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATUS_PATH = BASE_DIR / "status.json"

DEFAULT_CONFIG = {
    "serverUrl": "http://127.0.0.1:8856",
    "name": "worker-1",
    "workerId": "",
    "cacheDir": str(BASE_DIR / "cache"),
    "handbrakePath": "",
    "workHours": [],
    "pollIntervalSec": 10,
    "ffmpegPath": "",
}


class ConfigRequest(BaseModel):
    serverUrl: str | None = None
    name: str | None = None
    workerId: str | None = None
    cacheDir: str | None = None
    handbrakePath: str | None = None
    workHours: list | None = None
    pollIntervalSec: int | None = None
    ffmpegPath: str | None = None


def resolve_handbrake(config):
    explicit = config.get("handbrakePath") or os.environ.get("HANDBRAKECLI_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        return None

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


def resolve_ffmpeg(config):
    explicit = config.get("ffmpegPath") or os.environ.get("FFMPEG_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        if path.exists():
            return str(path)
        return None
    path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if path:
        return path
    return None


app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
def get_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return JSONResponse(DEFAULT_CONFIG)
    return JSONResponse(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


@app.post("/api/config")
def set_config(payload: ConfigRequest):
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    for key, value in payload.model_dump().items():
        if value is not None:
            config[key] = value

    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return JSONResponse(config)


@app.get("/api/status")
def get_status():
    if not STATUS_PATH.exists():
        return JSONResponse({"state": "idle", "jobId": None, "lastError": ""})
    try:
        return JSONResponse(json.loads(STATUS_PATH.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return JSONResponse({"state": "idle", "jobId": None, "lastError": ""})


@app.get("/api/diagnostics")
def diagnostics():
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    handbrake = resolve_handbrake(config)
    ffmpeg = resolve_ffmpeg(config)
    return JSONResponse(
        {
            "handbrake": {
                "found": bool(handbrake),
                "path": handbrake or "",
            }
            ,
            "ffmpeg": {
                "found": bool(ffmpeg),
                "path": ffmpeg or "",
            }
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ui:app", host="0.0.0.0", port=8857, reload=True)
