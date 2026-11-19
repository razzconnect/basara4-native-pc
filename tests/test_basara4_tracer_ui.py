"""The launcher window of the Tracer: one Tracer option (it replaces "Render debug trace" and "Audio debug
log", and a player who had either on keeps it on), and the Report button beside it. A real Tk window
is made like the other launcher tests; no game starts and no report is written.
"""
import tempfile
import unittest
from unittest import mock

from launcher_fixtures import bl
from test_basara4_launcher_window import descendants, launcher

ttk = bl.ttk


def widget(app, kind, text):
    return next(w for w in descendants(app) if isinstance(w, kind) and str(w.cget("text")) == text)


class TracerOptionTests(unittest.TestCase):
    def test_the_old_options_become_one_tracer(self):
        for cfg, expected in (({}, False), ({"render_trace": True}, True), ({"audio_log": True}, True),
                              ({"render_trace": True, "audio_log": True}, True)):
            with tempfile.TemporaryDirectory() as tmp, launcher(tmp, cfg=cfg) as (app, paths, game, _):
                self.assertEqual(app.tracer_var.get(), expected, cfg)

    def test_the_report_button_sits_with_the_tracer_and_makes_the_report(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            report, tracer = widget(app, ttk.Button, "Report"), widget(app, ttk.Checkbutton, "Tracer")
            self.assertIs(report.master, tracer.master)  # the same row as the option
            siblings = {str(w.cget("text")) for w in report.master.winfo_children() if isinstance(w, ttk.Button)}
            self.assertNotIn("Map controller", siblings)  # not in the controller row
            # The button asks what the player saw first (on_report), which then makes the report.
            with mock.patch.object(bl.Launcher, "on_report") as ask, mock.patch.object(bl.Launcher, "send_report") as send:
                report.invoke()
            ask.assert_called_once_with()
            send.assert_not_called()

    def test_the_tracer_row_fits_its_panel_and_does_not_widen_it(self):
        # The hint beside the Tracer was cut off ("...then R") and pushed the dropdown columns to the right:
        # measured in a real window (alpha 0, off screen) at the Windows scalings 100%, 125% and 150%.
        for scale in (1.0, 1.25, 1.5):
            with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                app.attributes("-alpha", 0.0)
                app.geometry("+-3000+0")
                # The scaling belongs to the Tk interpreter that the next test's window shares: give it back
                # inside the with-block (a cleanup would run after the window is gone).
                original_scaling = app.tk.call("tk", "scaling")
                try:
                    app.tk.call("tk", "scaling", 96 / 72 * scale)
                    app.update()
                    row = widget(app, ttk.Checkbutton, "Tracer").master
                    panel = row.master
                    with_row = panel.winfo_reqwidth()
                    self.assertLessEqual(row.winfo_reqwidth(), panel.winfo_width(), f"cut off at {scale}")
                    hint = [w for w in row.winfo_children() if isinstance(w, ttk.Label)]
                    self.assertEqual(len(hint), 1)
                    self.assertIn("Report", str(hint[0].cget("text")))  # the part that matters is still said
                    row.grid_remove()
                    app.update()
                    self.assertEqual(panel.winfo_reqwidth(), with_row, f"the row widens the panel at {scale}")
                finally:
                    app.tk.call("tk", "scaling", original_scaling)

    def test_the_width_test_leaves_the_tk_scaling_as_it_found_it(self):
        """The width test must not change what the next window is measured with."""
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            before = float(app.tk.call("tk", "scaling"))
        self.test_the_tracer_row_fits_its_panel_and_does_not_widen_it()
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.assertEqual(float(app.tk.call("tk", "scaling")), before)

    def test_the_tracer_off_turns_the_wav_capture_off_and_the_wav_turns_the_tracer_on(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            app.tracer_var.set(True)
            app.wav_var.set(True)
            app.tracer_var.set(False)
            app._tracer_toggled()
            self.assertFalse(app.wav_var.get())
            app.wav_var.set(True)
            with mock.patch.object(bl.messagebox, "askyesno", return_value=True):
                app._wav_toggled()
            self.assertTrue(app.tracer_var.get())
            app.tracer_var.set(False)
            app.wav_var.set(True)
            with mock.patch.object(bl.messagebox, "askyesno", return_value=False):
                app._wav_toggled()
            self.assertFalse(app.wav_var.get())


if __name__ == "__main__":
    unittest.main()
