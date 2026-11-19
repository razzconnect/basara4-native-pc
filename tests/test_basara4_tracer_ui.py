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
            with mock.patch.object(bl.Launcher, "send_report") as send:
                report.invoke()
            send.assert_called_once_with()

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
