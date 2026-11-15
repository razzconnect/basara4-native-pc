"""Resolution patches of launcher/basara4_patches.py (replaces the upstream test_patches.py).

The image is synthetic: each code pattern the patcher looks for is placed at a known offset between
int3 padding (wildcard bytes "??" filled with nops). No eboot or other game file is read.
"""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))
import basara4_patches as bp  # noqa: E402

SITES = {
    "RENDER_PAT_1": 0x100, "RENDER_PAT_2": 0x200, "RENDER_PAT_3": 0x300, "RENDER_PAT_4": 0x400,
    "RENDER_PAT_5": 0x500, "GUI_SCALE_CLAMP_PAT": 0x600, "HUD_PROJ_CLAMP_PAT": 0x700,
    "RTX_MIP_LOOP_PAT": 0x800, "ICON_SCALE_W_PAT": 0x900, "ICON_SCALE_H_PAT": 0xa00, "GARLIC_PAT": 0xb00,
}


def pattern_bytes(text):
    return bytes(0x90 if b == "??" else int(b, 16) for b in text.split())


def synthetic_image(extra=()):
    image = bytearray(b"\xcc" * 0x1000)
    for name, offset in list(SITES.items()) + list(extra):
        code = pattern_bytes(getattr(bp, name))
        image[offset:offset + len(code)] = code
    return bytes(image)


def apply(image, writes):
    out = bytearray(image)
    for offset, data in writes:
        out[offset:offset + len(data)] = data
    return bytes(out)


def synthetic_elf(image):
    """A minimal ELF64 with one PT_LOAD holding `image` at virtual address 0."""
    header = bytearray(0x40)
    header[:4] = b"\x7fELF"
    struct.pack_into("<Q", header, 0x20, 0x40)          # e_phoff
    struct.pack_into("<HH", header, 0x36, 56, 1)        # e_phentsize, e_phnum
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0x40 + 56, 0, 0, len(image), len(image), 0x1000)
    return bytes(header) + phdr + image


