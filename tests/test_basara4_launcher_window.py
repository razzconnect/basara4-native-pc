"""The launcher window around a game start (replaces the upstream test_launcher_run_log.py,
test_launcher_close_on_play.py, test_launcher_icon.py and test_launcher_controls.py):
user\\last_run.log, minimising on --play, the window icons, and the controller and keyboard/mouse
controls. Uses a real Tk window like test_basara4_render_trace.py; the game is never started
(subprocess is mocked) and the game folder is synthetic.
"""
import contextlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl, game_folder, launcher_folders

tk, ttk = bl.tk, bl.ttk


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@contextlib.contextmanager
def launcher(tmp, play_now=False, cfg=None):
    with launcher_folders(tmp) as paths, contextlib.ExitStack() as stack:
        paths.user.mkdir(parents=True, exist_ok=True)
        game = game_folder(tmp)
        paths.config.write_text(json.dumps(dict(cfg or {}, game=str(game))), encoding="utf-8")
        stack.enter_context(mock.patch.object(bl.Launcher, "_check_update_thread", lambda self: None))
        iconify = stack.enter_context(mock.patch.object(bl.Launcher, "iconify"))
        app = bl.Launcher(play_now=play_now)
        try:
            app.update_idletasks()
            yield app, paths, game, iconify
        finally:
            for pending in app.tk.splitlist(app.tk.call("after", "info")):  # _poll, update check, ...
                app.after_cancel(pending)
            app.destroy()


