"""On-screen wheel menu (Orbit style). Runs as a helper process of the service.

Reads JSON lines on stdin:
  {"cmd": "show", "modes": [{"name": .., "icon": ..}, ..], "sel": 2,
   "apps": [{"id": "firefox.desktop", "key": "Super+1"}, ..], "app_sel": 0}
  {"cmd": "hide"}
  {"cmd": "quit"}

The look follows Orbit: a dark ring of mode segments around a light, knurled
knob (like the CMF case dial). The highlighted segment pops out. In Apps mode
a fanned list of apps swings out to the right and scrolls with the dial.

GTK 3 on X11/XWayland is used on purpose: an override-redirect popup is drawn
above every window and never takes keyboard focus, which GNOME on Wayland does
not allow for normal windows started in the background.

Test without the service:  python3 -m cmfdial.wheel --demo [--apps]
Render to a file:           python3 -m cmfdial.wheel --render out.png [--apps]
"""
import json
import math
import os
import sys

if "--render" not in sys.argv:
    os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

W, H = 1000, 600     # window size (px)
CX, CY = 290, 300    # wheel centre inside the window
R_OUT = 176          # ring outer radius
R_IN = 110           # ring inner radius
POP = 20             # how far the highlighted segment sticks out
BEZEL = 104          # dark bezel around the knob
KNOB = 80            # light knob radius
ICON = 24            # mode icon size
APP_ICON = 30        # app icon size in the list
CARD_W, CARD_H = 300, 54
CARD_GAP = 64        # vertical distance between app cards
LIST_X = CX + R_OUT + 92

FONT = "Inter, Cantarell, Ubuntu, Sans"

DEFAULT_ICONS = {
    "scroll": "input-mouse-symbolic",
    "volume": "audio-volume-high-symbolic",
    "slides": "x-office-presentation-symbolic",
    "tabs": "view-dual-symbolic",
    "apps": "view-app-grid-symbolic",
    "arrows": "media-seek-forward-symbolic",
    "zoom": "zoom-in-symbolic",
    "undo": "edit-undo-symbolic",
    "media": "media-playback-start-symbolic",
    "brightness": "display-brightness-symbolic",
    "windows": "focus-windows-symbolic",
}
FALLBACK_ICON = "applications-system-symbolic"


def icon_for(mode):
    if mode.get("icon"):
        return mode["icon"]
    if mode.get("kind") == "apps":
        return DEFAULT_ICONS["apps"]
    n = mode.get("name", "").lower()
    for key, icon in DEFAULT_ICONS.items():
        if key in n:
            return icon
    return FALLBACK_ICON


def ease(t):
    return 1 - (1 - t) ** 3


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def segment_path(cr, cx, cy, r_in, r_out, a0, a1):
    cr.new_path()
    cr.arc(cx, cy, r_out, a0, a1)
    cr.arc_negative(cx, cy, r_in, a1, a0)
    cr.close_path()


