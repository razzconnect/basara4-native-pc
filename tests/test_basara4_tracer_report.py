"""Tracer reports: one zip per session holding exactly that session's files, within a size budget, with the
account and computer name checked in every text. Covers the five ways the zip used to take the wrong file
("newest" instead of "this session's"), a session the launcher lost track of, the budget order, and the
privacy checks. No window opens: the "Report saved" window and Explorer are replaced.
"""
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl
from test_basara4_launcher_window import launcher

rp = bl.basara4_report


def age(path, seconds=3600):
    """Make a file look like it was written `seconds` ago (before the session)."""
    old = time.time() - seconds
    os.utime(path, (old, old))


class SessionMixin:
    def begin(self, app, game, tracer=True):
        """A game session with the Tracer on (a trace folder, audio log) or off."""
        app.cfg.update(render_trace=tracer, audio_log=tracer)
        app.trace_session = bl.new_trace_session() if tracer else None
        app._start_session_report(game)
        return app.trace_session

    def end(self, app, code=0xC0000005):
        app._finish_session_report(code)
        app._finish_trace_session()  # clears app.trace_session, as when the game ends

    def zip_of(self, app):
        with mock.patch.object(bl.Launcher, "_show_report_saved"):
            target = app.send_report()
        self.assertIsNotNone(target)
        return zipfile.ZipFile(target)

    @staticmethod
    def text(zf, name):
        return zf.read(name).decode("utf-8").replace("\r\n", "\n")  # write_text writes CRLF on Windows


