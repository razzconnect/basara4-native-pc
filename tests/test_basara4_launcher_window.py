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
