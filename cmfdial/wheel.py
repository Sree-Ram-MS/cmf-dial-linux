"""On-screen wheel menu (Orbit style). Runs as a helper process of the service.

Reads JSON lines on stdin:
  {"cmd": "show", "modes": [{"name": .., "icon": ..}, ..], "sel": 2}
  {"cmd": "hide"}
  {"cmd": "quit"}

GTK 3 on X11/XWayland is used on purpose: an override-redirect popup is drawn
above every window and never takes keyboard focus, which GNOME on Wayland does
not allow for normal windows started in the background.
"""
import json
import math
import os
import sys

os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

SIZE = 460           # window size (px)
R = 200              # wheel radius
ICON = 26            # icon size
RING = 0.72          # icon ring radius as a fraction of R

DEFAULT_ICONS = {
    "scroll": "input-mouse-symbolic",
    "volume": "audio-volume-high-symbolic",
    "slides": "x-office-presentation-symbolic",
    "tabs": "view-dual-symbolic",
    "arrows": "media-seek-forward-symbolic",
    "zoom": "zoom-in-symbolic",
    "undo": "edit-undo-symbolic",
    "media": "media-playback-start-symbolic",
    "brightness": "display-brightness-symbolic",
    "windows": "focus-windows-symbolic",
}


def icon_for(mode):
    if mode.get("icon"):
        return mode["icon"]
    n = mode.get("name", "").lower()
    for key, icon in DEFAULT_ICONS.items():
        if key in n:
            return icon
    return "applications-system-symbolic"


