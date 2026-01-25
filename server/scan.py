import json
import os
import shutil
import subprocess
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".m4v", ".avi", ".mpg", ".mpeg", ".ts", ".wmv", ".webm"}


def ffprobe_path(explicit=None):
    explicit = explicit or os.environ.get("FFPROBE_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("ffprobe")


def probe_media(path, explicit_ffprobe=None):
    ffprobe = ffprobe_path(explicit_ffprobe)
    if not ffprobe:
        return {}

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}
    tags_lower = {str(k).lower(): str(v) for k, v in tags.items()}

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    duration = fmt.get("duration") or (video_stream or {}).get("duration")
    duration_sec = 0.0
    try:
        duration_sec = float(duration)
    except (TypeError, ValueError):
        duration_sec = 0.0

    width = int(video_stream.get("width") or 0) if video_stream else 0
    height = int(video_stream.get("height") or 0) if video_stream else 0
    fps = 0.0
    fps_raw = (video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate")
    if fps_raw and fps_raw != "0/0":
        try:
            num, den = fps_raw.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0

    audio_codecs = [s.get("codec_name") for s in audio_streams if s.get("codec_name")]
    subtitle_langs = []
    for s in subtitle_streams:
        tags = s.get("tags") or {}
        lang = tags.get("language")
        if lang:
            subtitle_langs.append(lang)

    encoded_by = tags_lower.get("encoded_by") or tags_lower.get("encodedby") or tags_lower.get("encoder")
    comment = tags_lower.get("comment") or ""
    spacesaver = False
    if encoded_by and "mediaspacesaver" in encoded_by.lower():
        spacesaver = True
    if "spacesaver=1" in comment.lower():
        spacesaver = True

    return {
        "durationSec": duration_sec,
        "width": width,
        "height": height,
        "fps": fps,
        "videoCodec": (video_stream or {}).get("codec_name"),
        "audioCodecs": audio_codecs,
        "subtitleLangs": subtitle_langs,
        "encodedBy": encoded_by or "",
        "encodedBySpacesaver": spacesaver,
    }


def compute_ratio(item, config):
    duration_sec = item.get("durationSec") or 0
    height = item.get("height") or 0
    size_bytes = item.get("sizeBytes") or 0

    if duration_sec <= 0 or size_bytes <= 0 or height <= 0:
        return {"targetBytes": 0, "savingsBytes": 0, "savingsPct": 0}

    buckets = config.get("targetMbPerMinByHeight") or {}
    if not buckets:
        return {"targetBytes": 0, "savingsBytes": 0, "savingsPct": 0}

    sorted_keys = sorted(int(k) for k in buckets.keys())
    target_key = sorted_keys[-1]
    for key in sorted_keys:
        if height <= key:
            target_key = key
            break

    target_mb_per_min = buckets.get(str(target_key)) if str(target_key) in buckets else buckets.get(target_key)
    if not target_mb_per_min:
        return {"targetBytes": 0, "savingsBytes": 0, "savingsPct": 0}

    duration_min = duration_sec / 60.0
    target_bytes = duration_min * float(target_mb_per_min) * 1024 * 1024
    savings_bytes = size_bytes - target_bytes
    savings_pct = (savings_bytes / size_bytes) if size_bytes > 0 else 0

    return {
        "targetBytes": int(target_bytes),
        "savingsBytes": int(savings_bytes),
        "savingsPct": round(savings_pct, 4),
    }


def list_media_files(root_path):
    root = Path(root_path)
    if not root.exists():
        return []
    files = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            files.append(path)
    return files
