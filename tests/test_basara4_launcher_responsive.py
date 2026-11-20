"""The settings panel of the launcher fits at every Windows scale and window size: nothing in it is cut off or out of
reach, the notes wrap instead of running past the panel, the Game section reads as one group, and a drag of the window
edge never leaves overlapping widgets.

The rule: every widget of the right-hand panel is inside the panel's width, and every widget can be brought into the
visible part of the panel (the panel scrolls when it is taller than the window); the PLAY button and the left panel do
not move when the panel scrolls. Measured with no window shown (alpha 0, off screen), the Windows scale (Tk scaling)
set BEFORE the window is made: a window made first does not lay out at the new scale."""
import tempfile
import unittest
from unittest import mock

from launcher_fixtures import bl
from test_basara4_launcher_tidy import current_scaling, find, set_scaling
from test_basara4_launcher_window import descendants, launcher

tk, ttk = bl.tk, bl.ttk

SCALES = (1.0, 1.25, 1.5)
SIZES = ("1060x685", "1020x650")  # the window as it opens, and the smallest it can be
INFO_TEXTS = ("No eboot.bin in this folder.", "Game image ready.",
              "The game image is made on the first start (about a minute); again if eboot.bin changes.")
LONG_INFO = INFO_TEXTS[2]


def position(widget, ancestor):
    """x, y of the widget inside the ancestor."""
    x = y = 0
    while widget is not ancestor:
        x += widget.winfo_x()
        y += widget.winfo_y()
        widget = widget.master
    return x, y


def leaves(body):
    """The widgets of the panel that draw something (frames only hold them)."""
    return [w for w in descendants(body) if w.winfo_class() not in ("TFrame", "Frame") and w.winfo_ismapped()]


def show(app, size):
    app.attributes("-alpha", 0.0)
    app.geometry(f"{size}+-3000+0")
    app.update()
    app.update()  # the second pass lays out what the first one wrapped