class ResolutionPatchTests(unittest.TestCase):
    def test_native_and_unknown_resolutions_patch_nothing(self):
        image = synthetic_image()
        for name in ("native", "1080p", "", "8K", None):
            self.assertEqual(bp.resolution_patches(image, name), [], name)

    def test_render_size_is_written_into_every_render_site(self):
        image = synthetic_image()
        for name, (w, h) in bp.RESOLUTION_SIZES.items():
            patched = apply(image, bp.resolution_patches(image, name))
            p1, p4, p5 = SITES["RENDER_PAT_1"], SITES["RENDER_PAT_4"], SITES["RENDER_PAT_5"]
            self.assertEqual(struct.unpack_from("<I", patched, p1 + 7)[0], w, name)
            self.assertEqual(struct.unpack_from("<I", patched, p1 + 18)[0], h, name)
            for site in ("RENDER_PAT_2", "RENDER_PAT_3"):   # mov rax, (H << 32) | W
                self.assertEqual(struct.unpack_from("<II", patched, SITES[site] + 2), (w, h), (name, site))
            for site in (p4, p5):
                self.assertEqual(struct.unpack_from("<I", patched, site + 6)[0], w, name)
                self.assertEqual(struct.unpack_from("<I", patched, site + 16)[0], h, name)
            # The instructions around the immediates are untouched.
            self.assertEqual(patched[p1:p1 + 3], bytes.fromhex("41c785"), name)
            self.assertEqual(patched[SITES["RENDER_PAT_2"]:SITES["RENDER_PAT_2"] + 2], b"\x48\xb8", name)

    def test_gui_scale_and_3d_projection_keep_the_1080p_layout(self):
        # 13284dd: the GUI clamp is replaced by proportional scaling; 90bdc0b: the 3D-to-2D
        # projection stays in 1920x1080 design space (floating HP bars) at every resolution.
        image = synthetic_image()
        for name in bp.RESOLUTION_SIZES:
            patched = apply(image, bp.resolution_patches(image, name))
            gui, hud = SITES["GUI_SCALE_CLAMP_PAT"], SITES["HUD_PROJ_CLAMP_PAT"]
            self.assertEqual(patched[gui:gui + len(bp.GUI_SCALE_UNCLAMP)], bp.GUI_SCALE_UNCLAMP, name)
            self.assertEqual(patched[hud + 8:hud + 12], b"\xc5\xf8\x28\xcc", name)
            self.assertEqual(patched[hud + 20:hud + 24], b"\xc5\xf8\x28\xdc", name)

    def test_rtx_mip_guard_only_below_960x540(self):
        # 94ba6d1: below 960x540 the movie render-target mip loop stops before mips < 1 (white movie).
        image = synthetic_image()
        rtx = SITES["RTX_MIP_LOOP_PAT"]
        guard = [(rtx + 2, b"\xf6\x45\xd8\x3e\x74\x55\x90\x90\x90\x90"), (rtx + 0x50, b"\x77\xb0"),
                 (rtx + 0x5b, b"\x77\xa5")]
        for name, (w, h) in bp.RESOLUTION_SIZES.items():
            writes = bp.resolution_patches(image, name)
            touches_rtx = [w_ for w_ in writes if rtx <= w_[0] < rtx + 0x70]
            if w < 960 or h < 540:
                self.assertEqual(touches_rtx, guard, name)
            else:
                self.assertEqual(touches_rtx, [], name)
        self.assertIn("360p", [n for n, (w, h) in bp.RESOLUTION_SIZES.items() if w < 960])
        self.assertEqual(bp.RESOLUTION_SIZES["540p"], (960, 540))  # the boundary itself is not guarded

    def test_icon_scale_and_garlic_heap_only_above_1080p(self):
        image = synthetic_image()
        icon_w, icon_h, garlic = SITES["ICON_SCALE_W_PAT"], SITES["ICON_SCALE_H_PAT"], SITES["GARLIC_PAT"]
        for name, (w, h) in bp.RESOLUTION_SIZES.items():
            writes = dict(bp.resolution_patches(image, name))
            above = w > 1920 or h > 1080
            self.assertEqual(writes.get(icon_w + 6), b"\xeb" if above else None, name)
            self.assertEqual(writes.get(icon_h + 5), b"\xeb" if above else None, name)
            self.assertEqual(writes.get(garlic + 6), struct.pack("<I", 0xE0000000) if above else None, name)

    def test_every_matching_site_is_patched(self):
        image = synthetic_image(extra=[("RENDER_PAT_2", 0xc00)])
        writes = bp.resolution_patches(image, "720p")
        self.assertIn((SITES["RENDER_PAT_2"] + 2, struct.pack("<II", 1280, 720)), writes)
        self.assertIn((0xc00 + 2, struct.pack("<II", 1280, 720)), writes)

    def test_image_without_the_code_gets_no_writes(self):
        self.assertEqual(bp.resolution_patches(b"\xcc" * 0x1000, "4K"), [])


class PatchFileTests(unittest.TestCase):
    def test_build_writes_resolution_patches_to_patches_bin(self):
        image = synthetic_image()
        with tempfile.TemporaryDirectory() as tmp:
            elf, out = Path(tmp) / "eboot.elf", Path(tmp) / "patches.bin"
            elf.write_bytes(synthetic_elf(image))
            writes, notes = bp.build(elf, out, False, resolution="720p")
            blob = out.read_bytes()
            self.assertEqual(writes, bp.resolution_patches(image, "720p"))
            self.assertEqual(notes, ["resolution 720p"])
            magic, base, count = struct.unpack_from("<8sQQ", blob, 0)
            self.assertEqual((magic, base, count), (b"G3PATCH2", bp.EBOOT_BASE, len(writes)))
            at, records = 24, []
            for _ in range(count):
                offset, length = struct.unpack_from("<QQ", blob, at)
                records.append((offset, blob[at + 16:at + 16 + length]))
                at += 16 + length
            self.assertEqual((records, at), (writes, len(blob)))

            writes, notes = bp.build(elf, out, False, resolution="native")
            self.assertEqual((writes, notes), ([], []))
            self.assertEqual(out.read_bytes(), struct.pack("<8sQQ", b"G3PATCH2", bp.EBOOT_BASE, 0))


