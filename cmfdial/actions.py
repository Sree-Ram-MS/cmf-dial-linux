"""Actions a gesture can trigger, and how they are performed (virtual keyboard/mouse via uinput)."""
import shlex
import subprocess
import time

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
    "brightness_up": "Brightness up",
    "brightness_down": "Brightness down",
    "kbd_light_up": "Keyboard light up",
    "kbd_light_down": "Keyboard light down",
    "night_light": "Night Light on / off",
    "screen_record": "Start / stop screen recording",
    "screenshot_window": "Screenshot of the window",
    "screenshot_screen": "Screenshot of the whole screen",
    "mic_mute": "Microphone mute",
    "dnd": "Do Not Disturb on / off",
    "lock_screen": "Lock screen",
    "overview": "Activities overview",
    "show_apps": "Show all apps",
    "show_desktop": "Show desktop",
    "next_workspace": "Next workspace",
    "prev_workspace": "Previous workspace",
    "switch_app_back": "Switch app backwards",
    "switch_window": "Switch window of the same app",
    "minimize": "Minimise window",
    "maximize": "Maximise / restore window",
    "close_window": "Close window",
    "new_tab": "New tab",
    "reopen_tab": "Reopen closed tab",
    "stop_media": "Stop media",
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
    "brightness_up": "brightnessup", "brightness_down": "brightnessdown",
    "kbd_light_up": "kbdillumup", "kbd_light_down": "kbdillumdown",
    "screen_record": "ctrl+shift+alt+r", "screenshot_window": "alt+sysrq", "screenshot_screen": "shift+sysrq",
    "mic_mute": "micmute", "lock_screen": "super+l", "overview": "super", "show_apps": "super+a",
    "show_desktop": "super+d", "next_workspace": "super+pagedown", "prev_workspace": "super+pageup",
    "switch_app_back": "alt+shift+tab", "switch_window": "alt+grave", "minimize": "super+h",
    "maximize": "alt+f10", "close_window": "alt+f4", "new_tab": "ctrl+t", "reopen_tab": "ctrl+shift+t",
    "stop_media": "stopcd",
}

# on / off switches done through GNOME settings: (schema, key, True when the key means "on")
TOGGLES = {
    "night_light": ("org.gnome.settings-daemon.plugins.color", "night-light-enabled", True),
    "dnd": ("org.gnome.desktop.notifications", "show-banners", False),
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
        # one key per frame with a short pause: GNOME Shell shortcuts (Super+1,
        # Ctrl+Shift+Alt+R...) are missed when the whole chord lands at once
        e = self.e
        for c in codes:
            self.ui.write(e.EV_KEY, c, 1)
            self.ui.syn()
            time.sleep(0.012)
        time.sleep(0.03)
        for c in reversed(codes):
            self.ui.write(e.EV_KEY, c, 0)
            self.ui.syn()
            time.sleep(0.008)

    @staticmethod
    def toggle(action):
        schema, key, on_means_true = TOGGLES[action]
        try:
            cur = subprocess.run(["gsettings", "get", schema, key], capture_output=True,
                                 text=True, timeout=3).stdout.strip() == "true"
            subprocess.run(["gsettings", "set", schema, key, "false" if cur else "true"], timeout=3)
            now_true = not cur
            return now_true if on_means_true else not now_true
        except (OSError, subprocess.TimeoutExpired):
            return None

    def run(self, action):
        e = self.e
        if not action or action == "none" or action == "system_volume":
            return
        if action in ("scroll_up", "scroll_down"):
            self.ui.write(e.EV_REL, e.REL_WHEEL, self.scroll_step if action == "scroll_up" else -self.scroll_step)
            self.ui.syn()
            return
        if action in TOGGLES:
            return self.toggle(action)
        if action.startswith("cmd:"):
            cmd = action[4:].strip()
            if cmd:
                subprocess.Popen(cmd, shell=True, start_new_session=True)
            return
        combo = action[4:] if action.startswith("key:") else KEY_COMBOS.get(action)
        if combo:
            self.combo(parse_combo(combo, e))
