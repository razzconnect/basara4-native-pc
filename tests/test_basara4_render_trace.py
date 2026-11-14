"""Render debug trace controls and env propagation in launcher/basara4_launcher.py."""
import contextlib
import json
import sys
import tempfile
import types
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

                # A started traced run writes into its own session folder.
                app.trace_session = user_dir / "render_traces" / "20261114-210509"
                self.assertEqual(app._env()["SPARTAN_RENDER_TRACE"],
                                 str(app.trace_session / "render_trace.log"))
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
        self.traces = self.user / "render_traces"
        for name, value in (("RENDER_TRACE_LOG", self.log), ("CAPTURES_DIR", self.captures),
                            ("RENDER_TRACES", self.traces)):
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


class Basara4RenderTraceSessionsTest(unittest.TestCase):
    """Every traced run keeps its own folder: no run overwrites the log of another."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.user = Path(self._tmp.name)
        self.traces = self.user / "render_traces"
        self.last_run = self.user / "last_run.log"
        for name, value in (("RENDER_TRACES", self.traces), ("RENDER_TRACE_LOG", self.user / "render_trace.log"),
                            ("CAPTURES_DIR", self.user / "captures"), ("LOG", self.last_run)):
            patcher = mock.patch.object(bl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.when = bl.datetime.datetime(2026, 11, 14, 21, 5, 9)

    def test_each_run_gets_its_own_folder(self):
        first = bl.new_trace_session(self.when)
        second = bl.new_trace_session(self.when)  # same second
        third = bl.new_trace_session(self.when)
        self.assertEqual([first.name, second.name, third.name],
                         ["20261114-210509", "20261114-210509-2", "20261114-210509-3"])
        self.assertTrue(all(p.is_dir() for p in (first, second, third)))

    def test_folder_creation_failure_is_raised(self):
        self.traces.write_text("a file where the folder should be", encoding="utf-8")
        with self.assertRaises(OSError):
            bl.new_trace_session(self.when)

    def test_latest_session(self):
        self.assertIsNone(bl.latest_trace_session())
        for name in ("20261114-210509", "20261114-210509-2", "20261114-210509-10", "20261113-235959"):
            (self.traces / name).mkdir(parents=True)
        (self.traces / "notes").mkdir()
        (self.traces / "20991231-000000").write_text("a file, not a session", encoding="utf-8")
        self.assertEqual(bl.latest_trace_session().name, "20261114-210509-10")

    def test_copy_into_session(self):
        session = bl.new_trace_session(self.when)
        settings = self.user / "launcher.json"
        settings.write_text("{}", encoding="utf-8")
        self.assertEqual(bl.copy_into_trace_session(session, [settings, self.user / "missing.ini"]), "")
        self.assertEqual((session / "launcher.json").read_text(encoding="utf-8"), "{}")
        self.assertFalse((session / "missing.ini").exists())

    def test_copy_failure_is_reported(self):
        session = bl.new_trace_session(self.when)
        self.last_run.write_text("log", encoding="utf-8")
        with mock.patch.object(bl.shutil, "copy2", side_effect=PermissionError(13, "Access is denied")):
            notice = bl.copy_into_trace_session(session, [self.last_run])
        self.assertIn("Not saved in render_traces\\20261114-210509", notice)
        self.assertIn("last_run.log (Access is denied)", notice)

    def test_finished_game_keeps_its_last_run_log(self):
        session = bl.new_trace_session(self.when)
        self.last_run.write_text("run 1\n", encoding="utf-8")
        launcher = types.SimpleNamespace(trace_session=session)
        self.assertEqual(bl.Launcher._finish_trace_session(launcher), "")
        self.assertIsNone(launcher.trace_session)
        self.last_run.write_text("run 2\n", encoding="utf-8")  # the next run rewrites user\last_run.log
        self.assertEqual((session / "last_run.log").read_text(encoding="utf-8"), "run 1\n")
        self.assertEqual(bl.Launcher._finish_trace_session(launcher), "")  # nothing left to finish

    def test_report_uses_newest_session(self):
        older = bl.new_trace_session(self.when)
        (older / "render_trace.log").write_text("old session\n", encoding="utf-8")
        newer = bl.new_trace_session(self.when)
        (newer / "render_trace.log").write_text("new session\n", encoding="utf-8")
        report = bl.render_trace_report()
        self.assertIn("--- [Recent Render Trace (render_traces\\20261114-210509-2\\render_trace.log)] ---\n"
                      "new session\n", report)
        self.assertNotIn("old session", report)

    def test_report_states_unlistable_sessions(self):
        self.traces.mkdir()
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError(13, "Permission denied")):
            report = bl.render_trace_report()
        self.assertIn("user\\render_traces could not be listed: [Errno 13] Permission denied", report)


if __name__ == "__main__":
    unittest.main()
