# Compare Tool

Standalone local UI for sampling a 10 second clip and visually comparing original vs encoded.

## Run

Python env is managed with `uv` (required).

1) Create a virtual env:

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
```

2) Install deps:

```powershell
uv pip install -r requirements.txt
```

3) Run:

```powershell
python app.py
```

Open `http://127.0.0.1:8855` in a browser.

## Notes

- `config.json` stores the baseline HandBrakeCLI args. Do not include `-i` or `-o`.
- If `HandBrakeCLI` isn’t found when running under `uv`, set `handbrakePath` in `config.json` or the `HANDBRAKECLI_PATH` env var.
- If `ffmpeg` isn’t found, set `ffmpegPath` in `config.json` or the `FFMPEG_PATH` env var.
- Output samples are written to `data/compare_cache/`.
- The tool first stream-copies a 10s source clip with `ffmpeg`, then encodes that clip with HandBrake for apples-to-apples comparison.
- If your browser cannot play the codec/container, preview may fail. Try `-f av_mp4` for testing.