def vex_scalar(opcode, dest, src1, src2):
    """4-byte VEX2 vminss (0x5d) / vmaxss (0x5f) dest, src1, src2 between xmm registers."""
    byte1 = (((~dest >> 3) & 1) << 7) | ((~src1 & 0xf) << 3) | 0x02
    return bytes([0xc5, byte1, opcode, 0xc0 | (dest & 7) << 3 | (src2 & 7)])


# Synthetic stand-in for the six game instructions after GUI_SCALE_CLAMP_PAT (slots 2-7): clamps of
# x, y and min(x, y) against the threshold in xmm3. The real ones are only read from eboot.elf.
SYNTHETIC_SLOTS = (vex_scalar(0x5f, 9, 3, 0) + vex_scalar(0x5f, 10, 3, 1) + vex_scalar(0x5d, 11, 3, 0) +
                   vex_scalar(0x5d, 12, 3, 1) + vex_scalar(0x5f, 7, 3, 2) + vex_scalar(0x5d, 3, 3, 2))


def image_with_full_gui_block(slots=SYNTHETIC_SLOTS):
    image = bytearray(synthetic_image())
    gui = SITES["GUI_SCALE_CLAMP_PAT"] + len(pattern_bytes(bp.GUI_SCALE_CLAMP_PAT))
    image[gui:gui + len(slots)] = slots
    return bytes(image)