class SessionTests(SessionMixin, unittest.TestCase):
    def test_every_capture_of_the_session_comes_with_its_text(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.begin(app, game)
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            for n in (1, 2):
                (bl.CAPTURES_DIR / f"frame_20261119-100000_{n}.png").write_bytes(b"\x89PNG" + bytes([n]))
                (bl.CAPTURES_DIR / f"frame_20261119-100000_{n}.txt").write_text(f"state of frame {n}\n", encoding="utf-8")
            self.end(app)
            with self.zip_of(app) as zf:
                names = set(zf.namelist())
                self.assertEqual({n for n in names if n.startswith("captures/")},
                                 {f"captures/frame_20261119-100000_{n}.{ext}" for n in (1, 2) for ext in ("png", "txt")})
                self.assertEqual(self.text(zf, "captures/frame_20261119-100000_1.txt"), "state of frame 1\n")

    def test_an_audio_log_of_an_earlier_session_stays_out(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            bl.AUDIO_LOG.write_text("[10 ms] OLD audio event\n", encoding="utf-8")
            age(bl.AUDIO_LOG)
            self.begin(app, game, tracer=False)
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertNotIn("audio_debug.log", zf.namelist())
            # a session that wrote one has it
            self.begin(app, game, tracer=True)
            bl.AUDIO_LOG.write_text("[12 ms] NEW audio event\n", encoding="utf-8")
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertIn("NEW audio event", self.text(zf, "audio_debug.log"))

    def test_a_trace_of_an_earlier_session_does_not_stand_in(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            old = bl.RENDER_TRACES / "20250101-000000"
            old.mkdir(parents=True)
            (old / "render_trace.log").write_text("OLD-TRACE-LINE\n", encoding="utf-8")
            self.begin(app, game, tracer=False)
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertNotIn("render_trace.log", zf.namelist())
                report = self.text(zf, "report.txt")
            self.assertNotIn("OLD-TRACE-LINE", report)
            self.assertIn("(none: Tracer was off in this session", report)

    def test_the_trace_is_found_after_the_game_ended(self):
        """The launcher forgets the trace folder when the game ends; the report remembers it."""
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            session = self.begin(app, game)
            (session / "render_trace.log").write_text("[ 5 ms] anomaly: x\n", encoding="utf-8")
            self.end(app)
            self.assertIsNone(app.trace_session)
            with self.zip_of(app) as zf:
                self.assertIn("anomaly: x", self.text(zf, "render_trace.log"))

    def test_the_capture_list_names_this_sessions_captures_only(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            for name in ("frame_old_1.png", "frame_old_1.txt"):
                (bl.CAPTURES_DIR / name).write_bytes(b"old")
                age(bl.CAPTURES_DIR / name)
            self.begin(app, game)
            (bl.CAPTURES_DIR / "frame_new_1.png").write_bytes(b"new")
            self.end(app)
            with self.zip_of(app) as zf:
                report = self.text(zf, "report.txt")
                self.assertIn("frame_new_1.png", report)
                self.assertNotIn("frame_old", report)
                self.assertNotIn("captures/frame_old_1.png", zf.namelist())

    def test_the_whole_trace_is_in_the_zip(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            session = self.begin(app, game)
            lines = [f"[{n} ms] trace line {n}" for n in range(40)]
            (session / "render_trace.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertEqual(self.text(zf, "render_trace.log").splitlines(), lines)
                self.assertIn("last 12 of 40 lines", self.text(zf, "report.txt"))

    def test_a_session_the_launcher_lost_track_of_can_be_reported(self):
        """The launcher closed (or the PC froze) before the game ended: after the next start the report
        still takes that session's files, and says it never ended."""
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            session = self.begin(app, game)
            (session / "render_trace.log").write_text("[ 9 ms] before the freeze\n", encoding="utf-8")
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (bl.CAPTURES_DIR / "frame_a_1.txt").write_text("state\n", encoding="utf-8")
            rp.mark_unfinished(bl.REPORTS)
            app.session_report = None  # a new launcher run knows nothing of it
            app.trace_session = None
            with self.zip_of(app) as zf:
                report = json.loads(self.text(zf, "report.json"))
                self.assertEqual(report["status"], "unfinished")
                self.assertIn("before the freeze", self.text(zf, "render_trace.log"))
                self.assertIn("captures/frame_a_1.txt", zf.namelist())

    def test_a_file_just_before_the_start_belongs_to_the_session_and_one_long_before_does_not(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (bl.CAPTURES_DIR / "frame_before_1.txt").write_text("a\n", encoding="utf-8")
            age(bl.CAPTURES_DIR / "frame_before_1.txt", 1)  # written 1 s before the start
            (bl.CAPTURES_DIR / "frame_long_ago_1.txt").write_text("b\n", encoding="utf-8")
            age(bl.CAPTURES_DIR / "frame_long_ago_1.txt", 600)
            self.begin(app, game)
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertIn("captures/frame_before_1.txt", zf.namelist())
                self.assertNotIn("captures/frame_long_ago_1.txt", zf.namelist())

    def test_no_wav_and_no_dump_ever_goes_into_the_zip(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.begin(app, game)
            bl.AUDIO_WAV.mkdir(parents=True, exist_ok=True)
            (bl.AUDIO_WAV / "master.wav").write_bytes(b"RIFF" + b"\0" * 64)
            (paths.user / "crash").mkdir(exist_ok=True)
            (paths.user / "crash" / "core-1.dmp").write_bytes(b"MDMP" + b"\0" * 64)
            self.end(app)
            with self.zip_of(app) as zf:
                self.assertFalse([n for n in zf.namelist() if n.lower().endswith((".wav", ".dmp"))])
                self.assertIn("core-1.dmp", self.text(zf, "report.txt"))  # named, kept on the PC


class SessionEdgeTests(SessionMixin, unittest.TestCase):
    def test_files_written_after_the_session_ended_stay_out(self):
        """A later game start writes its own files; the zip of this session must not take them."""
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.begin(app, game)
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (bl.CAPTURES_DIR / "frame_during_1.txt").write_text("during\n", encoding="utf-8")
            self.end(app)
            later = time.time() + 30  # the next session, well after this one ended
            for name in ("frame_next_1.txt", "frame_next_1.png"):
                (bl.CAPTURES_DIR / name).write_bytes(b"next")
                os.utime(bl.CAPTURES_DIR / name, (later, later))
            bl.AUDIO_LOG.write_text("[1 ms] next session\n", encoding="utf-8")
            os.utime(bl.AUDIO_LOG, (later, later))
            with self.zip_of(app) as zf:
                names = set(zf.namelist())
                self.assertIn("captures/frame_during_1.txt", names)
                self.assertFalse({"captures/frame_next_1.txt", "captures/frame_next_1.png", "audio_debug.log"} & names)

    def test_the_end_is_known_even_when_finishing_the_report_failed(self):
        """If completing the report fails there is no "ended" time in it: the launcher's own end time holds."""
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(rp, "finish_report", side_effect=ValueError("boom")):
            self.begin(app, game)
            self.end(app)
            self.assertNotIn("ended", app.session_report)
            real = time.time
            later = real() + 30  # written well after the game ended...
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (bl.CAPTURES_DIR / "frame_next_1.txt").write_text("next\n", encoding="utf-8")
            os.utime(bl.CAPTURES_DIR / "frame_next_1.txt", (later, later))
            # ...and the report is made even later: "until now" would take the file, the end time does not
            with mock.patch.object(rp.time, "time", lambda: real() + 100), self.zip_of(app) as zf:
                self.assertNotIn("captures/frame_next_1.txt", zf.namelist())

    def test_a_report_that_cannot_be_made_private_is_refused_in_plain_words(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.messagebox, "showerror") as error, \
                mock.patch.object(rp, "make_zip", side_effect=rp.PrivacyError("a.log still shows your account name "
                                                                               "after it was hidden")), \
                mock.patch.object(bl.Launcher, "_show_report_saved") as dialog:
            self.begin(app, game)
            self.assertIsNone(app.send_report())
            dialog.assert_not_called()  # no "Report saved" window for a zip that does not exist
            title, message = error.call_args.args[:2]
            self.assertEqual(title, "Report not saved")
            self.assertIn("a.log still shows your account name after it was hidden", message)
            self.assertIn("Nothing was written", message)
            self.assertEqual(list(bl.REPORTS.glob("report-*.zip*")), [])


class BudgetTests(SessionMixin, unittest.TestCase):
    def test_the_zip_never_passes_the_budget_and_lists_what_did_not_fit(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            session = self.begin(app, game)
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (session / "render_trace.log").write_text(
                "[1 ms] capture.saved reason=hotkey_f11 trace=C:\\x\\frame_f11_1.txt screenshot=C:\\x\\frame_f11_1.png\n"
                "[2 ms] capture.saved reason=auto_startup trace=C:\\x\\frame_auto_1.txt screenshot=C:\\x\\frame_auto_1.png\n",
                encoding="utf-8")
            (bl.CAPTURES_DIR / "frame_f11_1.txt").write_text("f11 state\n", encoding="utf-8")
            (bl.CAPTURES_DIR / "frame_f11_1.png").write_bytes(os.urandom(5_000_000))
            (bl.CAPTURES_DIR / "frame_auto_1.txt").write_text("auto state\n", encoding="utf-8")
            (bl.CAPTURES_DIR / "frame_auto_1.png").write_bytes(os.urandom(5_000_000))
            older = time.time() - 1  # the F11 screenshot is older than the auto one, and still in the session
            os.utime(bl.CAPTURES_DIR / "frame_f11_1.png", (older, older))
            self.end(app)
            with self.zip_of(app) as zf:
                names = set(zf.namelist())
                total = sum(i.compress_size for i in zf.infolist())
                self.assertLessEqual(Path(zf.filename).stat().st_size, rp.ZIP_BUDGET_BYTES)
                self.assertLess(total, rp.ZIP_BUDGET_BYTES)
                # every text is in; of the two screenshots the F11 one, though older, is kept
                for name in ("render_trace.log", "captures/frame_f11_1.txt", "captures/frame_auto_1.txt"):
                    self.assertIn(name, names)
                self.assertIn("captures/frame_f11_1.png", names)
                self.assertNotIn("captures/frame_auto_1.png", names)
                report = self.text(zf, "report.txt")
                self.assertRegex(report, r"captures/frame_auto_1\.png: \d+ bytes in the zip, over the 9 MB zip limit")

    def test_text_comes_before_screenshots(self):
        """With a screenshot that alone fills the budget, the texts still go in first."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "r.zip"
            logs = [(f"t{n}.log", Path(tmp) / f"t{n}.log") for n in range(3)]
            for _name, path in logs:
                path.write_text("text\n" * 100, encoding="utf-8")
            big = Path(tmp) / "big.png"
            big.write_bytes(os.urandom(2_000_000))
            skipped = rp.make_zip(target, {"schema": 1}, "report\n", rp.Masker(), logs=logs,
                                  binaries=[("big.png", big)], max_total_bytes=1_000_000)
            with zipfile.ZipFile(target) as zf:
                self.assertEqual(sorted(zf.namelist()), ["report.json", "report.txt", "t0.log", "t1.log", "t2.log"])
            self.assertEqual(len(skipped), 1)
            self.assertIn("big.png", skipped[0])
            self.assertLessEqual(target.stat().st_size, 1_000_000)


class PrivacyNetTests(unittest.TestCase):
    def test_a_one_or_two_letter_account_is_masked_as_a_folder_name(self):
        masker = rp.Masker(None, None, None, "Jo", "PC")
        self.assertEqual(masker(r"D:\Backup\Jo\x and C:\data\pc\y"), r"D:\Backup\<user>\x and C:\data\<pc>\y")
        self.assertEqual(masker("Jo and pc speak"), "Jo and pc speak")  # ordinary words stay

    def test_a_name_with_accents_is_masked_in_either_spelling(self):
        masker = rp.Masker(r"C:\Users\Jürgen", None, None, "Jürgen", "JÜRGEN-PC")
        composed = "open C:\\Users\\J\u00fcrgen\\x by J\u00fcrgen"
        decomposed = "open C:\\Users\\Ju\u0308rgen\\x by Ju\u0308rgen"
        for text in (composed, decomposed):
            masked = masker(text)
            self.assertNotIn("rgen", masked.replace("<user>", ""), text)
            self.assertEqual(masker.leaks(masked), [])

    def test_a_log_written_in_the_windows_code_page_is_read_and_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "last_run.log"
            log.write_bytes("open C:\\Users\\Jürgen\\x\n".encode("cp1252"))  # not valid UTF-8
            masker = rp.Masker(r"C:\Users\Jürgen", None, None, "Jürgen", None)
            with mock.patch.object(rp.locale, "getpreferredencoding", return_value="cp1252"):
                target = Path(tmp) / "r.zip"
                rp.make_zip(target, {"schema": 1}, "r\n", masker, logs=[("last_run.log", log)])
            with zipfile.ZipFile(target) as zf:
                self.assertNotIn("rgen", zf.read("last_run.log").decode("utf-8"))
                self.assertIn("<user>", zf.read("last_run.log").decode("utf-8"))

    def test_a_name_the_masking_missed_stops_the_zip(self):
        """The check looks without accents or case, so a spelling the masking does not match is found, and
        nothing is written."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "a.log"
            log.write_text("seen by Jurgen on the desk\n", encoding="utf-8")  # no umlaut
            masker = rp.Masker(None, None, None, "Jürgen", None)
            target = Path(tmp) / "r.zip"
            with self.assertRaises(rp.PrivacyError) as raised:
                rp.make_zip(target, {"schema": 1}, "r\n", masker, logs=[("a.log", log)])
            self.assertIn("a.log still shows your account name", str(raised.exception))
            self.assertFalse(target.exists())
            self.assertFalse(Path(str(target) + ".tmp").exists())
            self.assertEqual(masker.leaks("<user> and <pc>"), [])  # the tokens themselves are not a leak

    def test_the_report_itself_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "r.zip"
            with self.assertRaises(rp.PrivacyError):
                rp.make_zip(target, {"schema": 1, "note": "from Jurgen"}, "r\n", rp.Masker(None, None, None, "Jürgen", None))
            self.assertFalse(target.exists())

    def test_leftover_paths_are_cut_to_the_file_name_but_system_paths_stay(self):
        text = (r"open E:\Backup\Stuff\data.bin and C:\Windows\System32\vulkan-1.dll "
                r"and ""E:\\\\Backup\\\\x\\\\y.txt"" and /app0/a/b and https://x.y/z")
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            scrubbed = rp.scrub_paths(text)
        self.assertIn(r"<path>\data.bin", scrubbed)
        self.assertIn(r"C:\Windows\System32\vulkan-1.dll", scrubbed)
        self.assertIn(r"<path>\y.txt", scrubbed)
        self.assertNotIn("Backup", scrubbed)
        self.assertIn("/app0/a/b", scrubbed)
        self.assertIn("https://x.y/z", scrubbed)

    def test_every_text_of_a_tracer_zip_is_free_of_the_account_and_computer_name(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.dict(os.environ, {"USERNAME": "QAUSER9z", "COMPUTERNAME": "QAPC9z",
                                             "USERPROFILE": "C:\\Users\\QAUSER9z"}):
            app.cfg["dlc_dir"] = "D:\\QADLC9z ünï\\DLC"
            helper = SessionMixin()
            session = helper.begin(app, game)
            leaky = ("open C:\\Users\\QAUSER9z\\AppData\\x\n" "host QAPC9z up\n" "by qauser9z\n"
                     "capture D:\\Backup\\QAUSER9z\\frame.png\n")
            (session / "render_trace.log").write_text(leaky, encoding="utf-8")
            bl.AUDIO_LOG.write_text(leaky, encoding="utf-8")
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            (bl.CAPTURES_DIR / "frame_1.txt").write_text(leaky, encoding="utf-8")
            bl.LOG.write_text(leaky, encoding="utf-8")
            helper.end(app)
            with mock.patch.object(bl.Launcher, "_show_report_saved"):
                target = app.send_report()
            with zipfile.ZipFile(target) as zf:
                texts = [n for n in zf.namelist() if not n.endswith(".png")]
                self.assertTrue({"render_trace.log", "audio_debug.log", "captures/frame_1.txt", "last_run.log",
                                 "report.txt", "report.json"} <= set(texts))
                for name in texts:
                    text = zf.read(name).decode("utf-8").lower()
                    for private in ("qauser9z", "qapc9z", "qadlc9z"):
                        self.assertNotIn(private, text, (name, private))


class EndLabelTests(unittest.TestCase):
    def test_a_game_that_left_no_fault_is_not_called_a_crash(self):
        report = rp.finish_report(rp.new_report("2026-11-19T10:00:00", {}, {}, {}, {}, {}, {}), 1, False, False,
                                  ["Runtime: ok", "Audio: playing"], [], "NVIDIA")
        line = rp.summary_line(report)
        self.assertIn("no error message in the log", line)
        self.assertIn("closed from outside", line)
        self.assertNotIn("Crash:", line)


if __name__ == "__main__":
    unittest.main()
