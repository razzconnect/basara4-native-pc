import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))

from basara4_cheats import (
    AUTHOR, BASE_ADDRESS, CHEATS, SOURCE_URL, compile_cheats, game_version,
    normalize_selection, validate_selection,
)
import basara4_patches

LOAD_SEGMENTS = ((0, 0x12BD788, 5), (0x12C0000, 0x27DE50, 6))


def image_with_cheat_bytes(*cheat_ids):
    writes = [write for cheat_id in cheat_ids for write in CHEATS[cheat_id]["writes"]]
    image = bytearray(
        [0xCC] * max(0x13FD4A8, max(offset for offset, _on, _off in writes) + 1))
    for offset, _on, original in writes:
        target = offset - BASE_ADDRESS if offset == 0x16BF800 or offset == 0x929A91 else offset
        expected = bytes.fromhex(original)
        image[target:target + len(expected)] = expected
    return bytes(image)


def matching_offset(image, source_offset, original):
    expected = bytes.fromhex(original)
    candidates = {source_offset}
    if source_offset >= BASE_ADDRESS:
        candidates.add(source_offset - BASE_ADDRESS)
    return next(
        offset for offset in candidates
        if offset + len(expected) <= len(image)
        and image[offset:offset + len(expected)] == expected)


def build_sfo(values):
    entries, keys, data = [], bytearray(), bytearray()
    for key, value in values.items():
        key_at = len(keys)
        keys.extend(key.encode("utf-8") + b"\0")
        encoded = value.encode("utf-8") + b"\0"
        entries.append((key_at, len(data), encoded))
        data.extend(encoded)
    key_offset = 20 + len(entries) * 16
    data_offset = key_offset + len(keys)
    header = struct.pack("<4sIIII", b"\0PSF", 0x101, key_offset, data_offset, len(entries))
    index = b"".join(struct.pack("<HHIII", key_at, 0x0204, len(encoded), len(encoded), value_at)
                     for key_at, value_at, encoded in entries)
    return header + index + keys + data