class GuiScaleSlotTests(unittest.TestCase):
    """SPARTAN_GUI_SCALE_SLOTS: which cGUI scale registers the resolution patch unclamps."""

    def block(self, image, writes):
        gui = SITES["GUI_SCALE_CLAMP_PAT"]
        return apply(image, writes)[gui:gui + bp.GUI_SCALE_BLOCK_LEN]

    def test_layout_matches_the_known_game_bytes(self):
        # The two instructions inside GUI_SCALE_CLAMP_PAT are slots 0 and 1, and the generator used
        # by these tests encodes them byte for byte.
        code = pattern_bytes(bp.GUI_SCALE_CLAMP_PAT)
        self.assertEqual(bp._scalar_minmax_dest(code[8:12]), 8)
        self.assertEqual(bp._scalar_minmax_dest(code[12:16]), 5)
        self.assertEqual(vex_scalar(0x5d, 8, 3, 4) + vex_scalar(0x5f, 5, 3, 4), code[8:16])
        self.assertEqual(bp.GUI_SCALE_BLOCK_LEN, len(bp.GUI_SCALE_UNCLAMP))

    def test_default_is_the_release_patch(self):
        # Unset or "all": exactly the bytes v1.0.8 writes, with or without the full block in the image.
        self.assertEqual(bp.gui_scale_slots(None), bp.GUI_SCALE_ALL_SLOTS)
        for image in (synthetic_image(), image_with_full_gui_block()):
            for name in bp.RESOLUTION_SIZES:
                self.assertEqual(bp.resolution_patches(image, name, bp.gui_scale_slots("all")),
                                 bp.resolution_patches(image, name), name)
                self.assertEqual(self.block(image, bp.resolution_patches(image, name)),
                                 bp.GUI_SCALE_UNCLAMP, name)

    def test_none_keeps_the_game_code(self):
        image = image_with_full_gui_block()
        writes = bp.resolution_patches(image, "720p", bp.gui_scale_slots("none"))
        gui = SITES["GUI_SCALE_CLAMP_PAT"]
        self.assertEqual([w for w in writes if gui <= w[0] < gui + bp.GUI_SCALE_BLOCK_LEN], [])
        # Every other resolution patch is unchanged.
        others = [w for w in bp.resolution_patches(image, "720p") if w[0] != gui]
        self.assertEqual(writes, others)

    def test_each_slot_alone(self):
        image = image_with_full_gui_block()
        gui = SITES["GUI_SCALE_CLAMP_PAT"]
        original = image[gui:gui + bp.GUI_SCALE_BLOCK_LEN]
        for i in range(len(bp.GUI_SCALE_SLOT_REGS)):
            block = self.block(image, bp.resolution_patches(image, "1440p", frozenset({i})))
            self.assertEqual(block[:bp.GUI_SCALE_LOAD_LEN], original[:bp.GUI_SCALE_LOAD_LEN])
            for j in range(len(bp.GUI_SCALE_SLOT_REGS)):
                at = bp.GUI_SCALE_LOAD_LEN + 4 * j
                want = bp.GUI_SCALE_UNCLAMP[4 * j:4 * j + 4] if j == i else original[at:at + 4]
                self.assertEqual(block[at:at + 4], want, (i, j))

    def test_unexpected_game_code_fails_the_variant_only(self):
        broken = bytearray(SYNTHETIC_SLOTS)
        broken[8:12] = vex_scalar(0x5d, 13, 3, 0)  # slot 4 writes xmm13, not xmm11
        image = image_with_full_gui_block(bytes(broken))
        with self.assertRaisesRegex(ValueError, "slot 4"):
            bp.resolution_patches(image, "720p", frozenset({1}))
        with self.assertRaises(ValueError):
            bp.resolution_patches(synthetic_image(), "720p", frozenset())  # block not in the image
        self.assertEqual(self.block(image, bp.resolution_patches(image, "720p")), bp.GUI_SCALE_UNCLAMP)

    def test_slot_values(self):
        self.assertEqual(bp.gui_scale_slots(" ALL "), bp.GUI_SCALE_ALL_SLOTS)
        self.assertEqual(bp.gui_scale_slots(""), bp.GUI_SCALE_ALL_SLOTS)
        self.assertEqual(bp.gui_scale_slots("None"), frozenset())
        self.assertEqual(bp.gui_scale_slots("1, 6,7"), frozenset({1, 6, 7}))
        for bad in ("8", "-1", "x", "1,,2", ","):
            with self.assertRaises(ValueError, msg=bad):
                bp.gui_scale_slots(bad)

    def test_build_reads_the_environment(self):
        image = image_with_full_gui_block()
        with tempfile.TemporaryDirectory() as tmp:
            elf, out = Path(tmp) / "eboot.elf", Path(tmp) / "patches.bin"
            elf.write_bytes(synthetic_elf(image))
            with mock.patch.dict(os.environ, {bp.GUI_SCALE_SLOTS_ENV: "1"}):
                writes, notes = bp.build(elf, out, False, resolution="720p")
            self.assertEqual(writes, bp.resolution_patches(image, "720p", frozenset({1})))
            self.assertEqual(notes, ["resolution 720p", "gui scale slots [1]"])
            with mock.patch.dict(os.environ, {bp.GUI_SCALE_SLOTS_ENV: "9"}):
                with self.assertRaises(ValueError):
                    bp.build(elf, out, False, resolution="720p")
            with mock.patch.dict(os.environ, {bp.GUI_SCALE_SLOTS_ENV: "none"}):
                self.assertEqual(bp.build(elf, out, False, resolution="native"), ([], []))
            env = {k: v for k, v in os.environ.items() if k != bp.GUI_SCALE_SLOTS_ENV}
            with mock.patch.dict(os.environ, env, clear=True):
                writes, notes = bp.build(elf, out, False, resolution="720p")
            self.assertEqual((writes, notes), (bp.resolution_patches(image, "720p"), ["resolution 720p"]))


# Every resolution the launcher offers besides native; a preset added or removed must update this.
PRESETS = ("360p", "480p", "540p", "720p", "900p", "1440p", "1800p", "4K")


def hud_writes():
    hud = SITES["HUD_PROJ_CLAMP_PAT"]
    return [(hud + 8, b"\xc5\xf8\x28\xcc"), (hud + 20, b"\xc5\xf8\x28\xdc")]


def env_without(*names):
    return {k: v for k, v in os.environ.items() if k not in names}