class PanelFitTests(unittest.TestCase):
    def check_panel(self, app, where):
        panel, body = app.right_panel, app.right_panel.body
        pad = bl.ScrolledPanel.PAD
        width = body.winfo_width()
        # 1. inside the width: no widget runs past the panel, no note is wider than the room it has
        for widget in leaves(body):
            x, _y = position(widget, body)
            self.assertGreaterEqual(x, pad - 1, f"{where}: {widget} starts left of the panel")
            self.assertLessEqual(x + widget.winfo_width(), width - pad + 2,
                                 f"{where}: {widget.winfo_class()} {widget.cget('text') if 'text' in widget.keys() else ''!r} "
                                 f"runs past the right edge ({x + widget.winfo_width()} of {width - pad})")
            if isinstance(widget, ttk.Label):
                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth() - 1,
                                        f"{where}: the text {widget.cget('text')!r} is cut off")
        # 2. in reach: it fits the window, or the panel scrolls and every widget can be brought into view. The room
        # of the scroll bar is kept whether the bar shows or not: showing it must not change the width the notes
        # wrap at (a changed width changes the height, which would show or hide the bar again).
        self.assertGreaterEqual(panel.winfo_width() - panel.canvas.winfo_width(), panel.bar.winfo_reqwidth(),
                                f"{where}: the room of the scroll bar is not kept")
        self.assertEqual(body.winfo_width(), panel.canvas.winfo_width())
        shown = panel.canvas.winfo_height()
        need = body.winfo_reqheight()
        if need <= shown:
            self.assertFalse(panel.bar.winfo_ismapped(), f"{where}: a scroll bar without anything to scroll")
        else:
            self.assertTrue(panel.bar.winfo_ismapped(), f"{where}: taller than the window and no scroll bar")
            self.assertLess(panel.bar.get()[1] - panel.bar.get()[0], 1.0)
        for widget in leaves(body):
            panel.reveal(widget)
            app.update_idletasks()
            top = panel.canvas.canvasy(0)
            y = position(widget, body)[1]
            self.assertGreaterEqual(y, top - 1, f"{where}: {widget} cannot be scrolled into view (top)")
            self.assertLessEqual(y + widget.winfo_height(), top + shown + 1,
                                 f"{where}: {widget} cannot be scrolled into view (bottom)")
        panel.canvas.yview_moveto(0)
        # 3. no overlap: rows of the panel one under the other; widgets of one flowing row apart
        rows = {}
        for child in body.winfo_children():
            if child.grid_info() and child.winfo_ismapped():
                rows.setdefault(int(child.grid_info()["row"]), []).append(child)
        previous = None
        for number in sorted(rows):
            top = min(position(w, body)[1] for w in rows[number])
            bottom = max(position(w, body)[1] + w.winfo_height() for w in rows[number])
            if previous is not None:
                self.assertLessEqual(previous[1], top, f"{where}: row {number} overlaps row {previous[0]}")
            previous = (number, bottom)
        for flow in (w for w in descendants(body) if isinstance(w, bl.FlowRow)):
            boxes = [(w.winfo_x(), w.winfo_y(), w.winfo_x() + w.winfo_width(), w.winfo_y() + w.winfo_height())
                     for w, _gap in flow.items]
            for i, a in enumerate(boxes):
                self.assertLessEqual(a[2], flow.winfo_width() + 1, f"{where}: a widget of a flowing row is cut off")
                for b in boxes[i + 1:]:
                    apart = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                    self.assertTrue(apart, f"{where}: two widgets of one row overlap {a} {b}")

    def check_game_section(self, app, where):
        body = app.right_panel.body
        browse, cheats, dlc = (find(app, ttk.Button, t) for t in ("Browse…", "Cheats…", "Manage DLC…"))
        self.assertEqual(browse.winfo_width(), cheats.winfo_width(), f"{where}: Browse and Cheats differ in width")
        self.assertEqual(cheats.winfo_width(), dlc.winfo_width(), f"{where}: Cheats and Manage DLC differ in width")
        self.assertEqual(browse.winfo_height(), cheats.winfo_height())
        gap = position(dlc, body)[0] - (position(cheats, body)[0] + cheats.winfo_width())
        self.assertGreaterEqual(gap, 8, f"{where}: the buttons are closer than 8 px")
        entry = next(w for w in descendants(body) if isinstance(w, ttk.Entry))
        self.assertEqual(position(cheats, body)[0], position(entry, body)[0],
                         f"{where}: Cheats does not start where the folder entry starts")
        heads = {str(w.cget("text")): w for w in descendants(body)
                 if isinstance(w, ttk.Label) and str(w.cget("style")) == "Head.TLabel"}
        game_row = int(heads["Game"].grid_info()["row"])
        self.assertEqual(int(heads["Game"].grid_info()["columnspan"]), 3)  # the heading stands alone
        self.assertEqual(int(app.folder_label.grid_info()["row"]), game_row + 1)
        self.assertEqual(int(app.image_label.grid_info()["row"]), game_row + 2)
        self.assertEqual(int(app.addons_label.grid_info()["row"]), game_row + 3)
        self.assertEqual(int(cheats.master.grid_info()["row"]), game_row + 3)
        self.assertEqual(position(app.addons_label, body)[0], position(app.folder_label, body)[0])
        info_bottom = position(app.image_label, body)[1] + app.image_label.winfo_height()
        gap = position(cheats.master, body)[1] - info_bottom
        self.assertTrue(12 <= gap <= 14, f"{where}: {gap} px between the note and the Add-ons row")
        self.assertGreater(position(cheats, body)[1], position(browse, body)[1] + browse.winfo_height())
        self.assertIn(str(app.image_label.cget("text")), INFO_TEXTS)  # the text itself is as it was
        self.assertEqual(str(app.addons_label.cget("style")), str(app.folder_label.cget("style")))

    def test_every_scale_and_size_fits_and_every_widget_is_in_reach(self):
        original = current_scaling()
        try:
            for scale in SCALES:
                set_scaling(96 / 72 * scale)
                with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                    app.image_label.config(text=LONG_INFO)  # the longest note
                    for size in SIZES:
                        where = f"{int(scale * 100)}% {size}"
                        show(app, size)
                        self.check_panel(app, where)
                        self.check_game_section(app, where)
        finally:
            set_scaling(original)

    def test_the_longest_note_wraps_in_the_room_of_its_column(self):
        original = current_scaling()
        try:
            for scale in SCALES:
                set_scaling(96 / 72 * scale)
                with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                    app.image_label.config(text=LONG_INFO)
                    show(app, "1020x650")
                    label = app.image_label
                    self.assertEqual(label.cget("text"), LONG_INFO)
                    self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth() - 1)
                    self.assertLessEqual(int(str(label.cget("wraplength"))),
                                         app.right_panel.body.winfo_width() - 2 * bl.ScrolledPanel.PAD)
                    if scale > 1.0:  # the line is longer than the column: it takes more than one line
                        one_line = app.tk.call("font", "metrics", "TkDefaultFont", "-linespace")
                        self.assertGreater(label.winfo_height(), int(one_line))
        finally:
            set_scaling(original)

    def test_dragging_the_window_edge_never_breaks_the_layout(self):
        original = current_scaling()
        try:
            for scale in (1.0, 1.25):
                set_scaling(96 / 72 * scale)
                with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                    app.image_label.config(text=LONG_INFO)
                    for size in ("1020x650", "1500x900", "1100x700", "1020x650", "1300x660", "1020x650"):
                        show(app, size)
                        where = f"{int(scale * 100)}% after the drag to {size}"
                        self.check_panel(app, where)
                        self.check_game_section(app, where)
        finally:
            set_scaling(original)

    def test_the_play_button_and_the_left_panel_stay_when_the_panel_scrolls(self):
        original = current_scaling()
        try:
            set_scaling(96 / 72 * 1.5)
            with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                show(app, "1020x650")
                panel = app.right_panel
                self.assertTrue(panel.scrolls, "the test needs a window where the panel scrolls")
                before = (app.play.winfo_rootx(), app.play.winfo_rooty(), app.play.winfo_width(), app.play.winfo_height(),
                          app.status.winfo_rooty())
                panel.canvas.yview_moveto(1.0)
                app.update()
                self.assertGreater(panel.canvas.canvasy(0), 0)
                after = (app.play.winfo_rootx(), app.play.winfo_rooty(), app.play.winfo_width(), app.play.winfo_height(),
                         app.status.winfo_rooty())
                self.assertEqual(before, after)
                self.assertTrue(app.play.winfo_ismapped())
        finally:
            set_scaling(original)

    def test_the_mouse_wheel_scrolls_the_panel_but_not_a_drop_down_or_other_windows(self):
        original = current_scaling()
        try:
            set_scaling(96 / 72 * 1.5)
            with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                show(app, "1020x650")
                panel = app.right_panel
                button = find(app, ttk.Button, "Open saves")
                combo = next(w for w in descendants(panel.body) if isinstance(w, ttk.Combobox))
                event = mock.Mock(x_root=0, y_root=0, delta=-120)  # one notch down
                with mock.patch.object(panel, "winfo_containing", return_value=combo):
                    panel.wheel(event)
                self.assertEqual(panel.canvas.canvasy(0), 0, "the wheel over a drop-down must leave the panel alone")
                with mock.patch.object(panel, "winfo_containing", return_value=app.play):
                    panel.wheel(event)
                self.assertEqual(panel.canvas.canvasy(0), 0, "the wheel over the left panel must not scroll the right one")
                with mock.patch.object(panel, "winfo_containing", return_value=button):
                    panel.wheel(event)
                    first = panel.canvas.canvasy(0)
                    self.assertGreater(first, 0)
                    panel.wheel(mock.Mock(x_root=0, y_root=0, delta=120))  # one notch up
                self.assertLess(panel.canvas.canvasy(0), first)
                # a window big enough for everything has nothing to scroll
                show(app, "1500x1100")
                self.assertFalse(panel.scrolls)
                with mock.patch.object(panel, "winfo_containing", return_value=button):
                    panel.wheel(event)
                self.assertEqual(panel.canvas.canvasy(0), 0)
        finally:
            set_scaling(original)

    def test_a_widget_that_takes_the_focus_is_scrolled_into_view(self):
        original = current_scaling()
        try:
            set_scaling(96 / 72 * 1.5)
            with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                show(app, "1020x650")
                panel = app.right_panel
                last = find(app, ttk.Button, "Open saves")
                self.assertEqual(panel.canvas.canvasy(0), 0)
                last.event_generate("<FocusIn>")  # the same event the keyboard (Tab) causes
                app.update()
                top = panel.canvas.canvasy(0)
                y = position(last, panel.body)[1]
                self.assertGreater(top, 0)
                self.assertLessEqual(y + last.winfo_height(), top + panel.canvas.winfo_height() + 1)
                find(app, ttk.Button, "Browse…").event_generate("<FocusIn>")
                app.update()
                self.assertEqual(panel.canvas.canvasy(0), 0)  # back up for a widget at the top
        finally:
            set_scaling(original)


