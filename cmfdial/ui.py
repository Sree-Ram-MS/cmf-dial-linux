"""CMF Dial settings app (GTK 4 + libadwaita)."""
import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import __version__, actions, config  # noqa: E402

APP_ID = "io.github.sreerammsofficial.CmfDial"

ICONS = [
    ("Automatic", ""), ("Mouse / scroll", "input-mouse-symbolic"), ("Volume", "audio-volume-high-symbolic"),
    ("Slides", "x-office-presentation-symbolic"), ("Tabs", "view-dual-symbolic"),
    ("Arrows", "media-seek-forward-symbolic"), ("Zoom", "zoom-in-symbolic"), ("Undo", "edit-undo-symbolic"),
    ("Media", "media-playback-start-symbolic"), ("Brightness", "display-brightness-symbolic"),
    ("Windows", "focus-windows-symbolic"), ("Camera", "camera-photo-symbolic"),
    ("Screen", "video-display-symbolic"), ("Keyboard", "input-keyboard-symbolic"),
    ("Terminal", "utilities-terminal-symbolic"), ("Star", "starred-symbolic"),
]
TURN_ACTIONS = [k for k in actions.ACTIONS]
PRESS_ACTIONS = list(actions.GLOBAL_ACTIONS) + [k for k in actions.ACTIONS if k != "system_volume"]
MODE_PRESS_ACTIONS = [k for k in actions.ACTIONS if k != "system_volume"]