class Basara4CheatTests(unittest.TestCase):
    def test_goldhen_credit_and_offsets_are_preserved(self):
        self.assertEqual(AUTHOR, "Q8Despair")
        self.assertIn("GoldHEN_Cheat_Repository", SOURCE_URL)
        image = image_with_cheat_bytes("gold")
        writes = compile_cheats(["gold"], "01.02", image, LOAD_SEGMENTS)
        self.assertEqual(len(writes), 2)
        hook = next(data for offset, data in writes if offset == 0x529A91)
        cave = next((offset, data) for offset, data in writes if offset != 0x529A91)
        displacement, = struct.unpack("<i", hook[1:5])
        self.assertEqual(0x529A91 + 5 + displacement, cave[0])

    def test_all_cheats_are_relocated_to_distinct_trampolines(self):
        selected = list(CHEATS)
        image = image_with_cheat_bytes(*selected)
        writes = compile_cheats(selected, "01.02", image, LOAD_SEGMENTS)
        self.assertEqual(len(writes), len(selected) * 2)

        caves = {offset: data for offset, data in writes if 0x12BE000 <= offset < 0x12C0000}
        self.assertEqual(len(caves), len(selected))
        self.assertEqual(len(set(caves)), len(selected))
        self.assertTrue(all(offset + len(data) <= 0x12C0000 for offset, data in caves.items()))

        for cheat_id, cheat in CHEATS.items():
            hook_offset, _hook_bytes, hook_original = cheat["writes"][1]
            source_hook = matching_offset(image, hook_offset, hook_original)
            patched_hook = dict(writes)[source_hook]
            displacement, = struct.unpack("<i", patched_hook[1:5])
            target = source_hook + 5 + displacement
            self.assertIn(target, caves)

            source_cave = matching_offset(image, cheat["writes"][0][0], cheat["writes"][0][2])
            original_cave = bytes.fromhex(cheat["writes"][0][1])
            old_displacement, = struct.unpack("<i", original_cave[-4:])
            expected_return = source_cave + len(original_cave) + old_displacement
            relocated_displacement, = struct.unpack("<i", caves[target][-4:])
            self.assertEqual(target + len(caves[target]) + relocated_displacement, expected_return)

    def test_invalid_selection_and_update_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown cheat"):
            validate_selection(["not_a_cheat"])
        with self.assertRaisesRegex(ValueError, "update 01.02"):
            compile_cheats(["gold"], "01.00", b"", LOAD_SEGMENTS)
        self.assertEqual(normalize_selection(list(CHEATS)), list(CHEATS))

    def test_game_version_reads_and_checks_title_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            sfo = Path(directory) / "sce_sys" / "param.sfo"
            sfo.parent.mkdir()
            sfo.write_bytes(build_sfo({"TITLE_ID": "CUSA01159", "APP_VER": "01.02"}))
            self.assertEqual(game_version(directory), "01.02")
            sfo.write_bytes(build_sfo({"TITLE_ID": "CUSA00000", "APP_VER": "01.02"}))
            with self.assertRaisesRegex(ValueError, "CUSA01159"):
                game_version(directory)

    def test_patch_builder_writes_cheats_to_the_loader_format(self):
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "eboot.elf"
            output = Path(directory) / "patches.bin"
            elf.write_bytes(b"synthetic ELF")
            with mock.patch.object(basara4_patches, "eboot_image",
                                   return_value=image_with_cheat_bytes("special_coin")), \
                    mock.patch.object(basara4_patches, "eboot_load_segments",
                                      return_value=LOAD_SEGMENTS):
                writes, notes = basara4_patches.build(
                    elf, output, False, cheats=["special_coin"], game_version="01.02")
            self.assertEqual(len(writes), 2)
            self.assertEqual(notes, ["1 cheat(s)"])
            blob = output.read_bytes()
            magic, base, count = struct.unpack_from("<8sQQ", blob)
            self.assertEqual((magic, base, count), (b"G3PATCH2", BASE_ADDRESS, 2))
            cursor = struct.calcsize("<8sQQ")
            entries = []
            for _ in range(count):
                offset, length = struct.unpack_from("<QQ", blob, cursor)
                cursor += 16
                entries.append((offset, blob[cursor:cursor + length]))
                cursor += length
            self.assertEqual(entries, writes)

    def test_patch_builder_keeps_existing_fps_patch_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "eboot.elf"
            output = Path(directory) / "patches.bin"
            elf.write_bytes(b"synthetic ELF")
            fps_writes = [(0x401000, b"\xeb\x20")]
            with mock.patch.object(basara4_patches, "eboot_image", return_value=image_with_cheat_bytes("gold")), \
                    mock.patch.object(basara4_patches, "unlimited_fps", return_value=fps_writes):
                writes, notes = basara4_patches.build(elf, output, True)
            self.assertEqual(writes, fps_writes)
            self.assertEqual(notes, ["unlimited fps"])

    def test_patch_builder_combines_fps_and_cheat_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "eboot.elf"
            output = Path(directory) / "patches.bin"
            elf.write_bytes(b"synthetic ELF")
            fps_writes = [(0x401000, b"\xeb\x20")]
            with mock.patch.object(basara4_patches, "eboot_image", return_value=image_with_cheat_bytes("gold")), \
                    mock.patch.object(basara4_patches, "eboot_load_segments", return_value=LOAD_SEGMENTS), \
                    mock.patch.object(basara4_patches, "unlimited_fps", return_value=fps_writes):
                writes, notes = basara4_patches.build(
                    elf, output, True, cheats=["gold"], game_version="01.02")
            self.assertEqual(len(writes), 3)
            self.assertEqual(notes, ["unlimited fps", "1 cheat(s)"])


if __name__ == "__main__":
    unittest.main()
