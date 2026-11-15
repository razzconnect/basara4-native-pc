"""Game folder and first-start preparation (replaces the upstream test_launcher_game_files.py):
find_game, image_ready and prepare_image in launcher/basara4_launcher.py.

The eboot.bin here is synthetic bytes and the preparation scripts are mocked: no game file is read.
"""
import json
import tempfile
import types
import unittest
from unittest import mock

from launcher_fixtures import bl, game_folder, launcher_folders

IMAGE_FILES = ("boot-linked.bin", "content.bin", "eboot.elf")


def prepared(paths, game):
    paths.out.mkdir(parents=True, exist_ok=True)
    for name in IMAGE_FILES:
        (paths.out / name).write_bytes(b"image")
    paths.stamp.write_text(json.dumps({"eboot_sha256": bl.file_sha256(game / "eboot.bin"), "game": str(game)}),
                           encoding="utf-8")


class FindGameTests(unittest.TestCase):
    def test_game_folder_or_its_first_subfolder_with_eboot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bl, "ROOT", bl.Path(tmp)):
            self.assertEqual(bl.find_game(), "")
            (bl.Path(tmp) / "game" / "b").mkdir(parents=True)
            (bl.Path(tmp) / "game" / "a").mkdir()
            (bl.Path(tmp) / "game" / "b" / "eboot.bin").write_bytes(b"x")
            self.assertEqual(bl.find_game(), str(bl.Path(tmp) / "game" / "b"))
            (bl.Path(tmp) / "game" / "a" / "eboot.bin").write_bytes(b"x")
            self.assertEqual(bl.find_game(), str(bl.Path(tmp) / "game" / "a"))  # sorted
            (bl.Path(tmp) / "game" / "eboot.bin").write_bytes(b"x")
            self.assertEqual(bl.find_game(), str(bl.Path(tmp) / "game"))


class ImageReadyTests(unittest.TestCase):
    def test_ready_only_for_this_eboot_and_a_complete_image(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            game = game_folder(tmp)
            self.assertFalse(bl.image_ready(game))  # nothing prepared
            prepared(paths, game)
            self.assertTrue(bl.image_ready(game))
            (game / "eboot.bin").write_bytes(b"an updated eboot")  # eboot.bin changed: prepare again
            self.assertFalse(bl.image_ready(game))
            prepared(paths, game)
            (paths.out / "content.bin").unlink()
            self.assertFalse(bl.image_ready(game))
            prepared(paths, game)
            paths.stamp.write_text("{broken", encoding="utf-8")
            self.assertFalse(bl.image_ready(game))
            prepared(paths, game)
            (game / "eboot.bin").unlink()
            self.assertFalse(bl.image_ready(game))


class PrepareImageTests(unittest.TestCase):
    def test_runs_the_pipeline_in_order_and_stamps_the_eboot(self):
        progress = []
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            game = game_folder(tmp)
            self.assertIsNone(bl.prepare_image(game, progress.append))
            stamp = json.loads(paths.stamp.read_text(encoding="utf-8"))
            self.assertEqual(stamp, {"eboot_sha256": bl.file_sha256(game / "eboot.bin"), "game": str(game)})
            self.assertTrue(paths.prepare_log.is_file())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([c[1].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for c in commands],
                         [name for name, _ in bl.PIPELINE])
        for command, (name, extra) in zip(commands, bl.PIPELINE):
            self.assertEqual(command[2:], [str(game), "--out", str(paths.out)] + extra, name)
        self.assertEqual(len(progress), len(bl.PIPELINE))
        self.assertTrue(progress[0].startswith(f"Preparing the game image (1/{len(bl.PIPELINE)})"))
        self.assertEqual(bl.PIPELINE[-1][0], "isa_rewrite.py")  # 9a717af: last, and never fatal

    def test_a_failing_script_stops_with_its_name_and_no_stamp(self):
        results = [types.SimpleNamespace(returncode=0), types.SimpleNamespace(returncode=3)]
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl.subprocess, "run", side_effect=results) as run:
            game = game_folder(tmp)
            error = bl.prepare_image(game, lambda text: None)
            self.assertFalse(paths.stamp.exists())
        failed = bl.PIPELINE[1][0]
        self.assertEqual(error, f"{failed} failed (exit 3). See user\\prepare.log")
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
