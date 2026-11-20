"""The main window after the tidy-up: one Controller Mapping button (with a drawn controller icon) that opens the
Controller window, where Map all, Map sticks only and Reset are; Cheats and Manage DLC in the Game section; and every
text of the launcher in English. The values the launcher saves (resolution, input mode, GPU) are the same as before:
only the words shown for them changed. No window of the real launcher is shown on screen (alpha 0 when drawn) and
nothing is started: the mapper, Explorer and the browser are replaced."""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl
from paths import ROOT
from test_basara4_launcher_window import descendants, launcher

tk, ttk = bl.tk, bl.ttk
bc = bl.basara4_controller

# Words that only Indonesian text has (a word that is also English, such as "skill" or "default", is not listed).
INDONESIAN_WORDS = (
    "yang dan atau untuk dengan tidak anda kamu silakan simpan tersimpan batal aktifkan pengaturan kontrol kamera "
    "tombol tekan klik memeriksa pembaruan mengunduh unduhan menghubungkan menunggu mengekstrak menerapkan memulai "
    "tersedia terbaru menggunakan berhasil dikembalikan kentang prioritaskan diskrit serangan biasa lompat tangkis "
    "segitiga kotak lingkaran silang navigasi perintah sensitivitas pemetaan mengikat pergerakan terikat karakter "
    "ukuran jendela diagnostik dipisahkan bersamaan semuanya bisa agar tiap aktif sudah belum gagal laporan kiri "
    "kanan atas bawah stik utama tambahan mengubah secara otomatis terhapus lepaskan lewati colokkan jalankan "
    "memori cukup perubahan dikenali terbaca terhubung dorong diputar didorong dipakai bila kecil tengah diam "
    "detik pilih lagi dalam terputus terlalu panjang besar disimpan baris ke di pada dari").split()
INDONESIAN = re.compile(r"\b(" + "|".join(INDONESIAN_WORDS) + r")\b", re.IGNORECASE)
# A name of files the launcher cleans up, not text a player reads: the zips had this name before the messages were
# English only (basara4_report.prune still removes them).
NOT_TEXT = {"laporan-*.zip"}
MODULES = ("basara4_launcher", "basara4_report", "basara4_patches", "basara4_cheats", "basara4_dlc",
           "basara4_controller")


def string_literals(source):
    """Every string literal of a Python source that is not a docstring, with its line."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def find(app, kind, text):
    return next((w for w in descendants(app) if isinstance(w, kind) and str(w.cget("text")).strip() == text), None)


def button_texts(widget):
    return {str(w.cget("text")).strip() for w in widget.winfo_children() if isinstance(w, ttk.Button)}


def set_scaling(value):
    """Set the display's scaling with a throwaway window (the next window made keeps it)."""
    root = tk.Tk()
    root.withdraw()
    root.tk.call("tk", "scaling", value)
    root.update()
    root.destroy()


def current_scaling():
    root = tk.Tk()
    root.withdraw()
    value = float(root.tk.call("tk", "scaling"))
    root.destroy()
    return value