class HudProjectionSwitchTests(unittest.TestCase):
    """SPARTAN_HUD_PROJ_PATCH: the 3D-to-2D projection patch on (release) or off, at every preset."""

    def test_presets_are_the_launcher_resolutions(self):
        self.assertEqual(tuple(bp.RESOLUTION_SIZES), PRESETS)

    def test_on_is_the_release_patch_at_every_preset(self):
        for image in (synthetic_image(), image_with_full_gui_block()):
            for name in PRESETS + ("native", "bogus"):
                with self.subTest(name=name):
                    on = bp.resolution_patches(image, name, hud_proj=bp.hud_proj_patch("on"))
                    self.assertEqual(on, bp.resolution_patches(image, name))
                    if name in PRESETS:
                        self.assertEqual([w for w in on if w in hud_writes()], hud_writes())

    def test_off_leaves_only_the_projection_code_unpatched_at_every_preset(self):
        image = image_with_full_gui_block()
        hud = SITES["HUD_PROJ_CLAMP_PAT"]
        original = image[hud:hud + len(pattern_bytes(bp.HUD_PROJ_CLAMP_PAT))]
        for name in PRESETS:
            with self.subTest(name=name):
                on = bp.resolution_patches(image, name)
                off = bp.resolution_patches(image, name, hud_proj=False)
                self.assertEqual(off, [w for w in on if w not in hud_writes()])
                patched = apply(image, off)
                self.assertEqual(patched[hud:hud + len(original)], original)
                # Every other release patch is still there, the cGUI scale code included.
                gui = SITES["GUI_SCALE_CLAMP_PAT"]
                self.assertEqual(patched[gui:gui + bp.GUI_SCALE_BLOCK_LEN], bp.GUI_SCALE_UNCLAMP)
                self.assertEqual(len(off), len(on) - 2)

    def test_off_combines_with_gui_scale_slots(self):
        image = image_with_full_gui_block()
        for name in PRESETS:
            with self.subTest(name=name):
                both = bp.resolution_patches(image, name, frozenset(), hud_proj=False)
                gui_only = bp.resolution_patches(image, name, frozenset())
                self.assertEqual(both, [w for w in gui_only if w not in hud_writes()])

    def test_values(self):
        for value, want in ((None, True), ("", True), ("on", True), (" ON ", True), ("off", False),
                            ("Off", False)):
            self.assertIs(bp.hud_proj_patch(value), want, value)
        for bad in ("0", "no", "of", "on,off"):
            with self.assertRaises(ValueError, msg=bad):
                bp.hud_proj_patch(bad)

    def test_build_reads_the_environment_at_every_preset(self):
        image = image_with_full_gui_block()
        with tempfile.TemporaryDirectory() as tmp:
            elf, out = Path(tmp) / "eboot.elf", Path(tmp) / "patches.bin"
            elf.write_bytes(synthetic_elf(image))
            base = env_without(bp.HUD_PROJ_ENV, bp.GUI_SCALE_SLOTS_ENV)
            for name in PRESETS:
                with self.subTest(name=name):
                    with mock.patch.dict(os.environ, {**base, bp.HUD_PROJ_ENV: "off"}, clear=True):
                        writes, notes = bp.build(elf, out, False, resolution=name)
                    self.assertEqual(writes, bp.resolution_patches(image, name, hud_proj=False))
                    self.assertEqual(notes, [f"resolution {name}", "hud projection patch off"])
                    with mock.patch.dict(os.environ, base, clear=True):
                        writes, notes = bp.build(elf, out, False, resolution=name)
                    self.assertEqual((writes, notes), (bp.resolution_patches(image, name), [f"resolution {name}"]))
            with mock.patch.dict(os.environ, {**base, bp.HUD_PROJ_ENV: "maybe"}, clear=True):
                with self.assertRaises(ValueError):
                    bp.build(elf, out, False, resolution="720p")
            with mock.patch.dict(os.environ, {**base, bp.HUD_PROJ_ENV: "off"}, clear=True):
                self.assertEqual(bp.build(elf, out, False, resolution="native"), ([], []))


if __name__ == "__main__":
    unittest.main()