class Painter:
    """Draws the wheel and the app list. Shared by the window and --render."""

    def __init__(self):
        self.modes = []
        self.sel = 0
        self.apps = []          # [{"id", "key", "name", "pixbuf"}]
        self.app_sel = 0
        self.app_pos = 0.0      # animated scroll position of the list
        self.knob = 0.0         # animated knob rotation
        self.hl = 0.0           # animated highlight angle index
        self.anim = 1.0         # 0..1 fade in
        self.list_anim = 1.0    # 0..1 swing-in of the app list
        self.title = None
        self.hint = None
        self.icons = {}
        self.theme = Gtk.IconTheme.get_default()

    # ---------- data ----------
    def set_state(self, modes, sel, apps=None, app_sel=0, title=None, hint=None):
        if [m.get("name") for m in modes] != [m.get("name") for m in self.modes]:
            self.icons = {k: v for k, v in self.icons.items() if k[0] == "app"}
        self.modes = modes
        n = max(1, len(modes))
        new_sel = sel % n
        # step the animated highlight the short way round
        d = (new_sel - self.sel) % n
        if d > n / 2:
            d -= n
        self.sel = new_sel
        self.hl_target = getattr(self, "hl_target", float(self.sel)) + d
        self.knob_target = getattr(self, "knob_target", 0.0) + d * (2 * math.pi / n)
        had_apps = bool(self.apps)
        self.apps = [self.resolve_app(a) for a in (apps or [])]
        if self.apps and not had_apps:
            self.list_anim = 0.0
            self.app_pos = float(app_sel)
        self.app_sel = app_sel % max(1, len(self.apps)) if self.apps else 0
        self.title = title
        self.hint = hint

    def snap(self):
        self.hl = self.hl_target = float(self.sel)
        self.knob = self.knob_target = getattr(self, "knob_target", 0.0)
        self.app_pos = float(self.app_sel)
        self.anim = self.list_anim = 1.0

    def step(self):
        """Advance animations; returns True while anything still moves."""
        moving = False
        self.anim = min(1.0, self.anim + 0.11)
        self.list_anim = min(1.0, self.list_anim + 0.09)
        for attr, target in (("hl", self.hl_target), ("knob", self.knob_target),
                             ("app_pos", float(self.app_sel))):
            cur = getattr(self, attr)
            if abs(target - cur) > 0.002:
                setattr(self, attr, cur + (target - cur) * 0.3)
                moving = True
            else:
                setattr(self, attr, target)
        return moving or self.anim < 1.0 or (self.apps and self.list_anim < 1.0)

    def resolve_app(self, a):
        app_id = a.get("id", "")
        key = ("app", app_id)
        if key not in self.icons:
            name, pb = app_id.replace(".desktop", ""), None
            try:
                info = Gio.DesktopAppInfo.new(app_id)
            except TypeError:
                info = None
            if info:
                name = info.get_name() or name
                gicon = info.get_icon()
                if gicon:
                    li = self.theme.lookup_by_gicon(gicon, APP_ICON * 2, Gtk.IconLookupFlags.FORCE_SIZE)
                    if li:
                        try:
                            pb = li.load_icon()
                        except GLib.Error:
                            pb = None
            if pb is None:
                pb = self.symbolic("application-x-executable-symbolic", "#ffffff", APP_ICON)
            self.icons[key] = (name, pb)
        name, pb = self.icons[key]
        return {"id": app_id, "key": a.get("key", ""), "name": a.get("name") or name, "pixbuf": pb}

    def symbolic(self, name, color, size=ICON):
        key = (name, color, size)
        if key in self.icons:
            return self.icons[key]
        flags = Gtk.IconLookupFlags.FORCE_SYMBOLIC | Gtk.IconLookupFlags.FORCE_SIZE
        info = self.theme.lookup_icon(name, size, flags) or self.theme.lookup_icon(FALLBACK_ICON, size, flags)
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

    # ---------- helpers ----------
    @staticmethod
    def text(cr, s, size, weight, rgba, x, y, align="center", max_w=200):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"{FONT} {weight} {size}"))
        layout.set_text(s, -1)
        layout.set_width(int(max_w * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment({"center": Pango.Alignment.CENTER, "left": Pango.Alignment.LEFT,
                              "right": Pango.Alignment.RIGHT}[align])
        _, h = layout.get_pixel_size()
        left = {"center": x - max_w / 2, "left": x, "right": x - max_w}[align]
        cr.set_source_rgba(*rgba)
        cr.move_to(left, y - h / 2)
        PangoCairo.show_layout(cr, layout)

    @staticmethod
    def pixbuf(cr, pb, x, y, size):
        if not pb:
            return
        cr.save()
        s = size / max(pb.get_width(), pb.get_height())
        cr.translate(x - size / 2, y - size / 2)
        cr.scale(s, s)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        cr.restore()

    # ---------- drawing ----------
    def draw(self, cr):
        a = ease(self.anim)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if not self.modes:
            return
        cr.push_group()
        cr.save()
        sc = 0.9 + 0.1 * a
        cr.translate(CX, CY)
        cr.scale(sc, sc)
        cr.translate(-CX, -CY)
        self.draw_wheel(cr)
        cr.restore()
        if self.apps:
            self.draw_apps(cr)
        cr.pop_group_to_source()
        cr.paint_with_alpha(a)

    def seg_angle(self, i):
        n = max(1, len(self.modes))
        return -math.pi / 2 + 2 * math.pi * i / n

    def draw_wheel(self, cr):
        n = max(1, len(self.modes))
        span = 2 * math.pi / n
        gap = min(0.05, span * 0.06)

        # shadow
        for i in range(14, 0, -1):
            cr.arc(CX + 4, CY + 12, R_OUT + i * 2.2, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, 0.018)
            cr.fill()

        # ring base
        cr.arc(CX, CY, R_OUT, 0, 2 * math.pi)
        g = cairo.LinearGradient(CX, CY - R_OUT, CX, CY + R_OUT)
        g.add_color_stop_rgb(0, 0.17, 0.175, 0.19)
        g.add_color_stop_rgb(1, 0.11, 0.115, 0.125)
        cr.set_source(g)
        cr.fill()

        hl = self.hl % n
        for i, m in enumerate(self.modes):
            mid = self.seg_angle(i)
            a0, a1 = mid - span / 2 + gap / 2, mid + span / 2 - gap / 2
            # how "selected" this segment is right now (smooth during animation)
            dist = min(abs(i - hl), n - abs(i - hl))
            t = max(0.0, 1.0 - dist)
            if t < 1:
                segment_path(cr, CX, CY, R_IN, R_OUT - 3, a0, a1)
                cr.set_source_rgba(1, 1, 1, 0.025)
                cr.fill_preserve()
                cr.set_line_width(1)
                cr.set_source_rgba(1, 1, 1, 0.05)
                cr.stroke()
            icon_r = (R_IN + R_OUT) / 2
            if t > 0:
                self.draw_popped(cr, a0, a1, t)
                icon_r += POP * 0.5 * t
            col = "#ffffff" if t > 0.5 else "#a4a7ae"
            pb = self.symbolic(icon_for(m), col)
            self.pixbuf(cr, pb, CX + icon_r * math.cos(mid), CY + icon_r * math.sin(mid), ICON)

        # bezel
        cr.arc(CX, CY, BEZEL, 0, 2 * math.pi)
        cr.set_source_rgb(0.055, 0.058, 0.065)
        cr.fill()
        rg = cairo.RadialGradient(CX, CY, KNOB, CX, CY, BEZEL)
        rg.add_color_stop_rgba(0, 0, 0, 0, 0.6)
        rg.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.arc(CX, CY, BEZEL, 0, 2 * math.pi)
        cr.set_source(rg)
        cr.fill()
        self.draw_knob(cr)

    def draw_popped(self, cr, a0, a1, t):
        r_out = R_OUT + POP * t
        r_in = R_IN + 2
        segment_path(cr, CX, CY, r_in, r_out, a0, a1)
        mid = (a0 + a1) / 2
        x0, y0 = CX + r_in * math.cos(mid), CY + r_in * math.sin(mid)
        x1, y1 = CX + r_out * math.cos(mid), CY + r_out * math.sin(mid)
        g = cairo.LinearGradient(x0, y0, x1, y1)
        g.add_color_stop_rgba(0, 0.30, 0.31, 0.33, t)
        g.add_color_stop_rgba(1, 0.46, 0.47, 0.50, t)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_line_width(1.2)
        cr.set_source_rgba(1, 1, 1, 0.16 * t)
        cr.stroke()
        # ribs, like the grip on the CMF case dial
        cr.save()
        segment_path(cr, CX, CY, r_in, r_out, a0, a1)
        cr.clip()
        cr.set_line_width(1.2)
        step = 5.5 / ((r_in + r_out) / 2)
        ang = a0 + step / 2
        while ang < a1:
            c, s = math.cos(ang), math.sin(ang)
            cr.move_to(CX + (r_in + 8) * c, CY + (r_in + 8) * s)
            cr.line_to(CX + (r_out + 2) * c, CY + (r_out + 2) * s)
            cr.set_source_rgba(0, 0, 0, 0.20 * t)
            cr.stroke()
            c2, s2 = math.cos(ang + 0.006), math.sin(ang + 0.006)
            cr.move_to(CX + (r_in + 8) * c2, CY + (r_in + 8) * s2)
            cr.line_to(CX + (r_out + 2) * c2, CY + (r_out + 2) * s2)
            cr.set_source_rgba(1, 1, 1, 0.07 * t)
            cr.stroke()
            ang += step
        cr.restore()

    def draw_knob(self, cr):
        # knurled rim
        g = cairo.LinearGradient(CX - KNOB, CY - KNOB, CX + KNOB, CY + KNOB)
        g.add_color_stop_rgb(0, 0.96, 0.96, 0.95)
        g.add_color_stop_rgb(1, 0.80, 0.80, 0.79)
        cr.arc(CX, CY, KNOB, 0, 2 * math.pi)
        cr.set_source(g)
        cr.fill()
        cr.set_line_width(1)
        for k in range(144):
            ang = self.knob + 2 * math.pi * k / 144
            c, s = math.cos(ang), math.sin(ang)
            cr.move_to(CX + (KNOB - 7) * c, CY + (KNOB - 7) * s)
            cr.line_to(CX + (KNOB - 0.5) * c, CY + (KNOB - 0.5) * s)
            cr.set_source_rgba(0, 0, 0, 0.22)
            cr.stroke()
        # face
        face = KNOB - 9
        g = cairo.RadialGradient(CX - 20, CY - 26, 4, CX, CY, face)
        g.add_color_stop_rgb(0, 0.995, 0.995, 0.99)
        g.add_color_stop_rgb(1, 0.90, 0.90, 0.89)
        cr.arc(CX, CY, face, 0, 2 * math.pi)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.10)
        cr.set_line_width(1)
        cr.stroke()
        # two indicator marks that turn with the dial
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for off in (-math.pi * 0.72, math.pi * 0.28):
            ang = self.knob + off
            c, s = math.cos(ang), math.sin(ang)
            cr.move_to(CX + (face - 13) * c, CY + (face - 13) * s)
            cr.line_to(CX + (face - 4) * c, CY + (face - 4) * s)
            cr.set_source_rgba(0.13, 0.13, 0.14, 0.85)
            cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        # label
        m = self.modes[self.sel]
        self.pixbuf(cr, self.symbolic(icon_for(m), "#2b2c30", 22), CX, CY - 13, 22)
        self.text(cr, self.title or m.get("name", ""), 11.5, "Semibold", (0.17, 0.17, 0.19, 1),
                  CX, CY + 15, max_w=face * 1.5)
        if self.hint:
            w = 230
            rounded_rect(cr, CX - w / 2, CY + R_OUT + POP + 18, w, 28, 14)
            cr.set_source_rgba(0.08, 0.085, 0.095, 0.88)
            cr.fill()
            self.text(cr, self.hint, 9.5, "", (0.85, 0.86, 0.9, 1), CX, CY + R_OUT + POP + 32, max_w=w - 16)

    def draw_apps(self, cr):
        la = ease(self.list_anim)
        n = len(self.apps)
        # connector from the highlighted segment to the selected card
        mid = self.seg_angle(self.sel)
        if math.cos(mid) > 0.15:
            sx, sy = CX + (R_OUT + POP) * math.cos(mid), CY + (R_OUT + POP) * math.sin(mid)
        else:
            sx, sy = CX + R_OUT + 6, CY
        ex, ey = LIST_X - 4, CY
        cr.set_line_width(1.6)
        cr.set_source_rgba(0.35, 0.36, 0.39, 0.9 * la)
        cr.move_to(sx, sy)
        cr.line_to(sx + (ex - sx) * 0.45, sy)
        cr.line_to(ex - (ex - sx) * 0.25, ey)
        cr.line_to(ex, ey)
        cr.stroke()

        order = sorted(range(n), key=lambda i: -abs(i - self.app_pos))
        for i in order:
            k = i - self.app_pos
            ak = abs(k)
            if ak > 2.9:
                continue
            sel_t = max(0.0, 1.0 - ak)  # 1 for the selected card
            alpha = max(0.0, 1.0 - ak * 0.32) * la
            # cards sit like spokes of the dial, so the list curves around it
            spoke = 470
            ang = k * (CARD_GAP / spoke) * (0.4 + 0.6 * la)
            x = LIST_X - spoke + spoke * math.cos(ang) - (1 - la) * 50
            y = CY + spoke * math.sin(ang)
            sc = 1.0 - min(ak, 3) * 0.05
            cr.save()
            cr.translate(x, y)
            cr.rotate(ang)
            cr.scale(sc, sc)
            # card
            rounded_rect(cr, 0, -CARD_H / 2, CARD_W, CARD_H, 14)
            dark = (0.10, 0.105, 0.115)
            grey = (0.30, 0.31, 0.34)
            col = [grey[j] + (dark[j] - grey[j]) * sel_t for j in range(3)]
            cr.set_source_rgba(*col, (0.78 + 0.18 * sel_t) * alpha)
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_source_rgba(1, 1, 1, (0.10 + 0.08 * sel_t) * alpha)
            cr.stroke()
            app = self.apps[i]
            # icon tile
            cr.push_group()
            self.pixbuf(cr, app["pixbuf"], 32, 0, APP_ICON)
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha)
            fg = 1.0 if sel_t > 0.5 else 0.93
            self.text(cr, app["name"], 14 if sel_t > 0.5 else 13, "Bold" if sel_t > 0.5 else "Semibold",
                      (fg, fg, fg, alpha * (1 if sel_t > 0.5 else 0.82)), 58, 0, align="left",
                      max_w=CARD_W - 58 - 74)
            if app.get("key"):
                self.text(cr, app["key"], 9.5, "Medium", (0.78, 0.79, 0.82, alpha * 0.85),
                          CARD_W - 14, 0, align="right", max_w=70)
            cr.restore()