class Wheel(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.modes = []
        self.sel = 0
        self.anim = 1.0          # 0..1 fade in
        self.hint = "turn to choose \u00b7 press to select"
        self.icons = {}
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_default_size(SIZE, SIZE)
        self.resize(SIZE, SIZE)
        self.set_accept_focus(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.connect("draw", self.on_draw)
        self.connect("realize", self.on_realize)

    def on_realize(self, *_):
        # let mouse clicks pass through the wheel
        self.input_shape_combine_region(cairo.Region())

    # ---------- commands ----------
    def show_wheel(self, modes, sel, hint=None):
        changed = [m.get("name") for m in modes] != [m.get("name") for m in self.modes]
        self.modes = modes
        self.sel = sel % max(1, len(modes))
        if hint:
            self.hint = hint
        if changed:
            self.icons = {}
        if not self.get_visible():
            self.center()
            self.anim = 0.0
            self.show_all()
            GLib.timeout_add(16, self.tick)
        self.queue_draw()

    def hide_wheel(self):
        self.hide()

    def center(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        try:
            _, x, y, _ = disp.get_default_seat().get_pointer().get_position()
            mon = disp.get_monitor_at_point(x, y) or mon
        except Exception:
            pass
        g = mon.get_geometry()
        self.move(g.x + (g.width - SIZE) // 2, g.y + (g.height - SIZE) // 2)

    def tick(self):
        self.anim = min(1.0, self.anim + 0.12)
        self.queue_draw()
        return self.anim < 1.0 and self.get_visible()

    # ---------- drawing ----------
    def icon_pixbuf(self, name, color):
        key = (name, color)
        if key in self.icons:
            return self.icons[key]
        theme = Gtk.IconTheme.get_default()
        info = theme.lookup_icon(name, ICON, Gtk.IconLookupFlags.FORCE_SYMBOLIC | Gtk.IconLookupFlags.FORCE_SIZE)
        if info is None:
            info = theme.lookup_icon("applications-system-symbolic", ICON,
                                     Gtk.IconLookupFlags.FORCE_SYMBOLIC | Gtk.IconLookupFlags.FORCE_SIZE)
        pb = None
        if info is not None:
            rgba = Gdk.RGBA()
            rgba.parse(color)
            try:
                pb, _ = info.load_symbolic(rgba, None, None, None)
            except GLib.Error:
                pb = None
        self.icons[key] = pb
        return pb

    def text(self, cr, s, size, weight, rgba, y):
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(f"Inter, Cantarell, Ubuntu, Sans {weight} {size}")
        layout.set_font_description(fd)
        layout.set_text(s, -1)
        layout.set_width(int(R * 1.1 * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.CENTER)
        w, h = layout.get_pixel_size()
        cr.set_source_rgba(*rgba)
        cr.move_to(SIZE / 2 - R * 0.55, y - h / 2)
        PangoCairo.show_layout(cr, layout)

    def on_draw(self, _w, cr):
        a = 1 - (1 - self.anim) ** 3
        cx = cy = SIZE / 2
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        scale = 0.92 + 0.08 * a
        cr.translate(cx, cy)
        cr.scale(scale, scale)
        cr.translate(-cx, -cy)
        cr.push_group()

        # soft shadow
        for i in range(10, 0, -1):
            cr.arc(cx + 6, cy + 10, R + i * 2, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, 0.035)
            cr.fill()

        # disc
        g = cairo.LinearGradient(cx, cy - R, cx, cy + R)
        g.add_color_stop_rgba(0, 0.105, 0.11, 0.125, 0.97)
        g.add_color_stop_rgba(1, 0.075, 0.08, 0.09, 0.97)
        cr.arc(cx, cy, R, 0, 2 * math.pi)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_line_width(1.2)
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.stroke()

        n = max(1, len(self.modes))
        ring = R * RING
        for i, m in enumerate(self.modes):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            x = cx + ring * math.cos(ang)
            y = cy + ring * math.sin(ang)
            active = i == self.sel
            if active:
                # glow
                rg = cairo.RadialGradient(x, y, 0, x, y, 95)
                rg.add_color_stop_rgba(0, 0.36, 0.45, 0.85, 0.42)
                rg.add_color_stop_rgba(0.45, 0.30, 0.38, 0.75, 0.16)
                rg.add_color_stop_rgba(1, 0.25, 0.30, 0.6, 0.0)
                cr.save()
                cr.arc(cx, cy, R - 1, 0, 2 * math.pi)
                cr.clip()
                cr.set_source(rg)
                cr.paint()
                cr.restore()
            pb = self.icon_pixbuf(icon_for(m), "#ffffff" if active else "#8b8f98")
            if pb:
                Gdk.cairo_set_source_pixbuf(cr, pb, x - pb.get_width() / 2, y - pb.get_height() / 2)
                cr.paint()

        if self.modes:
            name = self.modes[self.sel].get("name", "")
            self.text(cr, name, 24, "Bold", (1, 1, 1, 1), cy - 10)
            self.text(cr, self.hint, 10.5, "", (0.72, 0.74, 0.78, 1), cy + 24)

        cr.pop_group_to_source()
        cr.paint_with_alpha(a)
        return False


def main():
    win = Wheel()
    win.realize()

    buf = {"data": b""}

    def handle(msg):
        cmd = msg.get("cmd")
        if cmd == "show":
            win.show_wheel(msg.get("modes", []), int(msg.get("sel", 0)), msg.get("hint"))
        elif cmd == "hide":
            win.hide_wheel()
        elif cmd == "quit":
            Gtk.main_quit()
            return False
        return True

    def on_input(source, cond):
        data = b""
        if cond & GLib.IO_IN:
            try:
                data = os.read(sys.stdin.fileno(), 65536)
            except OSError:
                data = b""
        if not data:
            Gtk.main_quit()
            return False
        buf["data"] += data
        *lines, buf["data"] = buf["data"].split(b"\n")
        msgs = []
        for line in lines:
            try:
                msgs.append(json.loads(line))
            except ValueError:
                pass
        # only the latest "show" matters; keep order for hide/quit
        for i, msg in enumerate(msgs):
            if msg.get("cmd") == "show" and any(m.get("cmd") == "show" for m in msgs[i + 1:]):
                continue
            if handle(msg) is False:
                return False
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.PRIORITY_DEFAULT,
                      GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_input)
    if "--demo" in sys.argv:
        demo = [{"name": n} for n in ("Volume", "Scroll", "Slides", "Tabs", "Zoom", "Undo / Redo", "Media", "Arrows")]
        win.show_wheel(demo, 2)
    Gtk.main()


if __name__ == "__main__":
    main()
