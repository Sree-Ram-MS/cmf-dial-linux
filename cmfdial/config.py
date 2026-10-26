"""Settings stored in ~/.config/cmf-dial/config.json, shared by the service and the settings app."""
import copy
import json
import os

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "cmf-dial")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", os.path.expanduser("~/.cache")), "cmf-dial")
EVENTS_PATH = os.path.join(STATE_DIR, "events.log")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")

# Gestures the buds can send. Each maps to a raw AVRCP signal.
GESTURES = [
    ("turn_up", "Turn dial clockwise"),
    ("turn_down", "Turn dial anticlockwise"),
    ("single", "Single press"),
    ("double", "Double press"),
    ("triple", "Triple press"),
]

DEFAULT = {
    "enabled": True,
    "notifications": True,
    "grab_buttons": True,        # keep dial presses away from music players
    "device_hint": "CMF",        # part of the buds' Bluetooth name
    "scroll_step": 3,
    "start_mode": 0,
    "config_version": 2,
    "menu_timeout": 5,           # seconds before the wheel picks the highlighted mode
    # What the presses do in every mode. "mode_press" runs the current mode's press action.
    "global": {
        "single": "mode_press",
        "double": "open_menu",
        "triple": "prev_mode",
    },
    "modes": [
        {"name": "Scroll", "enabled": True, "turn_up": "scroll_down", "turn_down": "scroll_up", "press": "play_pause"},
        {"name": "Volume", "enabled": True, "turn_up": "system_volume", "turn_down": "system_volume", "press": "play_pause"},
        {"name": "Slides", "enabled": True, "turn_up": "page_down", "turn_down": "page_up", "press": "key:b"},
        {"name": "Tabs", "enabled": True, "turn_up": "next_tab", "turn_down": "prev_tab", "press": "key:ctrl+w"},
        {"name": "Arrows", "enabled": False, "turn_up": "arrow_right", "turn_down": "arrow_left", "press": "key:space"},
        {"name": "Zoom", "enabled": False, "turn_up": "zoom_in", "turn_down": "zoom_out", "press": "key:ctrl+0"},
        {"name": "Undo / Redo", "enabled": False, "turn_up": "redo", "turn_down": "undo", "press": "none"},
        {"name": "Media", "enabled": False, "turn_up": "next_track", "turn_down": "prev_track", "press": "play_pause"},
    ],
}


def _merge(base, user):
    out = copy.deepcopy(base)
    for k, v in (user or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load():
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    old_version = data.get("config_version", 1) if data else 2
    cfg = _merge(DEFAULT, data)
    if old_version < 2:
        # 2.1 adds the wheel menu: double press opens it
        cfg["global"]["double"] = "open_menu"
        cfg["config_version"] = 2
        try:
            save(cfg)
        except OSError:
            pass
    if not cfg.get("modes"):
        cfg["modes"] = copy.deepcopy(DEFAULT["modes"])
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def mtime():
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return 0.0
