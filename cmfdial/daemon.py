"""Background service: reads the CMF Buds Pro 2 dial and performs the configured actions.

How the buds reach Linux (BlueZ + PipeWire):
  * Dial presses arrive as AVRCP play/pause, next and previous on BlueZ's
    "<buds name> (AVRCP)" input device.
  * Dial turns arrive either as AVRCP absolute volume (the buds' sink volume
    changes) or, on some setups, as volume-up / volume-down keys on that same
    device. Both are handled.
"""
import fcntl
import glob
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time

from . import __version__, actions, config

try:
    import evdev
    from evdev import ecodes as E
except ImportError:  # pragma: no cover
    sys.exit("python3-evdev is missing: sudo apt install python3-evdev")

PRESS_KEYS = {
    "KEY_PLAYPAUSE": "single", "KEY_PLAYCD": "single", "KEY_PAUSECD": "single",
    "KEY_PLAY": "single", "KEY_PAUSE": "single",
    "KEY_NEXTSONG": "double", "KEY_PREVIOUSSONG": "triple",
    "KEY_VOLUMEUP": "turn_up", "KEY_VOLUMEDOWN": "turn_down",
}
PRESS_CODES = {getattr(E, k): v for k, v in PRESS_KEYS.items() if hasattr(E, k)}


