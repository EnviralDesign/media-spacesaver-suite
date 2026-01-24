# Worker

Polling worker that claims jobs from the server and runs HandBrakeCLI.

## Run worker

Python env is managed with `uv` (required).

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
python worker.py
```

The worker now starts its UI automatically (default on port 8857).

## Run worker UI (standalone)

```powershell
uvicorn ui:app --reload --host 0.0.0.0 --port 8857
```

Open `http://127.0.0.1:8857`.

## Config

Edit `config.json`:

- `serverUrl`: server base URL
- `name`: worker name
- `cacheDir`: local cache for source/output files
- `workerId`: optional; defaults to `wrk_<hostname>`
- `handbrakePath`: optional path to HandBrakeCLI
- `workHours`: list of `{ "start": "HH:MM", "end": "HH:MM" }` blocks
- `pollIntervalSec`: delay between polls
- `uiEnabled`: start UI server with worker
- `uiHost`: UI bind host (use `0.0.0.0` for LAN access)
- `uiPort`: UI port

Changes via the UI require restarting the worker process to take effect.

## CLI flags

- `--ui` force enable UI
- `--no-ui` disable UI
- `--ui-host` override UI host
- `--ui-port` override UI port

## Notes

- Output replaces the original file path.
- If your output container differs from the source extension, keep the output format aligned with the source for now.
