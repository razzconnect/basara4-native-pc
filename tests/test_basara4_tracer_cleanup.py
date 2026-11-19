"""Automatic cleanup of the Tracer files that pile up on disk (user\\captures and user\\render_traces).

The rule under test: when the launcher starts, a capture pair or a trace folder that belongs to no kept session is
removed, and nothing else is. A session is kept as long as its report is kept (the newest KEEP_SESSIONS, the same
rule prune() uses for user\\reports), so a kept report never loses the pictures or the trace it describes. Every
file here is synthetic and lives in a temporary folder; no game file, no real user folder, no window is touched."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl
from test_basara4_launcher_window import launcher

rp = bl.basara4_report

BASE = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))  # a fixed local time; sessions are an hour apart
LENGTH = 600  # every session lasts ten minutes


def stamp(epoch):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(epoch))


def touch(path, epoch, data=b"x" * 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (epoch, epoch))
    return path


class World:
    """A user\\ folder with sessions, their reports, pictures and trace folders."""

    def __init__(self, root):
        self.root = Path(root)
        self.reports, self.captures, self.traces = (self.root / n for n in ("reports", "captures", "render_traces"))
        for folder in (self.reports, self.captures, self.traces):
            folder.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def start(self, i):
        return BASE + i * 3600

    def session(self, i, status="finished", pictures=1, trace=True, name=None):
        """Session number i: its report, a trace folder and `pictures` capture pairs inside its window."""
        start = self.start(i)
        folder = stamp(start - 1) if trace else None
        tracer = {"on": trace, "trace_folder": folder, "started_epoch": start}
        if status != "started":
            tracer["ended_epoch"] = start + LENGTH
        report = {"status": status, "tracer": tracer}
        path = self.reports / f"session-{stamp(start)}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        (self.reports / f"session-{stamp(start)}.txt").write_text("text", encoding="utf-8")
        (self.reports / f"report-{stamp(start + LENGTH)}.zip").write_bytes(b"PK zip")
        made = []
        if folder:
            for known in ("render_trace.log", "launcher.json", "basara4.ini", "last_run.log"):
                made.append(touch(self.traces / folder / known, start + 5))
        for k in range(pictures):
            made += self.pair(start + 10 + k, k + 1)
        return {"report": path, "folder": self.traces / folder if folder else None, "files": made}

    def pair(self, epoch, seq, png_epoch=None, txt_epoch=None):
        stem = f"frame_{stamp(epoch)}_{seq}"
        return [touch(self.captures / f"{stem}.png", png_epoch or epoch, b"p" * 1000),
                touch(self.captures / f"{stem}.txt", txt_epoch or epoch, b"t" * 50)]

    def clean(self, **kw):
        return rp.clean_tracer_files(self.reports, self.captures, self.traces, **kw)

    def listing(self, folder):
        return sorted(p.name for p in Path(folder).rglob("*"))


def alive(paths):
    return [p.exists() for p in paths]


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.w = World(self._tmp.name)

    # -------------------------------------------------- (a) old sessions go, the newest N stay
    def test_old_sessions_removed_and_the_newest_kept(self):
        sessions = [self.w.session(i, pictures=2) for i in range(25)]
        reports_before = self.w.listing(self.w.reports)
        result = self.w.clean()
        self.assertEqual(rp.KEEP_SESSIONS, 20)
        for old in sessions[:5]:
            self.assertFalse(any(alive(old["files"])), "files of a session beyond the newest 20 must go")
            self.assertFalse(old["folder"].exists(), "its (now empty) trace folder must go too")
        for kept in sessions[5:]:
            self.assertTrue(all(alive(kept["files"])), "a kept session keeps every picture and trace file")
            self.assertTrue(kept["folder"].is_dir())
        self.assertEqual(self.w.listing(self.w.reports), reports_before, "reports and zips are not this function's job")
        self.assertEqual(result.folders, 5)
        self.assertEqual(result.files, 5 * (2 * 2 + 4))
        self.assertEqual(result.bytes, 5 * (2 * 1050 + 4 * 100))
        self.assertEqual(result.failed, [])

    def test_keep_is_the_same_number_prune_uses(self):
        import inspect
        self.assertEqual(inspect.signature(rp.prune).parameters["keep"].default, rp.KEEP_SESSIONS)
        sessions = [self.w.session(i) for i in range(23)]
        self.w.clean()
        rp.prune(self.w.reports)
        left = sorted(p.name for p in self.w.reports.glob("session-*.json"))
        self.assertEqual(len(left), rp.KEEP_SESSIONS)
        for item in sessions:  # every report that is still there still has its pictures and trace
            if item["report"].exists():
                self.assertTrue(all(alive(item["files"])))
            else:
                self.assertFalse(any(alive(item["files"])))

    def test_fewer_sessions_than_the_limit_remove_only_what_is_older_than_all_of_them(self):
        kept = [self.w.session(i) for i in range(3)]
        old = self.w.pair(self.w.start(0) - 86400, 9)  # a day before the oldest kept session
        old_folder = self.w.traces / stamp(self.w.start(0) - 86400)
        touch(old_folder / "render_trace.log", self.w.start(0) - 86400)
        self.w.clean()
        self.assertFalse(any(alive(old)))
        self.assertFalse(old_folder.exists())
        for item in kept:
            self.assertTrue(all(alive(item["files"])))

    # -------------------------------------------------- (b) a picture pair is never split
    def test_pairs_are_kept_or_removed_together(self):
        sessions = [self.w.session(i, pictures=0) for i in range(22)]
        cutoff = self.w.start(2) - rp.SESSION_SLACK_SECONDS  # the oldest kept session
        both_old = self.w.pair(cutoff - 5000, 1)
        png_old_txt_new = self.w.pair(cutoff - 50, 2, png_epoch=cutoff - 50, txt_epoch=cutoff + 50)
        txt_old_png_new = self.w.pair(cutoff - 50, 3, png_epoch=cutoff + 50, txt_epoch=cutoff - 50)
        lone_txt = [touch(self.w.captures / f"frame_{stamp(cutoff - 9000)}_4.txt", cutoff - 9000)]
        lone_png_new = [touch(self.w.captures / f"frame_{stamp(cutoff + 9000)}_5.png", cutoff + 9000)]
        self.w.clean()
        self.assertEqual(alive(both_old), [False, False])
        self.assertEqual(alive(png_old_txt_new), [True, True], "one member is still wanted: the pair stays whole")
        self.assertEqual(alive(txt_old_png_new), [True, True])
        self.assertEqual(alive(lone_txt), [False])
        self.assertEqual(alive(lone_png_new), [True])
        self.assertTrue(sessions)

    def test_a_failed_delete_of_the_picture_leaves_its_text(self):
        for i in range(22):
            self.w.session(i, pictures=0)
        pair = self.w.pair(self.w.start(0) - 5000, 1)
        real = Path.unlink

        def unlink(path, *a, **k):
            if path.suffix == ".png":
                raise PermissionError(13, "in use")
            return real(path, *a, **k)
        with mock.patch.object(Path, "unlink", unlink):
            result = self.w.clean()
        self.assertEqual(alive(pair), [True, True], "the text of a picture that could not go must not be removed alone")
        self.assertEqual(len(result.failed), 1)

    # -------------------------------------------------- (c) running and unfinished sessions are not touched
    def test_running_and_unfinished_sessions_keep_their_files(self):
        sessions = [self.w.session(i) for i in range(22)]
        sessions[21] = self.w.session(21, status="started")
        sessions[10] = self.w.session(10, status="unfinished")
        late = touch(self.w.captures / f"frame_{stamp(self.w.start(10) + 7200)}_1.png", self.w.start(10) + 7200)
        now = self.w.start(21) + 30
        self.w.clean(now=now)
        for i in (10, 21):
            self.assertTrue(all(alive(sessions[i]["files"])))
            self.assertTrue(sessions[i]["folder"].is_dir())
        self.assertTrue(late.exists(), "a picture written after an unfinished session was marked is not older than the kept ones")
        for i in (0, 1):
            self.assertFalse(any(alive(sessions[i]["files"])))

    def test_a_trace_folder_a_kept_report_points_to_stays_even_with_an_old_name(self):
        for i in range(22):
            self.w.session(i)
        report_path = self.w.reports / f"session-{stamp(self.w.start(15))}.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["tracer"]["trace_folder"] = "20200101-000000-3"  # created long before, named by a renamed clock
        report_path.write_text(json.dumps(report), encoding="utf-8")
        pointed = touch(self.w.traces / "20200101-000000-3" / "render_trace.log", BASE - 99999)
        loose = touch(self.w.traces / "20200101-000000-4" / "render_trace.log", BASE - 99999)
        self.w.clean()
        self.assertTrue(pointed.exists())
        self.assertFalse(loose.exists())

    def test_files_between_sessions_are_not_older_than_the_kept_ones_and_stay(self):
        for i in range(22):
            self.w.session(i)
        gap = touch(self.w.captures / f"frame_{stamp(self.w.start(8) + 2000)}_1.png", self.w.start(8) + 2000)
        folder = touch(self.w.traces / stamp(self.w.start(8) + 2000) / "render_trace.log", self.w.start(8) + 2000)
        self.w.clean()
        self.assertTrue(gap.exists())
        self.assertTrue(folder.exists())

    # -------------------------------------------------- (d) a failure to delete never reaches the launcher
    def test_failures_are_recorded_and_the_rest_is_still_cleaned(self):
        sessions = [self.w.session(i) for i in range(23)]
        stuck = sessions[0]["files"][0]  # its render_trace.log
        real = Path.unlink

        def unlink(path, *a, **k):
            if path == stuck:
                raise PermissionError(13, "The process cannot access the file because it is being used")
            return real(path, *a, **k)
        with mock.patch.object(Path, "unlink", unlink):
            result = self.w.clean()
        self.assertTrue(stuck.exists())
        self.assertEqual(len(result.failed), 1)
        self.assertIn(stuck.name, result.failed[0])
        self.assertIn("being used", result.failed[0])
        self.assertFalse(any(alive(sessions[1]["files"])), "one stuck file must not stop the other sessions")
        self.assertTrue(sessions[0]["folder"].exists(), "a folder that still has a file stays")
        self.assertIn("could not be removed", result.summary())

    def test_a_folder_that_cannot_be_listed_is_reported_not_raised(self):
        for i in range(22):
            self.w.session(i)
        real = Path.iterdir

        def iterdir(path):
            if path == self.w.captures:
                raise PermissionError(5, "Access is denied")
            return real(path)
        with mock.patch.object(Path, "iterdir", iterdir):
            result = self.w.clean()
        self.assertTrue(any("captures" in item for item in result.failed))
        self.assertEqual(result.folders, 2, "the traces were still cleaned")

    # -------------------------------------------------- (e) nothing outside the known names and folders
    def test_only_names_the_tracer_writes_are_removed(self):
        for i in range(22):
            self.w.session(i)
        very_old = BASE - 365 * 86400
        names = ["notes.txt", "frame_x.png", "FRAME_20200101-000000_1.PNG", "frame_20200101-000000_1.png.bak",
                 "frame_20200101-000000_.png", "frame_20200101-000000_1.jpg", "my frame_20200101-000000_1.png",
                 "frame_20200101-000000_1.txt.zip", "フレーム_1.png"]
        outside = [touch(self.w.captures / n, very_old) for n in names]
        sub = touch(self.w.captures / "frame_20200101-000000_7.png" / "inner.txt", very_old)
        odd = [touch(self.w.traces / "odd folder" / "render_trace.log", very_old),
               touch(self.w.traces / "20200101-000000" / "my_notes.txt", very_old),
               touch(self.w.traces / "20200101-000000" / "render_trace.log", very_old),
               touch(self.w.traces / "readme.txt", very_old)]
        elsewhere = [touch(self.w.root / "screenshots" / "a.png", very_old),
                     touch(self.w.root / "audio_wav" / "port0.wav", very_old),
                     touch(self.w.root / "crash" / "report-20200101-000000.txt", very_old),
                     touch(self.w.root / "audio_debug.log", very_old),
                     touch(self.w.root / "last_run.log", very_old),
                     touch(self.w.reports / "report-20200101-000000.zip", very_old),
                     touch(self.w.reports / "notes.txt", very_old)]
        self.w.clean()
        for path in outside + [sub] + odd + elsewhere:
            self.assertTrue(path.exists(), f"{path} is not a Tracer file with a known name and must stay")

    def test_a_trace_folder_goes_only_when_everything_in_it_is_known(self):
        for i in range(22):
            self.w.session(i)
        very_old = BASE - 86400
        pure = [touch(self.w.traces / "20200101-000000" / n, very_old)
                for n in ("render_trace.log", "launcher.json", "basara4.ini", "last_run.log")]
        mixed = [touch(self.w.traces / "20200102-000000" / n, very_old) for n in ("render_trace.log", "mine.txt")]
        self.w.clean()
        self.assertFalse(any(alive(pure)))
        self.assertFalse(pure[0].parent.exists())
        self.assertTrue(all(alive(mixed)), "a folder with something the tracer did not write is left whole")

    def test_links_are_never_followed(self):
        for i in range(22):
            self.w.session(i)
        pair = self.w.pair(BASE - 99999, 1)
        folder = touch(self.w.traces / "20200101-000000" / "render_trace.log", BASE - 99999).parent
        with mock.patch.object(rp, "_is_link", lambda p: p.name in (pair[0].name, folder.name)):
            self.w.clean()
        self.assertEqual(alive(pair), [True, True])
        self.assertTrue(folder.exists())

    def test_a_captures_or_traces_folder_that_is_a_link_is_left_alone(self):
        for i in range(22):
            self.w.session(i)
        pair = self.w.pair(BASE - 99999, 1)
        folder = touch(self.w.traces / "20200101-000000" / "render_trace.log", BASE - 99999).parent
        with mock.patch.object(rp, "_is_link", lambda p: Path(p) in (self.w.captures, self.w.traces)):
            result = self.w.clean()
        self.assertEqual(alive(pair), [True, True])
        self.assertTrue(folder.exists())
        self.assertEqual((result.files, result.folders), (0, 0))

    # -------------------------------------------------- (f) empty, missing, unreadable
    def test_missing_and_empty_folders_are_safe(self):
        nowhere = Path(self._tmp.name) / "nowhere"
        for reports, captures, traces in ((nowhere, nowhere, nowhere), (self.w.reports, nowhere, nowhere),
                                          (self.w.reports, self.w.captures, self.w.traces)):
            result = rp.clean_tracer_files(reports, captures, traces)
            self.assertEqual((result.files, result.folders, result.bytes, result.failed), (0, 0, 0, []))
            self.assertEqual(result.summary(), "")
        self.assertIn("no session report", rp.clean_tracer_files(self.w.reports, self.w.captures, self.w.traces).skipped)

    def test_without_a_kept_session_nothing_is_removed(self):
        old = self.w.pair(BASE - 99999, 1)
        folder = touch(self.w.traces / "20200101-000000" / "render_trace.log", BASE - 99999)
        result = self.w.clean()
        self.assertEqual(alive(old) + [folder.exists()], [True, True, True])
        self.assertTrue(result.skipped)

    def test_a_report_that_cannot_be_read_stops_the_cleanup_before_it_deletes_anything(self):
        for i in range(22):
            self.w.session(i)
        old = self.w.pair(BASE - 99999, 1)
        (self.w.reports / f"session-{stamp(self.w.start(21))}.json").write_text("{not json", encoding="utf-8")
        result = self.w.clean()
        self.assertEqual(alive(old), [True, True])
        self.assertIn("cannot be read", result.skipped)
        self.assertEqual(result.files, 0)
        self.assertEqual(result.summary(), "Tracer cleanup skipped: " + result.skipped + ".")
        self.assertTrue(result.failed)

    def test_a_report_without_a_start_stops_the_cleanup(self):
        for i in range(22):
            self.w.session(i)
        old = self.w.pair(BASE - 99999, 1)
        (self.w.reports / f"session-{stamp(self.w.start(5))}.json").write_text('{"status": "finished"}', encoding="utf-8")
        result = self.w.clean()
        self.assertEqual(alive(old), [True, True])
        self.assertTrue(result.skipped)

    def test_a_second_run_finds_nothing_to_do(self):
        for i in range(24):
            self.w.session(i)
        self.assertGreater(self.w.clean().files, 0)
        again = self.w.clean()
        self.assertEqual((again.files, again.folders, again.failed), (0, 0, []))

    def test_old_reports_written_with_iso_times_still_have_a_window(self):
        for i in range(21):
            self.w.session(i)
        path = self.w.reports / f"session-{stamp(self.w.start(1))}.json"  # the oldest kept: before the Tracer existed
        start = self.w.start(1)
        path.write_text(json.dumps({"status": "finished",
                                    "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start)),
                                    "ended": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start + LENGTH))}),
                        encoding="utf-8")
        keep_me = touch(self.w.captures / f"frame_{stamp(start + 5)}_1.png", start + 5)
        drop_me = touch(self.w.captures / f"frame_{stamp(start - 7200)}_1.png", start - 7200)
        self.w.clean()
        self.assertTrue(keep_me.exists())
        self.assertFalse(drop_me.exists())

    # -------------------------------------------------- non-ASCII paths
    def test_non_ascii_folders_and_odd_names(self):
        root = Path(self._tmp.name) / "tempat uji äöü 日本"
        w = World(root)
        sessions = [w.session(i) for i in range(23)]
        odd = touch(w.captures / "catatan ä.txt", BASE - 99999)
        w.clean()
        self.assertFalse(any(alive(sessions[0]["files"])))
        self.assertTrue(all(alive(sessions[5]["files"])))
        self.assertTrue(odd.exists())

    # -------------------------------------------------- the message
    def test_summary_is_short_english(self):
        for i in range(22):
            self.w.session(i, pictures=1)
        result = self.w.clean()
        text = result.summary()
        self.assertTrue(text.startswith("Tracer cleanup: removed "), text)
        self.assertIn("2 folders", text)
        self.assertIn("KB", text)
        self.assertTrue(text.isascii())


# ---------------------------------------------------------------------- the launcher


class LauncherCleanupTest(unittest.TestCase):
    def test_the_launcher_cleans_when_it_starts_with_its_own_folders(self):
        with mock.patch.object(rp, "clean_tracer_files", wraps=rp.clean_tracer_files) as spy, \
                tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            spy.assert_called_once()
            args = spy.call_args.args
            self.assertEqual(args, (paths.reports, paths.captures_dir, paths.render_traces))

    def test_the_launcher_shows_what_was_removed_and_goes_on(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            w = World(paths.user)
            for i in range(22):
                w.session(i)
            app._clean_tracer_files()
            self.assertIn("Tracer cleanup: removed", app.status.cget("text"))
            self.assertFalse((w.traces / stamp(w.start(0) - 1)).exists())
            self.assertEqual(str(app.play.cget("state")), "normal")

    def test_a_cleanup_that_fails_does_not_stop_the_launcher(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            with mock.patch.object(rp, "clean_tracer_files", side_effect=RuntimeError("disk gone")):
                app._clean_tracer_files()
            self.assertIn("disk gone", app.status.cget("text"))
            self.assertEqual(str(app.play.cget("state")), "normal")

    def test_a_stuck_file_is_named_in_the_status(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            w = World(paths.user)
            sessions = [w.session(i) for i in range(23)]
            stuck = sessions[0]["files"][0]
            real = Path.unlink

            def unlink(path, *a, **k):
                if path == stuck:
                    raise PermissionError(13, "in use")
                return real(path, *a, **k)
            with mock.patch.object(Path, "unlink", unlink):
                app._clean_tracer_files()
            text = app.status.cget("text")
            self.assertIn("could not be removed", text)
            self.assertTrue(stuck.exists())

    def test_a_report_that_cannot_be_read_is_told_in_the_status(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            w = World(paths.user)
            for i in range(21):
                w.session(i)
            (w.reports / f"session-{stamp(w.start(20))}.json").write_text("{broken", encoding="utf-8")
            app._clean_tracer_files()
            self.assertIn("Tracer cleanup skipped", app.status.cget("text"))

    def test_nothing_to_clean_leaves_the_status_alone(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            before = app.status.cget("text")
            app._clean_tracer_files()
            self.assertEqual(app.status.cget("text"), before)

    def test_cleanup_does_not_run_while_a_game_of_this_launcher_runs(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, iconify):
            app.proc = mock.Mock(poll=mock.Mock(return_value=None))
            with mock.patch.object(rp, "clean_tracer_files") as clean:
                app._clean_tracer_files()
            clean.assert_not_called()


if __name__ == "__main__":
    unittest.main()
