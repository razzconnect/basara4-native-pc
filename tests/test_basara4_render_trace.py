"""Render debug trace controls and env propagation in launcher/basara4_launcher.py."""
import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))
import basara4_launcher as bl  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class Basara4RenderTraceLauncherTest(unittest.TestCase):
    def test_defaults_include_render_trace_false(self):
        self.assertIn("render_trace", bl.DEFAULTS)
        self.assertIs(bl.DEFAULTS["render_trace"], False)

    @unittest.skipUnless(tk, "requires Tk")
    def test_render_trace_checkbox_and_env_propagation(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            folder = Path(directory)
            user_dir = folder / "user"
            out_dir = folder / "out"
            config = user_dir / "launcher.json"
            ini = user_dir / "basara4.ini"
            trace_log = user_dir / "render_trace.log"
            captures = user_dir / "captures"
            user_dir.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"game": "", "render_trace": False}), encoding="utf-8")
            for name, value in (
                ("USER", user_dir),
                ("OUT", out_dir),
                ("CONFIG", config),
                ("INI", ini),
                ("RENDER_TRACE_LOG", trace_log),
                ("CAPTURES_DIR", captures),
                ("AUDIO_LOG", user_dir / "audio_debug.log"),
                ("AUDIO_WAV", user_dir / "audio_wav"),
            ):
                stack.enter_context(mock.patch.object(bl, name, value))
            stack.enter_context(mock.patch.object(bl.Launcher, "_check_update_thread", lambda self: None))

            app = bl.Launcher()
            try:
                app.update_idletasks()
                widgets = list(descendants(app))
                check_texts = {
                    w.cget("text") for w in widgets if isinstance(w, ttk.Checkbutton)
                }
                button_texts = {
                    w.cget("text") for w in widgets if isinstance(w, ttk.Button)
                }
                self.assertIn("Render debug trace", check_texts)
                self.assertIn("Open render log", button_texts)
                self.assertIn("Open captures", button_texts)

                # Off by default -> env vars must not be set.
                with mock.patch.dict("os.environ", {"SPARTAN_RENDER_TRACE": "stale", "SPARTAN_FRAME_CAPTURE_KEY": "1"}):
                    app._collect()
                    env_off = app._env()
                    self.assertNotIn("SPARTAN_RENDER_TRACE", env_off)
                    self.assertNotIn("SPARTAN_FRAME_CAPTURE_KEY", env_off)

                # Enable checkbox -> persisted to config and propagated to env.
                app.render_trace_var.set(True)
                app._collect()
                saved = json.loads(config.read_text(encoding="utf-8"))
                self.assertTrue(saved["render_trace"])
                env_on = app._env()
                self.assertEqual(env_on["SPARTAN_RENDER_TRACE"], str(trace_log))
                self.assertEqual(env_on["SPARTAN_FRAME_CAPTURE_KEY"], "1")
            finally:
                app.destroy()


class Basara4RenderTraceErrorsTest(unittest.TestCase):
    """Render-trace file errors are reported, not swallowed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.user = Path(self._tmp.name)
        self.log = self.user / "render_trace.log"
        self.captures = self.user / "captures"
        for name, value in (("RENDER_TRACE_LOG", self.log), ("CAPTURES_DIR", self.captures)):
            patcher = mock.patch.object(bl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_keep_previous_log_renames(self):
        self.log.write_text("old run\n", encoding="utf-8")
        self.assertEqual(bl.keep_previous_render_trace(), "")
        self.assertFalse(self.log.exists())
        self.assertEqual((self.user / "render_trace.previous.log").read_text(encoding="utf-8"), "old run\n")

    def test_keep_previous_log_without_log(self):
        self.assertEqual(bl.keep_previous_render_trace(), "")

    def test_keep_previous_log_failure_is_reported(self):
        self.log.write_text("old run\n", encoding="utf-8")
        with mock.patch.object(bl.os, "replace", side_effect=PermissionError(13, "Access is denied")):
            notice = bl.keep_previous_render_trace()
        self.assertIn("Previous render_trace.log not kept", notice)
        self.assertIn("Access is denied", notice)
        self.assertIn("overwrites", notice)

    def test_report_lists_tail_and_captures(self):
        self.log.write_text("\n".join(f"line {i}" for i in range(20)) + "\n", encoding="utf-8")
        self.captures.mkdir()
        (self.captures / "frame_1.txt").write_text("x", encoding="utf-8")
        report = bl.render_trace_report()
        self.assertIn("--- [Recent Render Trace (render_trace.log)] ---\nline 8\n", report)
        self.assertIn("line 19\n", report)
        self.assertNotIn("line 7\n", report)
        self.assertIn("--- [Recent Captures (user/captures)] ---\n  - frame_1.txt\n", report)

    def test_report_without_files_is_empty(self):
        self.assertEqual(bl.render_trace_report(), "")

    def test_report_states_unreadable_log(self):
        self.log.write_text("line\n", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "Permission denied")):
            report = bl.render_trace_report()
        self.assertIn("render_trace.log could not be read: [Errno 13] Permission denied", report)

    def test_report_states_unlistable_captures(self):
        self.captures.mkdir()
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError(13, "Permission denied")):
            report = bl.render_trace_report()
        self.assertIn("user\\captures could not be listed: [Errno 13] Permission denied", report)


if __name__ == "__main__":
    unittest.main()
