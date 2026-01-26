import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
COMPARE_CONFIG_PATH = ROOT / "tools" / "compare" / "config.json"

_LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def default_state():
    compare_baseline = None
    if COMPARE_CONFIG_PATH.exists():
        try:
            compare_data = json.loads(COMPARE_CONFIG_PATH.read_text(encoding="utf-8"))
            compare_baseline = (compare_data or {}).get("baselineArgs") or None
        except json.JSONDecodeError:
            compare_baseline = None

    return {
        "version": 1,
        "config": {
            "baselineArgs": compare_baseline
            or "-f av_mkv -e x265_10bit --encoder-preset medium -q 20 --audio-lang-list eng --first-audio -E copy --subtitle-lang-list eng --first-subtitle --crop 0:0:0:0",
            "ffprobePath": "",
            "targetMbPerMinByHeight": {
                "480": 6,
                "720": 10,
                "1080": 16,
                "2160": 32,
            },
            "targetSamplesByHeight": {},
            "audioLangList": ["eng"],
            "subtitleLangList": ["eng"],
        },
        "entries": [],
        "items": [],
        "jobs": [],
        "workers": [],
        "scanStatus": {
            "active": False,
            "entryId": None,
            "entryName": None,
            "total": 0,
            "done": 0,
            "currentPath": None,
            "startedAt": None,
            "updatedAt": None,
            "finishedAt": None,
        },
    }


def _read_state_no_lock():
    if not STATE_PATH.exists():
        state = default_state()
        _write_state_no_lock(state)
        return state
    with STATE_PATH.open("r", encoding="utf-8") as f:
        state = json.load(f)

    config = state.get("config") or {}
    if not config.get("baselineArgs"):
        compare_baseline = None
        if COMPARE_CONFIG_PATH.exists():
            try:
                compare_data = json.loads(COMPARE_CONFIG_PATH.read_text(encoding="utf-8"))
                compare_baseline = (compare_data or {}).get("baselineArgs") or None
            except json.JSONDecodeError:
                compare_baseline = None
        config["baselineArgs"] = compare_baseline or default_state()["config"]["baselineArgs"]

    if "targetMbPerMinByHeight" not in config:
        config["targetMbPerMinByHeight"] = default_state()["config"]["targetMbPerMinByHeight"]
    if "ffprobePath" not in config:
        config["ffprobePath"] = default_state()["config"]["ffprobePath"]
    if "audioLangList" not in config:
        config["audioLangList"] = default_state()["config"]["audioLangList"]
    if "subtitleLangList" not in config:
        config["subtitleLangList"] = default_state()["config"]["subtitleLangList"]
    if "targetSamplesByHeight" not in config:
        config["targetSamplesByHeight"] = default_state()["config"]["targetSamplesByHeight"]
    state["config"] = config
    if "scanStatus" not in state:
        state["scanStatus"] = default_state()["scanStatus"]

    return state


def _write_state_no_lock(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp_path.replace(STATE_PATH)


def load_state():
    with _LOCK:
        return _read_state_no_lock()


def save_state(state):
    with _LOCK:
        _write_state_no_lock(state)


def update_state(mutator):
    with _LOCK:
        state = _read_state_no_lock()
        result = mutator(state)
        _write_state_no_lock(state)
        return result