class RunLogAndPlayTests(unittest.TestCase):
    def start(self, app, game, isa_table=False):
        if isa_table:
            bl.OUT.mkdir(parents=True, exist_ok=True)
            (bl.OUT / "isa-rewrite.bin").write_bytes(b"table")
        app.pending = (None, game, bl.CORE, ["--patches", "p.bin"], "", "dlc")
        with mock.patch.object(bl.subprocess, "Popen") as popen:
            app._wait_prepare()
        call = popen.call_args
        call.kwargs["stdout"].close()
        return call

    def test_game_output_goes_to_last_run_log_in_user(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            call = self.start(app, game)
            self.assertEqual(Path(call.kwargs["stdout"].name), paths.log)
            self.assertEqual(call.kwargs["stderr"], bl.subprocess.STDOUT)
            self.assertEqual(call.kwargs["cwd"], str(paths.user))
            args = call.args[0]
            self.assertEqual(args[:2], [str(paths.core), str(paths.out / "boot-linked.bin")])
            self.assertEqual(args[-2:], ["--patches", "p.bin"])
            self.assertNotIn("--isa-rewrite", args)
            call = self.start(app, game, isa_table=True)  # the table is passed only when it exists
            args = call.args[0]
            self.assertEqual(args[args.index("--isa-rewrite") + 1], str(paths.out / "isa-rewrite.bin"))

    def test_open_log_opens_it_or_says_there_is_none(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, _game, _), \
                mock.patch.object(bl.os, "startfile", create=True) as startfile, \
                mock.patch.object(bl.messagebox, "showinfo") as showinfo:
            app.open_log()
            startfile.assert_not_called()
            showinfo.assert_called_once()
            paths.log.write_text("Runtime: started\n", encoding="utf-8")
            app.open_log()
            startfile.assert_called_once_with(paths.log)

    def test_play_shortcut_minimises_the_launcher_and_errors_bring_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with launcher(tmp) as (_app, _paths, _game, iconify):
                iconify.assert_not_called()  # opened normally: stays visible
            with launcher(tmp, play_now=True) as (app, paths, game, iconify):
                iconify.assert_called_once()
                (game / "eboot.bin").unlink()
                with mock.patch.object(bl.Launcher, "deiconify") as deiconify, \
                        mock.patch.object(bl.messagebox, "showerror") as showerror:
                    app.on_play()  # no eboot.bin: the window comes back with the error
                deiconify.assert_called_once()
                showerror.assert_called_once()
                self.assertIsNone(app.proc)

    def play_with_vulkan(self, vulkan, answer, play_now=False, cfg=None):
        """on_play with the Vulkan probe `vulkan` and the user's `answer` to the warning:
        (askyesno mock, deiconify mock, whether the game was prepared)."""
        with tempfile.TemporaryDirectory() as tmp, \
                launcher(tmp, play_now=play_now, cfg=cfg) as (app, paths, _game, _iconify):
            paths.core.parent.mkdir(parents=True, exist_ok=True)
            paths.core.write_bytes(b"synthetic runtime")
            with mock.patch.object(bl, "VULKAN", vulkan), \
                    mock.patch.object(bl, "SYSTEM_GPUS", bl.gpu_choices(vulkan)), \
                    mock.patch.object(bl, "check_commit_limit_mb", return_value=None), \
                    mock.patch.object(bl.threading, "Thread") as thread, \
                    mock.patch.object(bl.Launcher, "deiconify") as deiconify, \
                    mock.patch.object(bl.messagebox, "askyesno", return_value=answer) as ask:
                app._load_ini_into_ui()  # the GPU dropdown shows the saved gpu_index of this probe
                app.on_play()
            return ask, deiconify, thread.called

    def test_vulkan_warning_before_the_game_starts(self):
        old_loader = bl.VulkanProbe(True, (1, 0), False, [], "")
        ask, deiconify, started = self.play_with_vulkan(old_loader, answer=False, play_now=True)
        self.assertEqual(ask.call_args.args, bl.vulkan_warning("old", old_loader, None))
        self.assertEqual(ask.call_args.args[0], "Vulkan Terlalu Lama")
        self.assertIn("baru mendukung Vulkan 1.0.", ask.call_args.args[1])
        self.assertEqual((ask.call_args.kwargs["icon"], ask.call_args.kwargs["default"]), ("warning", "no"))
        deiconify.assert_called_once()  # the --play shortcut brings the window back for the question
        self.assertFalse(started)  # "No": the game is not started
        ask, _, started = self.play_with_vulkan(old_loader, answer=True)
        self.assertTrue(started)  # "Yes": it starts anyway
        missing = bl.VulkanProbe(False, None, False, [], "vulkan-1.dll: not found")
        ask = self.play_with_vulkan(missing, answer=False)[0]
        self.assertEqual(ask.call_args.args[0], "Vulkan Tidak Ditemukan")
        self.assertEqual(ask.call_args.args, bl.vulkan_warning("missing", missing, None))

    def test_vulkan_warning_follows_the_chosen_gpu(self):
        mixed = bl.VulkanProbe(True, (1, 3), True, [("Old dGPU", 2, (1, 2)), ("New iGPU", 1, (1, 3))], "")
        ask, _, started = self.play_with_vulkan(mixed, answer=False)  # Auto: the discrete GPU
        ask.assert_called_once()
        self.assertIn("baru mendukung Vulkan 1.2.", ask.call_args.args[1])  # the GPU the game would use
        self.assertFalse(started)
        ask, deiconify, started = self.play_with_vulkan(mixed, answer=False, cfg={"gpu_index": 1})
        ask.assert_not_called()
        deiconify.assert_not_called()
        self.assertTrue(started)

    def test_no_warning_on_a_vulkan_1_3_pc(self):
        ready = bl.VulkanProbe(True, (1, 4), True, [("RTX", 2, (1, 4))], "")
        ask, deiconify, started = self.play_with_vulkan(ready, answer=False)
        ask.assert_not_called()
        deiconify.assert_not_called()
        self.assertTrue(started)

    def test_crash_report_carries_the_vulkan_line(self):
        vulkan = bl.VulkanProbe(True, (1, 0), False, [], "")
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, _paths, _game, _iconify), \
                mock.patch.object(bl, "VULKAN", vulkan), \
                mock.patch.object(bl.messagebox, "showinfo"), \
                mock.patch.object(bl.Launcher, "clipboard_clear"), \
                mock.patch.object(bl.Launcher, "clipboard_append") as append:  # the user's clipboard stays as it is
            app._show_crash_dialog(0xC0000139)
            dialog = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            copy = next(w for w in descendants(dialog)
                        if isinstance(w, ttk.Button) and "Clipboard" in str(w.cget("text")))
            copy.invoke()
        report = append.call_args.args[0]
        self.assertIn("Exit Code     : 0xc0000139\n", report)
        self.assertIn("Vulkan        : loader 1.0, no instance; needs 1.3\n", report)

    # ---- the session report: written before the game starts, completed when it ends ----
    def game_with_sfo(self, game, app_ver="01.02"):
        from test_basara4_cheats import build_sfo
        (game / "sce_sys").mkdir(exist_ok=True)
        (game / "sce_sys" / "param.sfo").write_bytes(build_sfo({"TITLE_ID": "CUSA01159", "APP_VER": app_ver}))
        bl.OUT.mkdir(parents=True, exist_ok=True)
        bl.STAMP.write_text(json.dumps({"eboot_sha256": "1a2b3c4d" + "0" * 56, "game": str(game)}), encoding="utf-8")

    def reports(self, paths):
        return sorted((paths.user / "reports").glob("session-*.json"))

    def test_report_exists_before_the_game_starts_and_is_completed_at_the_end(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp, cfg={"resolution": "720p"}) as (app, paths, game, _):
            self.game_with_sfo(game)
            seen = {}

            def popen(*args, **kwargs):
                seen["reports"] = [json.loads(p.read_text(encoding="utf-8")) for p in self.reports(paths)]
                return mock.Mock()
            app.pending = (None, game, bl.CORE, [], "", "dlc")
            with mock.patch.object(bl.subprocess, "Popen", side_effect=popen) as started:
                app._wait_prepare()
            started.call_args.kwargs["stdout"].close()
            self.assertEqual(started.call_args.kwargs["env"]["SPARTAN_FILE_TRACE"], "1")
            report, = seen["reports"]  # already on disk when the game was started
            self.assertEqual(report["status"], "started")
            self.assertEqual((report["game"]["app_ver"], report["game"]["title_id"], report["game"]["eboot_sha8"]),
                             ("01.02", "CUSA01159", "1a2b3c4d"))
            self.assertEqual(report["game"]["folder"], "<game>")
            self.assertEqual((report["settings"]["resolution"], report["settings"]["render_size"]), ("720p", "1280x720"))
            for key in ("os", "cpu"):
                self.assertIn(key, report["system"])
            self.assertIn(report["vulkan"]["check"], ("ok", "old", "missing", "no_gpu"))
            self.assertEqual(report["launcher"]["version"], bl.VERSION)
            # The game crashed: the report is completed and a copy of its text is kept in user\crash.
            paths.log.write_text("\n".join([
                "Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found",
                "File trace: /app0/nativePS4/rom/ui/pl/pl012/pl012.arc (4096 bytes)",
                "Runtime: open(/app0/nativePS4/ui/gui/idrama/idrama00_ps4.gui) -> not found",
                "Fault: read access to 0000000000000000 at guest offset 0xaebfb7 (thread 1)"]), encoding="utf-8")
            app._finish_session_report(0xC0000005)
            report = json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "finished")
            self.assertTrue(report["end"]["crashed"])
            self.assertEqual(report["log"]["unusual_not_found"], ["/app0/nativePS4/ui/gui/idrama/idrama00_ps4.gui"])
            self.assertEqual(report["log"]["last_files"], [{"path": "/app0/nativePS4/rom/ui/pl/pl012/pl012.arc",
                                                            "bytes": 4096}])
            self.assertTrue(report["signature"].startswith("exit=C0000005 | read:null | top=g:0xaebfb7"))
            self.assertIn("build=01.02/1a2b3c4d/", report["signature"])
            crash_copy, = (paths.user / "crash").glob("report-*.txt")
            self.assertIn("Crash: read null di g:0xaebfb7.", crash_copy.read_text(encoding="utf-8"))

    def test_a_game_that_never_logs_still_has_a_full_report(self):
        missing = bl.VulkanProbe(False, None, False, [], "vulkan-1.dll: not found")
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl, "VULKAN", missing), mock.patch.object(bl, "SYSTEM_GPUS", bl.gpu_choices(missing)):
            self.game_with_sfo(game)
            self.assertEqual(app._start_session_report(game), "")
            paths.log.write_bytes(b"")  # 0xC0000139: Windows refused the program, it wrote nothing
            app._finish_session_report(0xC0000139)
            report = json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))
            self.assertEqual((report["log"]["lines"], report["end"]["exit_hex"]), (0, "0xC0000139"))
            self.assertEqual(report["vulkan"]["check"], "missing")
            self.assertEqual(report["game"]["app_ver"], "01.02")
            self.assertTrue(any("0xC0000139" in hint for hint in report["hints"]))
            text = self.reports(paths)[0].with_suffix(".txt").read_text(encoding="utf-8")
            self.assertIn("Game berhenti sebelum menulis log (exit 0xC0000139).", text)

    def test_unreadable_parts_are_written_with_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            app._start_session_report(game)  # no param.sfo, no prepared.json
            report = json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))
            self.assertIn("param.sfo not readable", report["game"]["app_ver"]["unknown"])
            self.assertIn("prepared.json not readable", report["game"]["eboot_sha8"]["unknown"])

    def test_a_report_folder_that_cannot_be_written_does_not_stop_the_game(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.basara4_report, "write_atomic", side_effect=PermissionError(13, "Access is denied")):
            self.assertIn("Session report not written (PermissionError: Access is denied)",
                          app._start_session_report(game))
            app._finish_session_report(1)
            self.assertIn("Session report not saved (PermissionError: Access is denied)", app.stop_note)

    def test_unfinished_sessions_are_marked_when_the_launcher_opens(self):
        with tempfile.TemporaryDirectory() as tmp:
            with launcher(tmp) as (app, paths, game, _):
                app._start_session_report(game)  # the launcher closes before the game ends
            with launcher(tmp) as (app, paths, game, _):
                self.assertEqual(json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))["status"], "unfinished")

    def test_send_report_makes_one_zip_and_opens_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.os, "startfile", create=True) as startfile, \
                mock.patch.object(bl.messagebox, "showinfo") as showinfo:
            self.game_with_sfo(game)
            app._start_session_report(game)
            paths.log.write_text("Fault: write access to 0000000000000010 at guest offset 0x9c859c (thread 3)\n",
                                 encoding="utf-8")
            (paths.user / "crash").mkdir(exist_ok=True)
            (paths.user / "crash" / "core-1.dmp").write_bytes(b"MDMP")
            (paths.user / "captures").mkdir(exist_ok=True)
            (paths.user / "captures" / "frame.png").write_bytes(b"\x89PNG")
            app._finish_session_report(0xC0000005)
            target = app.send_report()
            startfile.assert_called_once_with(paths.user / "reports")
            self.assertIn(target.name, showinfo.call_args.args[1])
            with zipfile.ZipFile(target) as zf:
                # The crash dump holds the process environment and is never zipped: named, sized, kept on the PC.
                self.assertEqual(sorted(zf.namelist()), ["captures/frame.png", "last_run.log",
                                                         "report.json", "report.txt"])
                report = json.loads(zf.read("report.json"))
                self.assertEqual(report["end"]["exit_hex"], "0xC0000005")
                self.assertEqual(report["crash_dump_kept_on_pc"], {"file": "core-1.dmp", "bytes": 4,
                                                                   "folder": "user\\crash"})
                self.assertIn("crash/core-1.dmp (4 bytes): disimpan di PC", zf.read("report.txt").decode("utf-8"))
            self.assertTrue((paths.user / "crash" / "core-1.dmp").is_file())  # the dump itself stays
            text = showinfo.call_args.args[1]
            self.assertIn("File crash dump (.dmp) tidak ikut", text)
            self.assertIn("pesan pribadi (DM)", text)
            self.assertNotIn("tidak perlu dijelaskan lagi", text)

    def test_crash_dialog_offers_send_report_and_masks_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.dict(bl.os.environ, {"USERNAME": "QA_LEAK_7f3a"}), \
                mock.patch.object(bl.messagebox, "showinfo"), \
                mock.patch.object(bl.Launcher, "clipboard_clear"), \
                mock.patch.object(bl.Launcher, "clipboard_append") as append, \
                mock.patch.object(bl.Launcher, "send_report") as send:
            paths.log.write_text("Settings: C:\\Users\\QA_LEAK_7f3a\\b4\\user\\basara4.ini\n"
                                 "Fault: read access to 0000000000000000 at guest offset 0xaebfb7 (thread 1)\n",
                                 encoding="utf-8")
            app._show_crash_dialog(0xC0000005)
            dialog = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            buttons = [w for w in descendants(dialog) if isinstance(w, ttk.Button)]
            self.assertEqual(str(buttons[0].cget("text")), "📦 Kirim laporan")  # the first, main action
            buttons[0].invoke()
            send.assert_called_once()
            next(b for b in buttons if "Clipboard" in str(b.cget("text"))).invoke()
        self.assertNotIn("qa_leak_7f3a", append.call_args.args[0].lower())

    def test_crash_dialog_names_a_stub_called_just_before_the_crash(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            paths.log.write_text("GPU [Lib.SaveData] <Error> savedata.cpp:12 sceSaveDataMount5: (STUBBED) called\n"
                                 "Fault: read access to 0000000000000000 at guest offset 0xaebfb7 (thread 1)\n",
                                 encoding="utf-8")
            app._show_crash_dialog(0xC0000005)
            dialog = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            texts = [str(w.cget("text")) for w in descendants(dialog) if isinstance(w, tk.Label)]
            self.assertTrue(any("sceSaveDataMount5" in text and "tidak biasa" in text for text in texts))

    # ---- the report never stops PLAY or the poll loop, and keeps nothing private ----
    def test_a_failing_collector_does_not_stop_play(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl, "system_info", side_effect=RuntimeError("WMI is broken")), \
                mock.patch.object(bl, "content_info", side_effect=AttributeError("'list' object has no attribute 'get'")):
            app.pending = (None, game, bl.CORE, [], "", "dlc")
            with mock.patch.object(bl.subprocess, "Popen") as popen:
                app._wait_prepare()
            popen.call_args.kwargs["stdout"].close()
            popen.assert_called_once()  # the game started
            report = json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))
            self.assertEqual(report["system"], {"unknown": "RuntimeError: WMI is broken"})
            self.assertEqual(report["content"], {"unknown": "AttributeError: 'list' object has no attribute 'get'"})
            self.assertEqual(report["collect_errors"], ["system: RuntimeError: WMI is broken",
                                                        "content: AttributeError: 'list' object has no attribute 'get'"])

    def test_a_report_that_cannot_be_built_still_starts_the_game(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.basara4_report, "new_report", side_effect=TypeError("broken")):
            self.assertEqual(app._start_session_report(game), "Session report not written (TypeError: broken).  ")

    def test_a_failing_finish_keeps_the_poll_loop_running(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.basara4_report, "analyze_log", side_effect=RuntimeError("bad log")), \
                mock.patch.object(bl.Launcher, "_show_crash_dialog"), \
                mock.patch.object(bl.Launcher, "after") as after:
            app._start_session_report(game)
            paths.log.write_text("Runtime: started\n", encoding="utf-8")
            app.proc = mock.Mock()
            app.proc.poll.return_value = 5
            app.proc.returncode = 5
            app._poll()
            after.assert_called_with(500, app._poll)  # the loop goes on
            report = json.loads(self.reports(paths)[0].read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "finished")
            self.assertEqual(report["end"]["exit_code"], 5)
            self.assertEqual(report["log"], {"unknown": "report not completed: RuntimeError: bad log"})
            self.assertIn("finish: RuntimeError: bad log", report["collect_errors"])
            # Called directly (without _poll, which shows and clears the note), the reason is in the status note.
            app._start_session_report(game)
            app._finish_session_report(5)
            self.assertIn("Session report incomplete (RuntimeError: bad log)", app.stop_note)

    def test_crash_copy_in_user_crash_is_masked(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.dict(bl.os.environ, {"USERNAME": "QA_LEAK_7f3a", "COMPUTERNAME": "PC-QA_LEAK_7f3a"}):
            app._start_session_report(game)
            paths.log.write_text("Settings: C:\\Users\\QA_LEAK_7f3a\\b4\\user\\basara4.ini on PC-QA_LEAK_7f3a\n"
                                 "Fault: read access to 0000000000000000 at guest offset 0xaebfb7 (thread 1)\n",
                                 encoding="utf-8")
            app._finish_session_report(0xC0000005)
            crash_copy, = (paths.user / "crash").glob("report-*.txt")
            text = crash_copy.read_text(encoding="utf-8")
            self.assertNotIn("qa_leak_7f3a", text.lower())
            self.assertIn("<pc>", text)

    def test_a_game_folder_name_never_reaches_the_report(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, _game, _), \
                mock.patch.object(bl.os, "startfile", create=True), mock.patch.object(bl.messagebox, "showinfo"):
            game = Path(tmp) / "GAMES QAFOLDER 7f3a" / "Basara4"  # no param.sfo: the error names the folder
            game.mkdir(parents=True)
            app.cfg["game"] = str(game).upper()  # saved in another case than the error message writes it
            app._start_session_report(game)
            paths.log.write_text(f"io_file.cpp:202 Open: path={str(game).lower()}\\\\x\n", encoding="utf-8")
            app._finish_session_report(0xC0000005)
            zip_path = app.send_report()
            texts = [p.read_text(encoding="utf-8") for p in (paths.user / "reports").glob("session-*")]
            texts += [p.read_text(encoding="utf-8") for p in (paths.user / "crash").glob("report-*.txt")]
            with zipfile.ZipFile(zip_path) as zf:
                texts += [zf.read(name).decode("utf-8") for name in zf.namelist()]
            self.assertEqual(len(texts), 6)  # session .json/.txt, crash copy, zip report.json/.txt/last_run.log
            for text in texts:
                self.assertNotIn("qafolder", text.lower())

    def test_a_dump_from_an_earlier_session_is_not_named(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.os, "startfile", create=True), mock.patch.object(bl.messagebox, "showinfo"):
            (paths.user / "crash").mkdir(parents=True)
            old = paths.user / "crash" / "core-old.dmp"
            old.write_bytes(b"MDMP")
            bl.os.utime(old, (1_000_000_000, 1_000_000_000))  # written long before this session
            app._start_session_report(game)
            paths.log.write_text("Fault: read access to 0000000000000000 at guest offset 0x10 (thread 1)\n",
                                 encoding="utf-8")
            app._finish_session_report(0xC0000005)
            with zipfile.ZipFile(app.send_report()) as zf:
                report = json.loads(zf.read("report.json"))
                self.assertNotIn("crash_dump_kept_on_pc", report)
                self.assertNotIn("core-old", zf.read("report.txt").decode("utf-8"))

    def test_send_report_without_a_session_still_describes_the_pc(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.os, "startfile", create=True), mock.patch.object(bl.messagebox, "showinfo"):
            with zipfile.ZipFile(app.send_report()) as zf:
                report = json.loads(zf.read("report.json"))
            self.assertEqual(report["status"], "no-session")
            self.assertIn("os", report["system"])
            self.assertIn("check", report["vulkan"])


class IconTests(unittest.TestCase):
    def test_launcher_icon_file(self):
        icon = Path(bl.__file__).resolve().parent / "basara4.ico"
        self.assertEqual(icon.read_bytes()[:4], b"\x00\x00\x01\x00")  # ICO header

    def test_game_window_icon_is_made_once_from_the_game_icon(self):
        if bl.Image is None:
            self.fail("Pillow is required by the launcher package (packaging/windows/README.md)")
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            (game / "sce_sys").mkdir()
            bl.Image.new("RGB", (512, 512), (200, 30, 40)).save(game / "sce_sys" / "icon0.png")  # synthetic
            app._make_window_icon(game)
            with bl.Image.open(paths.window_icon) as made:
                self.assertEqual((made.format, made.size), ("BMP", (128, 128)))
            before = paths.window_icon.read_bytes()
            bl.Image.new("RGB", (512, 512), (0, 0, 0)).save(game / "sce_sys" / "icon0.png")
            app._make_window_icon(game)  # kept: not made again
            self.assertEqual(paths.window_icon.read_bytes(), before)
            self.assertEqual(app._env()["SPARTAN_WINDOW_ICON"], str(paths.window_icon))

    def test_no_game_icon_no_window_icon(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            app._make_window_icon(game)
            self.assertFalse(paths.window_icon.exists())
            self.assertNotIn("SPARTAN_WINDOW_ICON", app._env())


class ControlsTests(unittest.TestCase):
    def keymap_window(self, app):
        app.open_keymap_ui()
        app.update_idletasks()
        windows = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(windows), 1)
        return windows[0]

    def buttons(self, window):
        return {w.cget("text"): w for w in descendants(window) if isinstance(w, (tk.Button, ttk.Button))}

    def test_keymap_shows_the_bindings_and_saves_a_new_one(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp, cfg={"keymap": {"cross": "J"}}) as \
                (app, paths, _game, _), mock.patch.object(bl.messagebox, "showinfo"):
            window = self.keymap_window(app)
            buttons = self.buttons(window)
            self.assertIn("J", buttons)                # cross, saved earlier (short names in capitals)
            self.assertIn("Mouse Left", buttons)       # square, the PC default
            self.assertEqual(sum(isinstance(w, tk.Button) for w in descendants(window)),
                             len(bl.ACTION_DESCRIPTIONS))
            buttons["Mouse Left"].invoke()             # rebind square ...
            window.update()                            # mapped, so it receives the key
            window.focus_force()
            window.event_generate("<Key>", keysym="k", when="now")   # ... to K
            self.assertEqual(buttons["Mouse Left"].cget("text"), "K")
            buttons["💾 Simpan Pengaturan"].invoke()
            saved = json.loads(paths.config.read_text(encoding="utf-8"))
            self.assertEqual((saved["keymap"]["square"], saved["keymap"]["cross"]), ("K", "J"))
            self.assertEqual((saved["mouse_camera"], saved["mouse_sens"]), (True, 100))
            self.assertIn("square=K", app._env()["SPARTAN_KEY_MAP"].split(","))

    def test_keymap_reset_restores_the_pc_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, \
                launcher(tmp, cfg={"keymap": {"cross": "J"}, "mouse_camera": False, "mouse_sens": 40}) as \
                (app, paths, _game, _), mock.patch.object(bl.messagebox, "showinfo"):
            window = self.keymap_window(app)
            buttons = self.buttons(window)
            buttons["🔄 Reset ke Default PC"].invoke()
            self.buttons(window)["💾 Simpan Pengaturan"].invoke()
            saved = json.loads(paths.config.read_text(encoding="utf-8"))
            self.assertEqual(saved["keymap"], bl.DEFAULT_PC_KEYMAP)
            self.assertEqual((saved["mouse_camera"], saved["mouse_sens"]), (True, 100))

    def test_controller_mapper_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, _game, _), \
                mock.patch.object(bl.subprocess, "Popen") as popen, \
                mock.patch.object(bl.messagebox, "showerror") as showerror:
            app.map_pad(["--second"])
            showerror.assert_called_once()  # the mapper is missing: nothing starts
            popen.assert_not_called()
            paths.bin.mkdir()
            mapper = paths.bin / "basara4-pad-mapper.exe"
            mapper.write_bytes(b"MZ")
            app.map_pad(["--second"])
            self.assertEqual(popen.call_args.args[0], [str(mapper), str(paths.mapping), "--second"])
            self.assertTrue(popen.call_args.kwargs["env"]["PATH"].startswith(str(paths.bin)))
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, _game, _):
            paths.mapping.write_text("mapping", encoding="utf-8")
            with mock.patch.object(bl.messagebox, "askyesno", return_value=False):
                app.reset_map()
            self.assertTrue(paths.mapping.exists())
            with mock.patch.object(bl.messagebox, "askyesno", return_value=True):
                app.reset_map()
            self.assertFalse(paths.mapping.exists())
            self.assertEqual(Path(str(paths.mapping) + ".bak").read_text(encoding="utf-8"), "mapping")


if __name__ == "__main__":
    unittest.main()
