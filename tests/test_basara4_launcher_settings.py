"""Launcher settings files (replaces the upstream test_launcher_save.py and
test_launcher_portable_settings.py): user\\launcher.json and the runtime's user\\basara4.ini, both kept
next to the program (portable), and how _collect writes the window's choices into them.
"""
import json
import tempfile
import types
import unittest
from unittest import mock

from launcher_fixtures import bl, game_folder, launcher_folders, launcher_like


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class ConfigTests(unittest.TestCase):
    def test_defaults_when_there_is_no_or_a_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl, "find_game", return_value=""):
            self.assertEqual(bl.load_config(), dict(bl.DEFAULTS))
            paths.user.mkdir()
            paths.config.write_text("{broken", encoding="utf-8")
            self.assertEqual(bl.load_config(), dict(bl.DEFAULTS))

    def test_save_and_load_round_trip_keeps_every_key(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            game = game_folder(tmp)
            cfg = dict(bl.DEFAULTS, game=str(game), fps_limit=144, resolution="540p", upscale="fsr1",
                       keymap={"cross": "J"}, mouse_sens=150, cheats=["infinite_health"], gpu_index=1)
            bl.save_config(cfg)  # creates user\
            self.assertEqual(json.loads(paths.config.read_text(encoding="utf-8")), cfg)
            self.assertEqual(bl.load_config(), cfg)

    def test_saved_values_override_defaults_and_new_defaults_fill_in(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl, "find_game", return_value=""):
            paths.user.mkdir()
            # An older launcher's file: a BOM, fewer keys.
            paths.config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"fps_limit": 120, "vsync": False}).encode())
            cfg = bl.load_config()
            self.assertEqual((cfg["fps_limit"], cfg["vsync"]), (120, False))
            self.assertEqual(cfg["render_trace"], bl.DEFAULTS["render_trace"])
            self.assertEqual(cfg["input_mode"], "controller")

    def test_a_game_folder_without_eboot_is_replaced_by_the_one_found(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl, "find_game", return_value="found-game") as find_game:
            bl.save_config(dict(bl.DEFAULTS, game=str(paths.root / "gone")))
            self.assertEqual(bl.load_config()["game"], "found-game")
            find_game.assert_called_once()


class IniTests(unittest.TestCase):
    def test_write_ini_changes_only_the_given_keys(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            paths.user.mkdir()
            paths.ini.write_text("# runtime settings\nvsync=1\nshow_fps=1\nfsr1=0\ncheat_very_hard=1\n",
                                 encoding="utf-8")
            bl.write_ini(paths.ini, {"vsync": 0, "fsr1": 1, "render_resolution": "720p"})
            self.assertEqual(paths.ini.read_text(encoding="utf-8").splitlines(),
                             ["# runtime settings", "vsync=0", "show_fps=1", "fsr1=1", "cheat_very_hard=1",
                              "render_resolution=720p"])
            self.assertEqual(bl.read_ini(paths.ini), {"vsync": "0", "show_fps": "1", "fsr1": "1",
                                                      "cheat_very_hard": "1", "render_resolution": "720p"})

    def test_write_ini_creates_the_file_and_read_ini_tolerates_missing_or_odd_lines(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            self.assertEqual(bl.read_ini(paths.ini), {})
            bl.write_ini(paths.ini, {"rcas": 1})  # creates user\ as well
            self.assertEqual(bl.read_ini(paths.ini), {"rcas": "1"})
            paths.ini.write_bytes(b"\xef\xbb\xbf# c\n\nno equals\nkey=a=b\n")
            self.assertEqual(bl.read_ini(paths.ini), {"key": "a=b"})

    def test_settings_made_before_the_rename_are_moved_once(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            paths.user.mkdir()
            paths.old_ini.write_text("vsync=0\n", encoding="utf-8")
            fake = launcher_like()
            self.assertEqual(fake._ini(), paths.ini)
            self.assertFalse(paths.old_ini.exists())
            self.assertEqual(bl.read_ini(paths.ini), {"vsync": "0"})
            paths.old_ini.write_text("vsync=1\n", encoding="utf-8")  # both exist: basara4.ini wins
            self.assertEqual(fake._ini(), paths.ini)
            self.assertEqual(bl.read_ini(paths.ini), {"vsync": "0"})


class CollectTests(unittest.TestCase):
    def window(self, cfg, **choices):
        values = dict(game="", mode="windowed", vsync=False, rcas=True, fps="144", res=bl.RESOLUTIONS[3][0],
                      up=bl.UPSCALERS[1][0], aniso="8x", audio=False, wav=False, render_trace=False,
                      gpu=bl.SYSTEM_GPUS[0][1], input_mode="kbm")
        values.update(choices)
        return launcher_like(cfg, **{f"{name}_var": Var(value) for name, value in values.items()})

    def test_collect_saves_the_window_choices_to_both_files(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            paths.user.mkdir()
            paths.ini.write_text("show_fps=1\n", encoding="utf-8")
            fake = self.window({"keymap": {"cross": "J"}})
            bl.Launcher._collect(fake)
            saved = json.loads(paths.config.read_text(encoding="utf-8"))
            self.assertEqual({k: saved[k] for k in ("display_mode", "vsync", "rcas", "fps_limit", "resolution",
                                                     "upscale", "anisotropy", "input_mode", "keymap")},
                             {"display_mode": "windowed", "vsync": False, "rcas": True, "fps_limit": 144,
                              "resolution": "720p", "upscale": "fsr1", "anisotropy": 8, "input_mode": "kbm",
                              "keymap": {"cross": "J"}})
            ini = bl.read_ini(paths.ini)
            self.assertEqual({k: ini[k] for k in ("display_mode", "vsync", "fps_limit", "rcas", "anisotropy",
                                                  "render_resolution", "fsr1", "upscaler", "show_fps")},
                             {"display_mode": "windowed", "vsync": "0", "fps_limit": "144", "rcas": "1",
                              "anisotropy": "8", "render_resolution": "720p", "fsr1": "1", "upscaler": "off",
                              "show_fps": "1"})

    def test_wav_capture_is_saved_only_with_the_audio_log(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            bl.Launcher._collect(self.window({}, audio=False, wav=True))
            self.assertFalse(json.loads(paths.config.read_text(encoding="utf-8"))["audio_wav"])
            bl.Launcher._collect(self.window({}, audio=True, wav=True))
            self.assertTrue(json.loads(paths.config.read_text(encoding="utf-8"))["audio_wav"])


if __name__ == "__main__":
    unittest.main()