class TabOrderTests(unittest.TestCase):
    def test_tab_goes_through_the_old_widgets_in_their_old_order_and_never_to_the_scrolling_parts(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            panel = app.right_panel
            ok = lambda w: bool(int(app.tk.call("tk::FocusOK", str(w))))  # noqa: E731
            self.assertFalse(ok(panel.canvas), "the canvas must not take the Tab focus")
            self.assertFalse(ok(panel.bar))
            focusable = [w for w in descendants(panel.body) if ok(w)]
            texts = [str(w.cget("text")).strip() if "text" in w.keys() else w.winfo_class() for w in focusable]
            # the order the widgets were made in (top to bottom, left to right): the Game section, Display, options,
            # the Tracer row, Input Mode, the tool rows
            self.assertEqual(texts[:6], ["TEntry", "Browse…", "Cheats…", "Manage DLC…", "Windowed", "Borderless fullscreen"])
            self.assertEqual(texts[-6:], ["⌨️ Keyboard & Mouse Controls", "Controller Mapping", "Open log", "Open audio log",
                                           "Open captures", "Open saves"])
            self.assertLess(texts.index("Report"), texts.index("Open render log"))
            self.assertLess(texts.index("Open render log"), texts.index("Controller Mapping") - 3)


class GameSectionKeepsItsFunctionTests(unittest.TestCase):
    def test_browse_cheats_and_manage_dlc_still_do_what_they_did(self):
        with mock.patch.object(bl.Launcher, "open_cheats_manager") as cheats, \
                mock.patch.object(bl.Launcher, "open_dlc_manager") as dlc, \
                mock.patch.object(bl.filedialog, "askdirectory", return_value="C:/some/folder") as ask, \
                tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            find(app, ttk.Button, "Cheats…").invoke()
            find(app, ttk.Button, "Manage DLC…").invoke()
            find(app, ttk.Button, "Browse…").invoke()
            cheats.assert_called_once_with()
            dlc.assert_called_once_with()
            ask.assert_called_once()
            self.assertTrue(app.game_var.get().replace("\\", "/").endswith("some/folder"))

    def test_the_old_widgets_are_all_still_in_the_panel(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            body = app.right_panel.body
            texts = {str(w.cget("text")).strip() for w in descendants(body) if "text" in w.keys()}
            for text in ("Game", "Game folder", "Add-ons", "Browse…", "Cheats…", "Manage DLC…", "Display", "Windowed",
                         "Borderless fullscreen", "Exclusive fullscreen", "Graphics card", "Frame limit", "Anisotropic",
                         "Resolution", "Upscaling", "VSync", "Sharpening (RCAS)", "Tracer", "Report", "Open render log",
                         "Controller & Input", "Input Mode:", "Open log", "Open audio log", "Open captures",
                         "Open saves", "Controller Mapping", "⌨️ Keyboard & Mouse Controls"):
                self.assertIn(text, texts, text)
            # PLAY and the left panel are not in the scrolling panel
            self.assertFalse(app.right_panel.contains(app.play))
            self.assertFalse(app.right_panel.contains(app.stop))


if __name__ == "__main__":
    unittest.main()
