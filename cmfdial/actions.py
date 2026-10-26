"""Actions a gesture can trigger, and how they are performed (virtual keyboard/mouse via uinput)."""
import shlex
import subprocess

# id -> label shown in the settings app
ACTIONS = {
    "none": "Do nothing",
    "scroll_up": "Scroll up",
    "scroll_down": "Scroll down",
    "system_volume": "Change volume (normal dial)",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "mute": "Mute",
    "play_pause": "Play / pause",
    "next_track": "Next track",
    "prev_track": "Previous track",
    "page_down": "Page down / next slide",
    "page_up": "Page up / previous slide",
    "arrow_right": "Right arrow",
    "arrow_left": "Left arrow",
    "arrow_down": "Down arrow",
    "arrow_up": "Up arrow",
    "next_tab": "Next tab",
    "prev_tab": "Previous tab",
    "switch_app": "Switch app (Alt+Tab)",
    "zoom_in": "Zoom in",
    "zoom_out": "Zoom out",
    "undo": "Undo",
    "redo": "Redo",
    "screenshot": "Screenshot",
    "key:": "Custom shortcut...",
    "cmd:": "Run command...",
}

GLOBAL_ACTIONS = {
    "open_menu": "Open the wheel menu",
    "mode_press": "Current mode's press action",
    "next_mode": "Next mode",
    "prev_mode": "Previous mode",
}

KEY_COMBOS = {
    "volume_up": "volumeup", "volume_down": "volumedown", "mute": "mute",
    "play_pause": "playpause", "next_track": "nextsong", "prev_track": "previoussong",
    "page_down": "pagedown", "page_up": "pageup",
    "arrow_right": "right", "arrow_left": "left", "arrow_down": "down", "arrow_up": "up",
    "next_tab": "ctrl+tab", "prev_tab": "ctrl+shift+tab", "switch_app": "alt+tab",
    "zoom_in": "ctrl+equal", "zoom_out": "ctrl+minus",
    "undo": "ctrl+z", "redo": "ctrl+shift+z", "screenshot": "sysrq",
}

ALIASES = {
    "ctrl": "leftctrl", "control": "leftctrl", "shift": "leftshift", "alt": "leftalt",
    "super": "leftmeta", "meta": "leftmeta", "win": "leftmeta", "esc": "esc",
    "enter": "enter", "return": "enter", "del": "delete", "plus": "equal",
    "+": "equal", "-": "minus", "pgup": "pageup", "pgdn": "pagedown", "printscreen": "sysrq",
    "prtsc": "sysrq", "playpause": "playpause",
}


def label(action):
    if action.startswith("key:") and len(action) > 4:
        return "Shortcut " + action[4:]
    if action.startswith("cmd:") and len(action) > 4:
        return "Run: " + action[4:]
    return ACTIONS.get(action) or GLOBAL_ACTIONS.get(action) or action


def parse_combo(combo, ecodes):
    """'ctrl+shift+t' -> [KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_T]; raises ValueError."""
    codes = []
    for part in combo.lower().replace(" ", "").split("+"):
        if not part:
            continue
        name = ALIASES.get(part, part)
        code = getattr(ecodes, "KEY_" + name.upper(), None)
        if code is None:
            raise ValueError(f"unknown key '{part}'")
        codes.append(code)
    if not codes:
        raise ValueError("empty shortcut")
    return codes


class Performer:
    """Creates one virtual input device and performs actions with it."""

    def __init__(self, evdev_mod, scroll_step=3):
        from evdev import UInput, ecodes as e
        self.e = e
        self.scroll_step = scroll_step
        keys = [v for k, v in e.ecodes.items() if k.startswith("KEY_") and v < 0x2ff]
        self.ui = UInput({e.EV_KEY: sorted(set(keys)), e.EV_REL: [e.REL_WHEEL, e.REL_HWHEEL]},
                         name="cmf-dial-virtual")

    def close(self):
        try:
            self.ui.close()
        except Exception:
            pass

    def combo(self, codes):
        e = self.e
        for c in codes:
            self.ui.write(e.EV_KEY, c, 1)
        self.ui.syn()
        for c in reversed(codes):
            self.ui.write(e.EV_KEY, c, 0)
        self.ui.syn()

    def run(self, action):
        e = self.e
        if not action or action == "none" or action == "system_volume":
            return
        if action in ("scroll_up", "scroll_down"):
            self.ui.write(e.EV_REL, e.REL_WHEEL, self.scroll_step if action == "scroll_up" else -self.scroll_step)
            self.ui.syn()
            return
        if action.startswith("cmd:"):
            cmd = action[4:].strip()
            if cmd:
                subprocess.Popen(cmd, shell=True, start_new_session=True)
            return
        combo = action[4:] if action.startswith("key:") else KEY_COMBOS.get(action)
        if combo:
            self.combo(parse_combo(combo, e))