class Wheel(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.p = Painter()
        self.ticking = False
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_default_size(W, H)
        self.resize(W, H)
        self.set_accept_focus(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.connect("draw", lambda _w, cr: self.p.draw(cr) or False)
        self.connect("realize", self.on_realize)

    def on_realize(self, *_):
        # let mouse clicks pass through the wheel
        self.input_shape_combine_region(cairo.Region())

    def show_wheel(self, msg):
        self.p.set_state(msg.get("modes", []), int(msg.get("sel", 0)), msg.get("apps"),
                         int(msg.get("app_sel", 0)), msg.get("title"), msg.get("hint"))
        if not self.get_visible():
            self.p.snap()
            self.p.anim = 0.0
            if self.p.apps:
                self.p.list_anim = 0.0
            self.center()
            self.show_all()
        self.start_tick()
        self.queue_draw()

    def start_tick(self):
        if not self.ticking:
            self.ticking = True
            GLib.timeout_add(16, self.tick)

    def tick(self):
        moving = self.p.step()
        self.queue_draw()
        if moving and self.get_visible():
            return True
        self.ticking = False
        return False

    def hide_wheel(self):
        self.hide()
        self.p.apps = []

    def center(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        try:
            _, x, y = disp.get_default_seat().get_pointer().get_position()
            mon = disp.get_monitor_at_point(x, y) or mon
        except Exception:
            pass
        g = mon.get_geometry()
        # wheel a little left of centre, so the app list fits on the right
        self.move(g.x + g.width // 2 - CX - 120, g.y + (g.height - H) // 2)


DEMO_MODES = [{"name": n} for n in ("Volume", "Brightness", "Apps", "Tabs", "Windows", "Slides", "Scroll", "Undo")]
DEMO_MODES[2]["kind"] = "apps"
DEMO_APPS = [{"id": i, "key": f"Super+{k + 1}"} for k, i in enumerate(
    ("org.gnome.Nautilus.desktop", "google-chrome.desktop", "firefox_firefox.desktop",
     "org.gnome.Terminal.desktop", "code.desktop", "org.gnome.Settings.desktop"))]


def render(path, with_apps):
    p = Painter()
    p.set_state(DEMO_MODES, 2, DEMO_APPS if with_apps else None, 2 if with_apps else 0,
                hint=None if with_apps else "turn to choose \u00b7 press to select")
    p.snap()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)
    # a light backdrop so the result is easy to judge
    bg = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    bcr = cairo.Context(bg)
    bcr.set_source_rgb(0.93, 0.92, 0.90)
    bcr.paint()
    p.draw(cr)
    bcr.set_source_surface(surf)
    bcr.paint()
    bg.write_to_png(path)


def main():
    if "--render" in sys.argv:
        out = sys.argv[sys.argv.index("--render") + 1]
        render(out, "--apps" in sys.argv)
        return

    win = Wheel()
    win.realize()
    buf = {"data": b""}

    def handle(msg):
        cmd = msg.get("cmd")
        if cmd == "show":
            win.show_wheel(msg)
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
        apps = "--apps" in sys.argv
        win.show_wheel({"modes": DEMO_MODES, "sel": 2, "apps": DEMO_APPS if apps else None, "app_sel": 1})
    Gtk.main()


if __name__ == "__main__":
    main()