class MainWindowTests(unittest.TestCase):
    def test_one_controller_mapping_button_with_an_icon(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            mapping = find(app, ttk.Button, "Controller Mapping")
            self.assertIsNotNone(mapping, "the Controller Mapping button is missing")
            names = app.tk.splitlist(mapping.cget("image"))
            self.assertTrue(names, "the button must carry the drawn controller icon")
            self.assertEqual(str(mapping.cget("compound")), "left")
            self.assertEqual(str(app.tk.call("image", "type", names[0])), "photo")  # drawn with Tk itself: no file, no font
            self.assertGreater(int(app.tk.call("image", "width", names[0])), 8)

    def test_the_old_mapping_buttons_are_gone_from_the_main_window(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            texts = {str(w.cget("text")).strip() for w in descendants(app) if isinstance(w, ttk.Button)}
            for old in ("Map controller", "Map sticks only", "Reset mapping"):
                self.assertNotIn(old, texts)

    def test_the_controller_row_holds_the_keyboard_button_and_the_mapping_button_only(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            keyboard = next(w for w in descendants(app) if isinstance(w, ttk.Button)
                            and "Keyboard & Mouse Controls" in str(w.cget("text")))
            self.assertEqual({t.split(" ", 1)[-1] if "Keyboard" in t else t for t in button_texts(keyboard.master)},
                             {"Keyboard & Mouse Controls", "Controller Mapping"})

    def test_the_mapping_button_opens_the_controller_window_and_never_a_second(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.messagebox, "showerror"):
            paths.bin.mkdir(parents=True, exist_ok=True)
            (paths.bin / "basara4-pad-mapper.exe").write_bytes(b"MZ")
            with mock.patch.object(bc.subprocess, "Popen") as popen:
                find(app, ttk.Button, "Controller Mapping").invoke()
                window = app.pad_window
                window.attributes("-alpha", 0.0)
                self.assertIsInstance(window, bc.ControllerWindow)
                self.assertFalse(window.sticks_only)  # the whole controller first
                find(app, ttk.Button, "Controller Mapping").invoke()
                self.assertIs(app.pad_window, window)
                popen.assert_not_called()  # nothing runs before Map all
                window.close()

    def test_the_button_calls_the_old_behaviour_with_the_old_argument(self):
        with mock.patch.object(bl.Launcher, "map_pad") as map_pad, \
                tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            find(app, ttk.Button, "Controller Mapping").invoke()
            map_pad.assert_called_once_with([])

    def test_cheats_and_manage_dlc_sit_in_the_game_section_and_still_work(self):
        with mock.patch.object(bl.Launcher, "open_cheats_manager") as cheats, \
                mock.patch.object(bl.Launcher, "open_dlc_manager") as dlc, \
                tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            cheats_button, dlc_button = find(app, ttk.Button, "Cheats…"), find(app, ttk.Button, "Manage DLC…")
            self.assertIsNotNone(cheats_button)
            self.assertIsNotNone(dlc_button)
            heads = {str(w.cget("text")): w for w in descendants(app)
                     if isinstance(w, ttk.Label) and str(w.cget("style")) == "Head.TLabel"}
            panel = heads["Game"].master
            holder = cheats_button.master
            self.assertIs(dlc_button.master, holder)  # one row for both
            self.assertIs(holder.master, panel)  # a row of the panel that holds the sections
            game_row = int(heads["Game"].grid_info()["row"])
            display_row = int(heads["Display"].grid_info()["row"])
            input_row = int(heads["Controller & Input"].grid_info()["row"])
            self.assertTrue(game_row <= int(holder.grid_info()["row"]) < display_row,
                            "Cheats and Manage DLC belong to the Game section")
            self.assertLess(display_row, input_row)
            cheats_button.invoke()
            dlc_button.invoke()
            cheats.assert_called_once_with()
            dlc.assert_called_once_with()
            # not in the Controller & Input area any more
            self.assertTrue(all(t not in button_texts(find(app, ttk.Button, "Open log").master)
                                for t in ("Cheats…", "Manage DLC…")))

    def test_the_other_buttons_of_the_old_rows_are_still_there(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            for name in ("Open log", "Open audio log", "Open captures", "Open saves", "Credits", "Check Update",
                         "Browse…", "PLAY", "Stop game", "Report", "Open render log"):
                self.assertIsNotNone(find(app, ttk.Button, name), name)
            self.assertIsNotNone(find(app, ttk.Button, "⌨️ Keyboard & Mouse Controls"))

    def test_the_play_button_keeps_its_size(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            app.attributes("-alpha", 0.0)
            app.geometry("+-3000+0")
            app.update()
            self.assertEqual((app.play.winfo_width(), app.play.winfo_height()), (214, 54))  # measured before the tidy-up

    def test_rows_fit_as_before_or_better_at_every_windows_scale(self):
        # Measured before the tidy-up (alpha 0, off screen, the scaling set BEFORE the window is made: a window made
        # first does not lay out at the new scale): the input mode row needed 652 / 830 / 942 px at 100 / 125 / 150 %,
        # the Controller row 600 / 736 / 831 and the row of Open buttons (then with Cheats and Manage DLC) 679 / 824 / 939.
        # A row cut off before stays no worse; a row that fitted still fits.
        old = {"input": {1.0: 652, 1.25: 830, 1.5: 942}, "controller": {1.0: 600, 1.25: 736, 1.5: 831},
               "open": {1.0: 679, 1.25: 824, 1.5: 939}}
        original = current_scaling()
        try:
            for scale in (1.0, 1.25, 1.5):
                set_scaling(96 / 72 * scale)
                with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
                    app.attributes("-alpha", 0.0)
                    app.geometry("+-3000+0")
                    app.update()
                    rows = {"input": next(w for w in descendants(app) if isinstance(w, ttk.Radiobutton)).master,
                            "controller": find(app, ttk.Button, "Controller Mapping").master,
                            "open": find(app, ttk.Button, "Open log").master,
                            "game": find(app, ttk.Button, "Cheats…").master}
                    for name, row in rows.items():
                        needs = row.winfo_reqwidth()
                        if name in old:
                            self.assertLessEqual(needs, old[name][scale] + 2, f"{name} row at {scale}")
                        if scale == 1.0:
                            self.assertLessEqual(needs, row.master.winfo_width(), f"{name} row fits at 100%")
                    self.assertLessEqual(rows["game"].winfo_reqwidth(), rows["game"].master.winfo_width(),
                                         f"the Game section buttons fit at {scale}")
        finally:
            set_scaling(original)


class ControllerWindowActionsTests(unittest.TestCase):
    def open_window(self, app, paths):
        paths.bin.mkdir(parents=True, exist_ok=True)
        (paths.bin / "basara4-pad-mapper.exe").write_bytes(b"MZ")
        app.map_pad([])
        window = app.pad_window
        window.attributes("-alpha", 0.0)
        return window

    def test_the_window_offers_map_all_map_sticks_only_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            window = self.open_window(app, paths)
            texts = {str(b.cget("text")) for b in (window.map_all, window.map_sticks, window.reset)}
            self.assertEqual(texts, {"Map all", "Map sticks only", "Reset"})
            for button in (window.map_all, window.map_sticks, window.reset):
                self.assertNotIn("disabled", button.state())  # all three are there to press when it is idle
            window.close()

    def test_map_all_and_map_sticks_only_start_the_mapper_as_the_old_buttons_did(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            window = self.open_window(app, paths)
            mapper = mock.Mock()
            with mock.patch.object(bc, "MapperProcess", return_value=mapper) as process:
                window.map_sticks.invoke()
                self.assertEqual(process.call_args.args[2], ["--sticks"])  # what "Map sticks only" passed before
                self.assertEqual(list(window.rows.get_children()), ["leftx", "lefty", "rightx", "righty"])
                window.handle({"ev": "exit", "code": 2})  # the run ends: the window is as it was opened
                self.assertFalse(window.sticks_only)
                self.assertEqual(str(window.map_all.cget("text")), "Map all")
                self.assertEqual(len(window.rows.get_children()), 21)
                window.map_all.invoke()
                self.assertEqual(process.call_args.args[2], [])  # what "Map controller" passed before
                self.assertEqual(process.call_count, 2)
            window.close()

    def test_a_mapper_that_cannot_start_leaves_the_window_as_it_was(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            window = self.open_window(app, paths)
            with mock.patch.object(bc, "MapperProcess", side_effect=OSError(2, "not found")):
                window.map_sticks.invoke()
            self.assertFalse(window.sticks_only)
            self.assertEqual(str(window.map_all.cget("text")), "Map all")
            self.assertEqual(len(window.rows.get_children()), 21)
            self.assertIn("could not start", window.prompt.cget("text"))
            window.close()

    def test_map_sticks_only_does_nothing_while_a_mapping_runs(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            window = self.open_window(app, paths)
            with mock.patch.object(bc, "MapperProcess", return_value=mock.Mock()) as process:
                window.map_all.invoke()
                window.map_sticks.invoke()
                self.assertEqual(process.call_count, 1)
                self.assertFalse(window.sticks_only)
            window.close()

    def test_reset_in_the_window_asks_first_and_does_what_reset_mapping_did(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            window = self.open_window(app, paths)
            paths.mapping.write_text("mapping", encoding="utf-8")
            with mock.patch.object(bl.messagebox, "askyesno", return_value=False) as ask:
                window.reset.invoke()
                ask.assert_called_once()
            self.assertTrue(paths.mapping.exists())
            with mock.patch.object(bl.messagebox, "askyesno", return_value=True):
                window.reset.invoke()
            self.assertFalse(paths.mapping.exists())
            self.assertEqual(Path(str(paths.mapping) + ".bak").read_text(encoding="utf-8"), "mapping")
            window.close()

    def test_the_old_way_in_still_works(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            paths.bin.mkdir(parents=True, exist_ok=True)
            (paths.bin / "basara4-pad-mapper.exe").write_bytes(b"MZ")
            app.map_pad(["--sticks"])
            window = app.pad_window
            window.attributes("-alpha", 0.0)
            self.assertTrue(window.sticks_only)
            self.assertEqual(str(window.map_all.cget("text")), "Map sticks")
            window.close()


class SettingsStayTheSameTests(unittest.TestCase):
    def test_the_values_behind_the_labels_did_not_change(self):
        self.assertEqual([key for _, key in bl.RESOLUTIONS],
                         ["360p", "480p", "540p", "720p", "900p", "native", "1440p", "1800p", "4K"])
        labels = dict((key, name) for name, key in bl.RESOLUTIONS)
        for key in ("360p", "480p", "540p"):
            self.assertIn("Potato", labels[key])
        self.assertIn(("860 x 480  (480p - Super Potato)", "480p"), bl.RESOLUTIONS)
        self.assertEqual(bl.SYSTEM_GPUS[0][0], -1)  # "Auto" keeps the value -1 that is saved as gpu_index
        self.assertEqual(bl.SYSTEM_GPUS[0][1], "Auto (Prefer discrete GPU)")

    def test_an_old_launcher_json_loads_and_is_saved_with_the_same_values(self):
        old = {"resolution": "480p", "input_mode": "kbm_coop", "gpu_index": -1, "display_mode": "borderless",
               "fps_limit": 90, "anisotropy": 4, "upscale": "fsr1"}
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp, cfg=old) as (app, paths, game, _):
            self.assertEqual(app.res_var.get(), "860 x 480  (480p - Super Potato)")
            self.assertEqual(app.input_mode_var.get(), "kbm_coop")
            app._collect()
            saved = json.loads(paths.config.read_text(encoding="utf-8"))
            for key in ("resolution", "input_mode", "gpu_index", "display_mode", "fps_limit", "anisotropy", "upscale"):
                self.assertEqual(saved[key], old[key], key)

    def test_the_input_mode_labels_are_english_and_not_longer_than_before(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            radios = [w for w in descendants(app) if isinstance(w, ttk.Radiobutton) and str(w.cget("value")) in ("controller", "kbm_coop")]
            self.assertEqual({str(r.cget("value")) for r in radios}, {"controller", "kbm_coop"})
            for radio in radios:
                self.assertIsNone(INDONESIAN.search(str(radio.cget("text"))), radio.cget("text"))
            self.assertLessEqual(max(len(str(r.cget("text"))) for r in radios),
                                 len("🎮 Controller Utama (Stik 1 = P1, Stik 2 = P2)"))


class EnglishOnlyTests(unittest.TestCase):
    def test_no_launcher_module_holds_indonesian_text(self):
        for name in MODULES:
            source = (ROOT / "launcher" / f"{name}.py").read_text(encoding="utf-8")
            for line, text in string_literals(source):
                if text in NOT_TEXT:
                    continue
                self.assertIsNone(INDONESIAN.search(text), f"{name}.py:{line}: {text[:80]!r}")

    def test_the_mapper_prompts_are_english_too(self):
        source = (ROOT / "tools" / "gamepad_mapper.c").read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        source = re.sub(r"//[^\n]*", "", source)
        for number, line in enumerate(source.splitlines(), 1):
            for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                self.assertIsNone(INDONESIAN.search(literal), f"gamepad_mapper.c (code line {number}): {literal[:80]!r}")

    def test_the_keymap_window_is_english(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.messagebox, "showinfo") as info:
            app.open_keymap_ui()
            app.update_idletasks()
            window = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][0]
            texts = [str(w.cget("text")) for w in descendants(window) if "text" in w.keys()] + [window.title()]
            self.assertGreater(len(texts), 30)
            for text in texts:
                self.assertIsNone(INDONESIAN.search(text), text)
            by_text = {str(w.cget("text")): w for w in descendants(window) if isinstance(w, (tk.Button, ttk.Button))}
            for expected in ("🔄 Reset to PC Default", "Cancel", "💾 Save Settings"):
                self.assertIn(expected, by_text)
            by_text["🔄 Reset to PC Default"].invoke()
            by_text["💾 Save Settings"].invoke()
            self.assertEqual(info.call_count, 2)
            for call in info.call_args_list:
                self.assertIsNone(INDONESIAN.search(" ".join(str(a) for a in call.args)), call)

    def test_the_key_action_list_is_english(self):
        for key, label, ps_label in bl.ACTION_DESCRIPTIONS:
            self.assertIsNone(INDONESIAN.search(label), label)
            self.assertIsNone(INDONESIAN.search(ps_label), ps_label)
        self.assertEqual([k for k, _, _ in bl.ACTION_DESCRIPTIONS][:4], ["square", "triangle", "cross", "circle"])

    def test_the_update_texts_and_the_updater_script_are_english(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.subprocess, "Popen"), mock.patch.object(bl.Launcher, "destroy"):
            window = mock.Mock()
            app._apply_update_and_restart(window)
            script = (paths.user / "updater.cmd").read_text(encoding="ascii")
            self.assertIsNone(INDONESIAN.search(script), script)
            self.assertIn("Waiting for the launcher to close", script)
            self.assertIn("Starting the new version", script)

    def test_the_status_after_a_crash_is_english(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "_show_crash_dialog"):
            paths.log.write_text("x\n", encoding="utf-8")
            text = app._check_exit_error(0xC0000005)
            self.assertIn("exit code", text)
            self.assertIsNone(INDONESIAN.search(text), text)


if __name__ == "__main__":
    unittest.main()