def service(*args):
    try:
        return subprocess.run(["systemctl", "--user", *args, "cmf-dial.service"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_status():
    try:
        with open(config.STATUS_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    pid = st.get("pid")
    if pid:
        try:
            os.kill(pid, 0)
        except OSError:
            return None
    return st


class ActionPicker:
    """A combo row with an optional text row for custom shortcuts and commands."""

    def __init__(self, title, choices, value, on_change):
        self.choices = choices
        self.on_change = on_change
        self.combo = Adw.ComboRow(title=title)
        self.combo.set_model(Gtk.StringList.new([actions.label(c) if c not in ("key:", "cmd:")
                                                 else actions.ACTIONS[c] for c in choices]))
        self.entry = Adw.EntryRow()
        self.entry.set_show_apply_button(True)
        self._set(value)
        self.combo.connect("notify::selected", self._changed)
        self.entry.connect("apply", self._changed)

    def _set(self, value):
        prefix = value[:4] if value[:4] in ("key:", "cmd:") else None
        key = prefix or value
        idx = self.choices.index(key) if key in self.choices else 0
        self.combo.set_selected(idx)
        self._update_entry(prefix, value[4:] if prefix else "")

    def _update_entry(self, prefix, text):
        if prefix == "key:":
            self.entry.set_title("Shortcut, e.g. ctrl+shift+t, alt+f4, super+d")
        elif prefix == "cmd:":
            self.entry.set_title("Command, e.g. gnome-calculator")
        self.entry.set_visible(prefix is not None)
        if text is not None and prefix is not None and not self.entry.get_text():
            self.entry.set_text(text)

    def value(self):
        key = self.choices[self.combo.get_selected()]
        if key in ("key:", "cmd:"):
            return key + self.entry.get_text().strip()
        return key

    def _changed(self, *_):
        key = self.choices[self.combo.get_selected()]
        self._update_entry(key if key in ("key:", "cmd:") else None, None)
        if key in ("key:", "cmd:") and not self.entry.get_text().strip():
            return  # wait until something is typed
        if key == "key:" and self.entry.get_text().strip():
            try:
                from evdev import ecodes
                actions.parse_combo(self.entry.get_text(), ecodes)
                self.entry.remove_css_class("error")
            except ValueError:
                self.entry.add_css_class("error")
                return
            except ImportError:
                pass
        self.on_change()

    def rows(self):
        return [self.combo, self.entry]


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="CMF Dial")
        self.set_default_size(560, 760)
        self.cfg = config.load()
        self._save_id = 0
        self._log_pos = 0

        self.toast = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset to defaults")
        reset.connect("clicked", self.on_reset)
        header.pack_end(reset)
        view.add_top_bar(header)
        self.toast.set_child(view)
        self.set_content(self.toast)

        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        view.set_content(self.stack)

        self.build()
        GLib.timeout_add(700, self.refresh_status)
        self.refresh_status()

    # ---------- build ----------
    def build(self):
        for name in ("controls", "modes", "test"):
            child = self.stack.get_child_by_name(name)
            if child:
                self.stack.remove(child)
        self.stack.add_titled_with_icon(self.page_controls(), "controls", "Controls", "audio-headphones-symbolic")
        self.stack.add_titled_with_icon(self.page_modes(), "modes", "Modes", "view-list-symbolic")
        self.stack.add_titled_with_icon(self.page_test(), "test", "Test", "utilities-terminal-symbolic")

    def page_controls(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Status")
        self.status_row = Adw.ActionRow(title="Checking…")
        self.status_icon = Gtk.Image(icon_name="emblem-synchronizing-symbolic")
        self.status_row.add_prefix(self.status_icon)
        restart = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_: self.restart_service())
        self.status_row.add_suffix(restart)
        g.add(self.status_row)
        en = Adw.SwitchRow(title="Dial control on", subtitle="Turn off to use the dial normally")
        en.set_active(self.cfg.get("enabled", True))
        en.connect("notify::active", lambda r, *_: self.set("enabled", r.get_active()))
        g.add(en)
        page.add(g)

        g = Adw.PreferencesGroup(title="Dial presses",
                                 description="Set the dial in the Nothing X app to: single = Play/Pause, "
                                             "double = Next track, triple = Previous track.")
        labels = {"single": "Single press", "double": "Double press", "triple": "Triple press"}
        for gest in ("single", "double", "triple"):
            pick = ActionPicker(labels[gest], PRESS_ACTIONS, self.cfg["global"].get(gest, "none"), None)
            pick.on_change = (lambda p=pick, k=gest: self.set_global(k, p.value()))
            for r in pick.rows():
                g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(title="Options")
        step = Adw.SpinRow.new_with_range(1, 15, 1)
        step.set_title("Scroll distance per click")
        step.set_value(int(self.cfg.get("scroll_step", 3)))
        step.connect("notify::value", lambda r, *_: self.set("scroll_step", int(r.get_value())))
        g.add(step)
        mt = Adw.SpinRow.new_with_range(2, 15, 1)
        mt.set_title("Wheel picks the highlighted mode after (seconds)")
        mt.set_value(int(self.cfg.get("menu_timeout", 5)))
        mt.connect("notify::value", lambda r, *_: self.set("menu_timeout", int(r.get_value())))
        g.add(mt)
        pv = Adw.ActionRow(title="Preview the wheel menu", subtitle="Shows it for 3 seconds")
        pvb = Gtk.Button(label="Show", valign=Gtk.Align.CENTER)
        pvb.connect("clicked", self.on_preview)
        pv.add_suffix(pvb)
        g.add(pv)
        no = Adw.SwitchRow(title="Show mode pop-ups")
        no.set_active(self.cfg.get("notifications", True))
        no.connect("notify::active", lambda r, *_: self.set("notifications", r.get_active()))
        g.add(no)
        gr = Adw.SwitchRow(title="Keep presses away from music players",
                           subtitle="Off: a press also plays/pauses music")
        gr.set_active(self.cfg.get("grab_buttons", True))
        gr.connect("notify::active", lambda r, *_: self.set("grab_buttons", r.get_active()))
        g.add(gr)
        hint = Adw.EntryRow(title="Buds name contains")
        hint.set_text(self.cfg.get("device_hint", "CMF"))
        hint.set_show_apply_button(True)
        hint.connect("apply", lambda r: self.set("device_hint", r.get_text().strip()))
        g.add(hint)
        page.add(g)
        return page

    def page_modes(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Modes",
                                 description="Double press switches mode. In each mode the dial "
                                             "turn and the press do different things.")
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add mode", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.connect("clicked", self.on_add_mode)
        g.set_header_suffix(add)
        for i, m in enumerate(self.cfg["modes"]):
            g.add(self.mode_row(i, m))
        page.add(g)
        return page

    def mode_row(self, i, m):
        row = Adw.ExpanderRow(title=m.get("name", f"Mode {i + 1}"),
                              subtitle=f"{actions.label(m.get('turn_down', 'none'))} / "
                                       f"{actions.label(m.get('turn_up', 'none'))}")
        sw = Gtk.Switch(active=m.get("enabled", True), valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, *_: self.set_mode(i, "enabled", s.get_active()))
        row.add_suffix(sw)

        name = Adw.EntryRow(title="Name")
        name.set_text(m.get("name", ""))
        name.set_show_apply_button(True)
        name.connect("apply", lambda r: (self.set_mode(i, "name", r.get_text().strip() or f"Mode {i + 1}"),
                                         row.set_title(r.get_text().strip() or f"Mode {i + 1}")))
        row.add_row(name)
        icon = Adw.ComboRow(title="Wheel icon")
        icon.set_model(Gtk.StringList.new([lbl for lbl, _ in ICONS]))
        cur = m.get("icon") or ""
        icon.set_selected(next((k for k, (_, n) in enumerate(ICONS) if n == cur), 0))
        icon.connect("notify::selected", lambda r, *_: self.set_mode(i, "icon", ICONS[r.get_selected()][1]))
        row.add_row(icon)
        for key, title, choices in (("turn_up", "Turn clockwise", TURN_ACTIONS),
                                    ("turn_down", "Turn anticlockwise", TURN_ACTIONS),
                                    ("press", "Press (single press)", MODE_PRESS_ACTIONS)):
            pick = ActionPicker(title, choices, m.get(key, "none"), None)
            pick.on_change = (lambda p=pick, k=key: self.set_mode(i, k, p.value()))
            for r in pick.rows():
                row.add_row(r)

        tools = Adw.ActionRow()
        up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Move up", valign=Gtk.Align.CENTER)
        up.add_css_class("flat")
        up.connect("clicked", lambda *_: self.move_mode(i, -1))
        down = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Move down", valign=Gtk.Align.CENTER)
        down.add_css_class("flat")
        down.connect("clicked", lambda *_: self.move_mode(i, 1))
        delete = Gtk.Button(label="Delete mode", valign=Gtk.Align.CENTER)
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *_: self.delete_mode(i))
        tools.add_suffix(up)
        tools.add_suffix(down)
        tools.add_suffix(delete)
        row.add_row(tools)
        return row

    def page_test(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        lbl = Gtk.Label(wrap=True, xalign=0,
                        label="Play some audio through the buds, then turn and press the dial. "
                              "Every signal the buds send shows up here. Tip: pause audio on your "
                              "phone, since the dial talks to whichever device is playing.")
        lbl.add_css_class("dim-label")
        box.append(lbl)
        self.last_label = Gtk.Label(xalign=0, label="Last heard: nothing yet")
        self.last_label.add_css_class("title-4")
        box.append(self.last_label)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.add_css_class("card")
        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        sc.set_child(self.log_view)
        self.log_scroll = sc
        box.append(sc)
        clear = Gtk.Button(label="Clear", halign=Gtk.Align.END)
        clear.connect("clicked", self.on_clear_log)
        box.append(clear)
        self._log_pos = 0
        return box

    # ---------- config changes ----------
    def set(self, key, value):
        self.cfg[key] = value
        self.queue_save()

    def set_global(self, key, value):
        self.cfg["global"][key] = value
        self.queue_save()

    def set_mode(self, i, key, value):
        if i < len(self.cfg["modes"]):
            self.cfg["modes"][i][key] = value
            self.queue_save()

    def move_mode(self, i, d):
        modes = self.cfg["modes"]
        j = i + d
        if 0 <= j < len(modes):
            modes[i], modes[j] = modes[j], modes[i]
            self.save_now()
            self.rebuild("modes")

    def delete_mode(self, i):
        if len(self.cfg["modes"]) <= 1:
            self.toast.add_toast(Adw.Toast(title="Keep at least one mode"))
            return
        del self.cfg["modes"][i]
        self.save_now()
        self.rebuild("modes")

    def on_add_mode(self, *_):
        self.cfg["modes"].append({"name": f"Mode {len(self.cfg['modes']) + 1}", "enabled": True,
                                  "turn_up": "arrow_right", "turn_down": "arrow_left", "press": "none"})
        self.save_now()
        self.rebuild("modes")

    def on_reset(self, *_):
        dlg = Adw.AlertDialog(heading="Reset all settings?",
                              body="Your modes and button choices go back to the defaults.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(d, res):
            if d.choose_finish(res) == "reset":
                self.cfg = json.loads(json.dumps(config.DEFAULT))
                self.save_now()
                self.rebuild("controls")
        dlg.choose(self, None, done)

    def rebuild(self, show):
        self.build()
        self.stack.set_visible_child_name(show)

    def queue_save(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self.save_now)

    def save_now(self):
        self._save_id = 0
        try:
            config.save(self.cfg)
        except OSError as ex:
            self.toast.add_toast(Adw.Toast(title=f"Could not save: {ex}"))
        return False

    def on_preview(self, *_):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "GDK_BACKEND": "x11", "PYTHONPATH": root}
        try:
            p = subprocess.Popen([sys.executable, "-m", "cmfdial.wheel"], stdin=subprocess.PIPE,
                                 cwd=root, env=env, text=True)
        except OSError as ex:
            self.toast.add_toast(Adw.Toast(title=f"Could not show the wheel: {ex}"))
            return
        modes = [{"name": m.get("name", ""), "icon": m.get("icon")}
                 for m in self.cfg["modes"] if m.get("enabled", True)]
        p.stdin.write(json.dumps({"cmd": "show", "modes": modes, "sel": 0}) + "\n")
        p.stdin.flush()

        def stop():
            try:
                p.stdin.write('{"cmd": "quit"}\n')
                p.stdin.flush()
            except (OSError, ValueError):
                pass
            return False
        GLib.timeout_add(3000, stop)

    # ---------- service ----------
    def restart_service(self):
        r = service("restart")
        if r is None or r.returncode != 0:
            # no systemd user service: start it directly
            pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.Popen([sys.executable, "-m", "cmfdial.daemon"], start_new_session=True,
                             cwd=pkg_root, env={**os.environ, "PYTHONPATH": pkg_root},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.toast.add_toast(Adw.Toast(title="Restarting CMF Dial…"))

    def refresh_status(self):
        st = read_status()
        if not st:
            self.status_row.set_title("Not running")
            self.status_row.set_subtitle("Press Restart to start it")
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        elif st.get("error"):
            self.status_row.set_title("Needs attention")
            self.status_row.set_subtitle(st["error"])
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        elif st.get("state") == "paused":
            self.status_row.set_title("Paused")
            self.status_row.set_subtitle("Dial control is off")
            self.status_icon.set_from_icon_name("media-playback-pause-symbolic")
        elif st.get("ready"):
            self.status_row.set_title(f"Connected · {st.get('mode', '')} mode")
            parts = [p for p in (st.get("device"), st.get("sink")) if p]
            if not st.get("device"):
                parts.append("presses not available yet")
            self.status_row.set_subtitle(" · ".join(parts))
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
        else:
            self.status_row.set_title("Waiting for the buds")
            self.status_row.set_subtitle("Connect them over Bluetooth and choose them as the sound output")
            self.status_icon.set_from_icon_name("bluetooth-symbolic")
        self.refresh_log()
        return True

    def refresh_log(self):
        try:
            size = os.path.getsize(config.EVENTS_PATH)
        except OSError:
            return
        if size < self._log_pos:
            self._log_pos = 0
        if size == self._log_pos:
            return
        with open(config.EVENTS_PATH) as f:
            f.seek(self._log_pos)
            new = f.read()
            self._log_pos = f.tell()
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), new)
        lines = [ln for ln in new.splitlines() if ln.strip()]
        if lines:
            self.last_label.set_text("Last heard: " + lines[-1][9:])
        adj = self.log_scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def on_clear_log(self, *_):
        self.log_view.get_buffer().set_text("")
        try:
            open(config.EVENTS_PATH, "w").close()
        except OSError:
            pass
        self._log_pos = 0


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main():
    app = App()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