class Service:
    def __init__(self, foreground=False):
        self.foreground = foreground
        self.cfg = config.load()
        self.cfg_mtime = config.mtime()
        self.mode_idx = int(self.cfg.get("start_mode", 0))
        self.performer = None
        self.devices = {}          # fd -> InputDevice
        self.sink = None           # (index, name)
        self.base_vol = None
        self.sub = None
        self.sub_buf = b""
        self.last_key_turn = 0.0
        self.status = {}
        self.last_scan = 0.0
        self.wheel = None
        self.wheel_failed = 0.0
        self.menu_open = False
        self.menu_sel = 0
        self.menu_deadline = 0.0
        os.makedirs(config.STATE_DIR, exist_ok=True)

    # ---------- output ----------
    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        if self.foreground or sys.stdout.isatty():
            print("[cmf-dial] " + line, flush=True)
        try:
            path = config.EVENTS_PATH
            if os.path.exists(path) and os.path.getsize(path) > 40000:
                with open(path) as f:
                    keep = f.readlines()[-150:]
                with open(path, "w") as f:
                    f.writelines(keep)
            with open(path, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def set_status(self, **kw):
        self.status.update(kw)
        self.status["time"] = time.time()
        self.status["version"] = __version__
        self.status["pid"] = os.getpid()
        try:
            tmp = config.STATUS_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.status, f)
            os.replace(tmp, config.STATUS_PATH)
        except OSError:
            pass

    def notify(self, msg):
        self.log(msg)
        if not self.cfg.get("notifications", True) or not shutil.which("notify-send"):
            return
        try:
            subprocess.Popen(["notify-send", "-a", "CMF Dial", "-i", "audio-headphones", "-t", "1200",
                              "-h", "string:x-canonical-private-synchronous:cmfdial",
                              "-h", "string:x-dunst-stack-tag:cmfdial", "CMF Dial", msg],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    # ---------- modes ----------
    def modes(self):
        m = [x for x in self.cfg.get("modes", []) if x.get("enabled", True)]
        return m or [config.DEFAULT["modes"][0]]

    def mode(self):
        m = self.modes()
        self.mode_idx %= len(m)
        return m[self.mode_idx]

    # ---------- audio (pactl) ----------
    @staticmethod
    def pactl(*args):
        try:
            return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def find_sink(self):
        for line in self.pactl("list", "short", "sinks").splitlines():
            parts = line.split()
            if len(parts) > 1 and parts[1].startswith("bluez_output"):
                return (parts[0], parts[1])
        return None

    def get_vol(self):
        if not self.sink:
            return None
        m = re.search(r"(\d+)%", self.pactl("get-sink-volume", self.sink[1]))
        return int(m.group(1)) if m else None

    def set_vol(self, value):
        if self.sink:
            self.pactl("set-sink-volume", self.sink[1], value)

    def start_subscribe(self):
        if self.sub and self.sub.poll() is None:
            return
        cmd = ["pactl", "subscribe"]
        if shutil.which("stdbuf"):
            cmd = ["stdbuf", "-oL"] + cmd
        try:
            self.sub = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env={**os.environ, "LC_ALL": "C"})
            self.sub_buf = b""
        except OSError as ex:
            self.sub = None
            self.log(f"cannot run pactl: {ex}")

    # ---------- input devices ----------
    def find_devices(self):
        hint = (self.cfg.get("device_hint") or "").lower()
        found = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            n = d.name.lower()
            if n.startswith("cmf-dial"):
                d.close()
                continue
            if "avrcp" in n and (not hint or hint in n):
                found.append(d)
            else:
                d.close()
        if not found:  # fall back to any AVRCP device
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                except OSError:
                    continue
                if "avrcp" in d.name.lower() and not d.name.lower().startswith("cmf-dial"):
                    found.append(d)
                else:
                    d.close()
        return found

    def unreadable_avrcp(self):
        """AVRCP devices that exist but this user cannot open (permission problem)."""
        names = []
        try:
            with open("/proc/bus/input/devices") as f:
                blocks = f.read().split("\n\n")
        except OSError:
            return names
        for b in blocks:
            m = re.search(r'N: Name="([^"]*)"', b)
            h = re.search(r"H: Handlers=.*?(event\d+)", b)
            if m and h and "avrcp" in m.group(1).lower():
                p = "/dev/input/" + h.group(1)
                if not os.access(p, os.R_OK):
                    names.append(f"{m.group(1)} ({p})")
        return names

    def drop_devices(self):
        for d in self.devices.values():
            try:
                d.ungrab()
            except Exception:
                pass
            try:
                d.close()
            except Exception:
                pass
        self.devices = {}

    def ensure_inputs(self):
        if time.time() - self.last_scan < 2 and (not self.devices or not self.sink):
            return
        if self.devices and self.sink:
            return
        self.last_scan = time.time()
        if not self.sink:
            self.sink = self.find_sink()
            if self.sink:
                self.base_vol = self.get_vol()
                self.log(f"buds audio output: {self.sink[1]} (volume {self.base_vol}%)")
        if not self.devices:
            for d in self.find_devices():
                if self.cfg.get("grab_buttons", True):
                    try:
                        d.grab()
                    except OSError as ex:
                        self.log(f"could not grab {d.name}: {ex} (presses still read)")
                self.devices[d.fd] = d
                self.log(f"buds buttons: {d.name} at {d.path}")
        if self.devices or self.sink:
            if not self.status.get("ready"):
                self.notify(f"Ready: {self.mode()['name']}")
            self.set_status(state="running", ready=True,
                            device=", ".join(d.name for d in self.devices.values()) or None,
                            sink=self.sink[1] if self.sink else None,
                            mode=self.mode()["name"], error=None)
        else:
            hidden = self.unreadable_avrcp()
            err = ("Buds button device found but not readable: " + ", ".join(hidden) +
                   ". Reboot once, or run: sudo chmod a+rw /dev/input/event*") if hidden else None
            self.set_status(state="waiting", ready=False, device=None, sink=None,
                            mode=self.mode()["name"], error=err)

    # ---------- wheel menu ----------
    def wheel_env(self):
        env = dict(os.environ, GDK_BACKEND="x11", NO_AT_BRIDGE="1")
        if not env.get("DISPLAY"):
            env["DISPLAY"] = ":0"
        if not env.get("XAUTHORITY"):
            run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            auth = sorted(glob.glob(os.path.join(run, ".mutter-Xwaylandauth.*")))
            if auth:
                env["XAUTHORITY"] = auth[-1]
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = pkg_root + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env, pkg_root

    def wheel_send(self, msg):
        if self.wheel is None or self.wheel.poll() is not None:
            if time.time() - self.wheel_failed < 10:
                return False
            env, root = self.wheel_env()
            try:
                self.wheel = subprocess.Popen([sys.executable, "-m", "cmfdial.wheel"], stdin=subprocess.PIPE,
                                              cwd=root, env=env, stdout=subprocess.DEVNULL,
                                              stderr=open(os.path.join(config.STATE_DIR, "wheel.log"), "w"),
                                              text=True, bufsize=1)
            except OSError as ex:
                self.log(f"wheel menu cannot start: {ex}")
                self.wheel, self.wheel_failed = None, time.time()
                return False
        try:
            self.wheel.stdin.write(json.dumps(msg) + "\n")
            self.wheel.stdin.flush()
            return True
        except (OSError, ValueError):
            self.wheel, self.wheel_failed = None, time.time()
            return False

    def show_menu(self):
        modes = [{"name": m.get("name", ""), "icon": m.get("icon")} for m in self.modes()]
        ok = self.wheel_send({"cmd": "show", "modes": modes, "sel": self.menu_sel})
        if not ok:
            # no wheel possible (no display): behave like "next mode"
            self.menu_open = False
            self.mode_idx += 1
            self.notify(self.mode()["name"])
            self.set_status(mode=self.mode()["name"])

    def open_menu(self):
        self.menu_open = True
        self.menu_sel = self.mode_idx % len(self.modes())
        self.menu_deadline = time.time() + float(self.cfg.get("menu_timeout", 5))
        self.log("wheel menu opened")
        self.show_menu()

    def close_menu(self, select=True):
        if not self.menu_open:
            return
        self.menu_open = False
        self.wheel_send({"cmd": "hide"})
        if select:
            self.mode_idx = self.menu_sel
            self.base_vol = self.get_vol()
            name = self.mode()["name"]
            self.log(f"wheel menu: picked {name}")
            self.set_status(mode=name)
        else:
            self.log("wheel menu closed")

    def menu_gesture(self, gesture, source):
        n = len(self.modes())
        if gesture in ("turn_up", "turn_down"):
            if source == "volume" and self.base_vol is not None:
                self.set_vol(f"{self.base_vol}%")
            self.menu_sel = (self.menu_sel + (1 if gesture == "turn_up" else -1)) % n
            self.menu_deadline = time.time() + float(self.cfg.get("menu_timeout", 5))
            self.show_menu()
        elif gesture == "single":
            self.close_menu(select=True)
        else:  # double / triple press closes without changing mode
            self.close_menu(select=False)

    # ---------- gestures ----------
    def handle(self, gesture, source):
        if self.menu_open:
            self.menu_gesture(gesture, source)
            return
        mode = self.mode()
        if gesture in ("turn_up", "turn_down"):
            act = mode.get(gesture, "none")
            self.log(f"{gesture.replace('_', ' ')} ({source}) -> {mode['name']}: {actions.label(act)}")
            if act == "system_volume":
                if source == "volume":
                    self.base_vol = self.get_vol()
                else:
                    self.set_vol("+5%" if gesture == "turn_up" else "-5%")
                    self.base_vol = self.get_vol()
            else:
                if source == "volume" and self.base_vol is not None:
                    self.set_vol(f"{self.base_vol}%")  # put the real volume back
                self.perform(act)
            self.set_status(last=f"{gesture} via {source}")
            return
        g = self.cfg.get("global", {}).get(gesture, "none")
        if g == "open_menu":
            self.open_menu()
        elif g == "mode_press":
            act = mode.get("press", "none")
            self.log(f"{gesture} press -> {mode['name']}: {actions.label(act)}")
            self.perform(act)
        elif g in ("next_mode", "prev_mode"):
            self.mode_idx += 1 if g == "next_mode" else -1
            m = self.mode()
            self.base_vol = self.get_vol()
            self.notify(m["name"])
            self.set_status(mode=m["name"])
        else:
            self.log(f"{gesture} press -> {actions.label(g)}")
            self.perform(g)
        self.set_status(last=f"{gesture} press")

    def perform(self, act):
        if not self.performer:
            return
        try:
            self.performer.run(act)
        except ValueError as ex:
            self.log(f"bad shortcut '{act}': {ex}")
        except OSError as ex:
            self.log(f"action failed: {ex}")

    def on_volume_event(self, line):
        if " on sink " not in line:
            return
        if "'new'" in line or "'remove'" in line:
            self.sink = None  # rescan the outputs
            return
        if not self.sink or not line.rstrip().endswith(f"#{self.sink[0]}"):
            return
        v = self.get_vol()
        if v is None:
            return
        if self.base_vol is None or v == self.base_vol:
            self.base_vol = v
            return
        if time.time() - self.last_key_turn < 0.4:
            self.base_vol = v  # the key event already handled this turn
            return
        self.handle("turn_up" if v > self.base_vol else "turn_down", "volume")

    def on_key(self, dev):
        try:
            events = list(dev.read())
        except OSError:
            self.log(f"buds disconnected ({dev.name})")
            self.devices.pop(dev.fd, None)
            try:
                dev.close()
            except Exception:
                pass
            self.sink = None
            self.status["ready"] = False
            return
        for ev in events:
            if ev.type != E.EV_KEY or ev.value != 1:
                continue
            name = E.KEY.get(ev.code, ev.code)
            if isinstance(name, list):
                name = name[0]
            gesture = PRESS_CODES.get(ev.code)
            if not gesture:
                self.log(f"button {name} (not used)")
                continue
            if gesture.startswith("turn"):
                self.last_key_turn = time.time()
            self.handle(gesture, f"button {name}")

    # ---------- main loop ----------
    def reload_if_changed(self):
        mt = config.mtime()
        if mt != self.cfg_mtime:
            self.cfg_mtime = mt
            old_grab = self.cfg.get("grab_buttons", True)
            self.cfg = config.load()
            if self.performer:
                self.performer.scroll_step = int(self.cfg.get("scroll_step", 3))
            if self.cfg.get("grab_buttons", True) != old_grab:
                self.drop_devices()
            self.log("settings reloaded")
            self.set_status(mode=self.mode()["name"])

    def ensure_performer(self):
        if self.performer:
            return True
        try:
            self.performer = actions.Performer(evdev, int(self.cfg.get("scroll_step", 3)))
            return True
        except Exception as ex:
            self.set_status(state="error", ready=False,
                            error=f"Cannot create the virtual keyboard (/dev/uinput): {ex}. "
                                  "Reboot once after installing, or run: sudo chmod a+rw /dev/uinput")
            return False

    def run(self):
        lock = open(os.path.join(config.STATE_DIR, "lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("cmf-dial is already running (stop it with: systemctl --user stop cmf-dial)")
            return 1
        self.log(f"CMF Dial {__version__} started")
        self.set_status(state="starting", ready=False)
        while True:
            self.reload_if_changed()
            if not self.cfg.get("enabled", True):
                if self.devices:
                    self.drop_devices()
                self.set_status(state="paused", ready=False)
                time.sleep(1)
                continue
            if not self.ensure_performer():
                time.sleep(3)
                continue
            self.ensure_inputs()
            self.start_subscribe()
            fds = list(self.devices.keys())
            sub_fd = self.sub.stdout.fileno() if self.sub else None
            if sub_fd is not None:
                fds.append(sub_fd)
            if self.menu_open and time.time() > self.menu_deadline:
                self.close_menu(select=True)
            try:
                r, _, _ = select.select(fds, [], [], 0.2 if self.menu_open else 1.0)
            except (OSError, ValueError):
                self.drop_devices()
                continue
            for fd in r:
                if fd == sub_fd:
                    data = os.read(fd, 4096)
                    if not data:  # pactl ended; restart it next loop
                        self.sub = None
                        continue
                    self.sub_buf += data
                    *lines, self.sub_buf = self.sub_buf.split(b"\n")
                    for line in lines:
                        self.on_volume_event(line.decode(errors="replace"))
                elif fd in self.devices:
                    self.on_key(self.devices[fd])


def main():
    foreground = "--foreground" in sys.argv or "-f" in sys.argv
    svc = Service(foreground=foreground)

    def stop(*_):
        svc.wheel_send({"cmd": "quit"}) if svc.wheel else None
        svc.drop_devices()
        if svc.performer:
            svc.performer.close()
        svc.set_status(state="stopped", ready=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sys.exit(svc.run() or 0)


if __name__ == "__main__":
    main()
