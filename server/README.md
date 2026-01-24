# Server

Local-only FastAPI server that owns state and queues jobs for workers.

## Run

Python env is managed with `uv` (required).

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
python app.py
```

Server listens on `http://0.0.0.0:8856` (LAN accessible).

## Dev run (with reload)

```powershell
uvicorn app:app --reload --host 0.0.0.0 --port 8856
```

## Notes

- UI is served at the root URL.
- State is stored at `data/state.json`.
- ffprobe is used during scans if available on PATH (or `FFPROBE_PATH`).
