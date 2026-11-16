"""Resolution patches of launcher/basara4_patches.py (replaces the upstream test_patches.py).

The image is synthetic: each code pattern the patcher looks for is placed at a known offset between
int3 padding (wildcard bytes "??" filled with nops). No eboot or other game file is read.
"""
import math
import random
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

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


# Every resolution the launcher offers besides native; a preset added or removed must update this.
PRESETS = ("360p", "480p", "540p", "720p", "900p", "1440p", "1800p", "4K")

# Synthetic layout for the 2D effect position code: the site, uBasaraGUI's scale code it reads, the
# 3.0 the site divides by, the 1280.0/720.0 pair and the viewport pointer cell (in .bss in the game).
E2D_SITE, UBGUI_SITE, CONST_THREE, CONST_SCALE, VIEWPORT_CELL = 0x1100, 0x1200, 0x1300, 0x1310, 0x1320
# Where the game keeps the screen rect (x, y, W, H as int32) in the viewport object: uBasaraGUI's scale
# code reads [viewport+0D30h] (checked against the game's eboot). Kept here as a literal so the test
# does not follow a wrong offset in the patcher.
SCREEN_RECT = 0xD30


def e2d_site_code(site):
    code = bytearray(pattern_bytes(bp.E2D_NODE_POS_PAT))
    struct.pack_into("<i", code, 18, CONST_THREE - (site + 22))
    return bytes(code)


def ubgui_scale_code(site):
    code = bytearray(pattern_bytes(bp.UBGUI_SCALE_PAT))
    struct.pack_into("<i", code, 3, VIEWPORT_CELL - (site + 7))
    struct.pack_into("<I", code, 14, SCREEN_RECT)  # vcvtdq2ps xmm0,[rax+0D30h]
    struct.pack_into("<i", code, 26, CONST_SCALE - (site + 30))
    return bytes(code)


def image_with_e2d(sites=(E2D_SITE,), scale_sites=(UBGUI_SITE,), three=3.0, scale=(1280.0, 720.0),
                   after=0x5b):
    image = bytearray(synthetic_image()) + bytearray(b"\xcc" * 0x1000)
    for site in sites:
        image[site:site + bp.E2D_NODE_POS_LEN] = e2d_site_code(site)
        image[site + bp.E2D_NODE_POS_LEN] = after
    for site in scale_sites:
        code = ubgui_scale_code(site)
        image[site:site + len(code)] = code
    struct.pack_into("<f", image, CONST_THREE, three)
    struct.pack_into("<ff", image, CONST_SCALE, *scale)
    return bytes(image)


def f32(value):
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:  # the CPU rounds a too large single-precision result to infinity
        return math.copysign(math.inf, value)


def f32_div(a, b):
    if b == 0:
        return float("nan") if a == 0 else math.copysign(math.inf, a) * math.copysign(1.0, b)
    return f32(a / b)


def f32_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


class E2DMachine:
    """Runs the 56 bytes at the effect position site: the game's own code or the patch. Knows only
    the instructions either of them uses; anything else fails the test. Single-precision results are
    rounded exactly like the CPU (double rounding is exact for one add or divide)."""

    VIEWPORT, NODE, EFFECT = 0x100000, 0x200000, 0x300000

    def __init__(self, image, screen, node_xy):
        self.mem = bytearray(image) + bytearray(0x301000 - len(image))
        struct.pack_into("<Q", self.mem, VIEWPORT_CELL, self.VIEWPORT)
        struct.pack_into("<4i", self.mem, self.VIEWPORT + SCREEN_RECT, 0, 0, *screen)
        struct.pack_into("<ff", self.mem, self.NODE + 0x40, *node_xy)
        self.rax, self.rcx, self.rbx, self.r14 = 0x1111, 0x2222, self.NODE, self.EFFECT
        self.xmm = {0: [0.0] * 4, 1: [0.0] * 4, 2: [0.0] * 4}

    def f(self, at):
        return struct.unpack_from("<f", self.mem, at)[0]

    def run(self, site):
        ip, end = site, site + bp.E2D_NODE_POS_LEN
        x = self.xmm
        while ip < end:
            b = bytes(self.mem[ip:ip + 9])
            d32 = struct.unpack_from("<i", b, 4)[0] if len(b) >= 8 else 0
            if b[:3] == b"\x48\x8d\x05":                       # lea rax,[rip+d]
                self.rax, ip = ip + 7 + struct.unpack_from("<i", b, 3)[0], ip + 7
            elif b[:3] == b"\x48\x8b\x00":                     # mov rax,[rax]
                self.rax, ip = struct.unpack_from("<Q", self.mem, self.rax)[0], ip + 3
            elif b[:4] == b"\xc5\xf8\x5b\x90":                 # vcvtdq2ps xmm2,[rax+d]
                ints = struct.unpack_from("<4i", self.mem, self.rax + d32)
                x[2], ip = [f32(float(i)) for i in ints], ip + 8
            elif b[:4] == b"\xc5\xe8\x12\xd2":                 # vmovhlps xmm2,xmm2,xmm2
                x[2], ip = [x[2][2], x[2][3], x[2][2], x[2][3]], ip + 4
            elif b[:4] == b"\xc5\xe8\x5e\x15":                 # vdivps xmm2,xmm2,[rip+d]
                m = struct.unpack_from("<4f", self.mem, ip + 8 + d32)
                x[2], ip = [f32_div(x[2][i], m[i]) for i in range(4)], ip + 8
            elif b[:5] == b"\xc5\xfa\x7e\x43\x40":             # vmovq xmm0,[rbx+40h]
                x[0], ip = [self.f(self.rbx + 0x40), self.f(self.rbx + 0x44), 0.0, 0.0], ip + 5
            elif b[:4] == b"\xc5\xf8\x5e\xc2":                 # vdivps xmm0,xmm0,xmm2
                x[0], ip = [f32_div(x[0][i], x[2][i]) for i in range(4)], ip + 4
            elif b[:9] == b"\xc4\xc1\x79\xd6\x86\x48\x01\x00\x00":  # vmovq [r14+148h],xmm0
                struct.pack_into("<ff", self.mem, self.r14 + 0x148, x[0][0], x[0][1])
                ip += 9
            elif b[:5] == b"\xc4\xe1\xf9\x7e\xc0":             # vmovq rax,xmm0
                self.rax, ip = f32_bits(x[0][0]) | f32_bits(x[0][1]) << 32, ip + 5
            elif b[:3] == b"\x0f\x1f\x00":                     # nop
                ip += 3
            elif b[:5] == b"\x0f\x1f\x44\x00\x00":             # nop (5 bytes)
                ip += 5
            elif b[:5] == b"\xc5\xfa\x10\x43\x40":             # vmovss xmm0,[rbx+40h]
                x[0], ip = [self.f(self.rbx + 0x40), 0.0, 0.0, 0.0], ip + 5
            elif b[:5] == b"\xc5\xfa\x10\x4b\x44":             # vmovss xmm1,[rbx+44h]
                x[1], ip = [self.f(self.rbx + 0x44), 0.0, 0.0, 0.0], ip + 5
            elif b[:4] == b"\xc5\xfa\x58\xc0":                 # vaddss xmm0,xmm0,xmm0
                x[0][0], ip = f32(x[0][0] + x[0][0]), ip + 4
            elif b[:4] == b"\xc5\xfa\x10\x15":                 # vmovss xmm2,[rip+d]
                x[2], ip = [self.f(ip + 8 + d32), 0.0, 0.0, 0.0], ip + 8
            elif b[:4] == b"\xc5\xfa\x5e\xc2":                 # vdivss xmm0,xmm0,xmm2
                x[0][0], ip = f32_div(x[0][0], x[2][0]), ip + 4
            elif b[:4] == b"\xc5\xf9\x7e\xc0":                 # vmovd eax,xmm0
                self.rax, ip = f32_bits(x[0][0]), ip + 4
            elif b[:4] == b"\xc5\xf2\x58\xc1":                 # vaddss xmm0,xmm1,xmm1
                x[0][0], ip = f32(x[1][0] + x[1][0]), ip + 4
            elif b[:4] == b"\xc5\xf9\x7e\xc1":                 # vmovd ecx,xmm0
                self.rcx, ip = f32_bits(x[0][0]), ip + 4
            elif b[:4] == b"\x48\xc1\xe1\x20":                 # shl rcx,20h
                self.rcx, ip = (self.rcx << 32) & (2 ** 64 - 1), ip + 4
            elif b[:3] == b"\x48\x09\xc8":                     # or rax,rcx
                self.rax, ip = self.rax | self.rcx, ip + 3
            elif b[:7] == b"\x49\x89\x86\x48\x01\x00\x00":     # mov [r14+148h],rax
                struct.pack_into("<Q", self.mem, self.r14 + 0x148, self.rax)
                ip += 7
            else:
                raise AssertionError(f"unexpected instruction at {ip:#x}: {b.hex()}")
        if ip != end:
            raise AssertionError(f"instruction crosses the end of the site: {ip:#x}")
        return struct.unpack_from("<ff", self.mem, self.r14 + 0x148), self.rax


def shown_on_screen(writes, screen, node_xy):
    """Where the effect lands on a W x H screen: the E2D space (1280x720) is stretched to the screen."""
    machine = E2DMachine(apply(image_with_e2d(), writes), screen, node_xy)
    (ex, ey), _ = machine.run(E2D_SITE)
    w, h = screen
    return ex * w / 1280, ey * h / 720


# Cursor positions in the 1920x1080 GUI canvas (menu items, corners).
CANVAS_POINTS = ((0.0, 0.0), (120.0, 267.0), (120.0, 765.0), (958.5, 540.25), (1919.0, 1079.0))


class E2DNodePositionTests(unittest.TestCase):
    """2D effects on GUI nodes (pause menu cursor glow) follow the node at every resolution preset."""

    def test_presets_are_the_launcher_resolutions(self):
        self.assertEqual(tuple(bp.RESOLUTION_SIZES), PRESETS)

    def test_written_once_at_every_preset_and_never_at_native(self):
        image = image_with_e2d()
        for name in PRESETS:
            with self.subTest(name=name):
                writes = bp.resolution_patches(image, name)
                at_site = [w for w in writes if w[0] < E2D_SITE + bp.E2D_NODE_POS_LEN and
                           E2D_SITE < w[0] + len(w[1])]
                self.assertEqual(at_site, bp.e2d_node_pos_patch(image))
                self.assertEqual([(o, len(d)) for o, d in at_site], [(E2D_SITE, bp.E2D_NODE_POS_LEN)])
        for name in ("native", "bogus"):
            self.assertEqual(bp.resolution_patches(image, name), [], name)

    def test_patch_reads_the_screen_rect_where_the_game_does(self):
        (site, code), = bp.e2d_node_pos_patch(image_with_e2d())
        self.assertEqual(site, E2D_SITE)
        self.assertEqual(code[10:14], b"\xc5\xf8\x5b\x90")  # vcvtdq2ps xmm2,[rax+disp32]
        self.assertEqual(struct.unpack_from("<I", code, 14)[0], SCREEN_RECT)

    def test_other_patches_are_the_release_ones_at_every_preset(self):
        with_code, without_code = image_with_e2d(), image_with_e2d(sites=())
        for name in PRESETS:
            with self.subTest(name=name):
                writes = bp.resolution_patches(with_code, name)
                self.assertEqual(writes[:-1], bp.resolution_patches(without_code, name))
                self.assertEqual(writes[:-1], bp.resolution_patches(synthetic_image(), name))
                self.assertEqual(writes[-1][0], E2D_SITE)

    def test_effect_lands_on_the_node_at_every_preset(self):
        image = image_with_e2d()
        for name in PRESETS:
            w, h = bp.RESOLUTION_SIZES[name]
            writes = bp.resolution_patches(image, name)
            for cx, cy in CANVAS_POINTS:
                node = (f32(cx * w / 1920), f32(cy * h / 1080))  # the node's screen position
                with self.subTest(name=name, canvas=(cx, cy)):
                    sx, sy = shown_on_screen(writes, (w, h), node)
                    self.assertAlmostEqual(sx, node[0], delta=1e-5 * max(1.0, abs(node[0])))
                    self.assertAlmostEqual(sy, node[1], delta=1e-5 * max(1.0, abs(node[1])))

    def test_the_game_code_misses_the_node_off_1080p(self):
        # The fault the patch removes: unpatched, the effect lands at node x W/1920 (720p: 0.67).
        for name in PRESETS:
            w, h = bp.RESOLUTION_SIZES[name]
            node = (f32(120 * w / 1920), f32(765 * h / 1080))
            with self.subTest(name=name):
                sx, sy = shown_on_screen([], (w, h), node)
                self.assertAlmostEqual(sx / node[0], w / 1920, places=5)
                self.assertGreater(abs(sx - node[0]), 0.01 * node[0])
        node = (120.0, 765.0)  # native: the game's code is right, and the patch is not written there
        self.assertEqual(shown_on_screen([], (1920, 1080), node), node)

    def test_patch_equals_the_game_code_bit_for_bit_at_1920x1080(self):
        # x / (1920/1280) and (x + x) / 3.0 round identically; the stored value and rax match too.
        # Only above 1.7e38, where the game's x + x overflows to infinity, do they differ (a node
        # position is a screen pixel, never that large).
        image = image_with_e2d()
        patch = bp.e2d_node_pos_patch(image)
        values = [0.0, -0.0, 1.0, -1.0, 1e-30, 1e38, -1.7e38, 1919.0, 1080.0, 0.1, 765.333]
        values += [struct.unpack("<f", struct.pack("<I", bits))[0]
                   for bits in range(0x3f800000, 0x45000000, 0x13579b)]
        for xv, yv in zip(values, reversed(values)):
            game = E2DMachine(image, (1920, 1080), (xv, yv)).run(E2D_SITE)
            fixed = E2DMachine(apply(image, patch), (1920, 1080), (xv, yv)).run(E2D_SITE)
            self.assertEqual([f32_bits(v) for v in fixed[0]], [f32_bits(v) for v in game[0]], (xv, yv))
            self.assertEqual(fixed[1], game[1], (xv, yv))

    def test_unexpected_code_stops_with_an_error(self):
        cases = {
            "site twice": image_with_e2d(sites=(E2D_SITE, 0x1400)),
            "no scale code": image_with_e2d(scale_sites=()),
            "scale code twice": image_with_e2d(scale_sites=(UBGUI_SITE, 0x1500)),
            "not 3.0": image_with_e2d(three=2.0),
            "not 1280x720": image_with_e2d(scale=(1920.0, 1080.0)),
            "no pop rbx after": image_with_e2d(after=0xc3),
        }
        for case, image in cases.items():
            for name in PRESETS:
                with self.subTest(case=case, name=name):
                    with self.assertRaises(ValueError):
                        bp.resolution_patches(image, name)
            self.assertEqual(bp.resolution_patches(image, "native"), [], case)

    def test_eboot_without_the_code_keeps_the_other_patches(self):
        self.assertEqual(bp.e2d_node_pos_patch(synthetic_image()), [])
        self.assertEqual(bp.e2d_node_pos_patch(image_with_e2d(sites=(), scale_sites=())), [])

    def test_build_writes_it_at_every_preset(self):
        image = image_with_e2d()
        with tempfile.TemporaryDirectory() as tmp:
            elf, out = Path(tmp) / "eboot.elf", Path(tmp) / "patches.bin"
            elf.write_bytes(synthetic_elf(image))
            for name in PRESETS:
                with self.subTest(name=name):
                    writes, notes = bp.build(elf, out, False, resolution=name)
                    self.assertEqual(writes, bp.resolution_patches(image, name))
                    self.assertIn(bp.e2d_node_pos_patch(image)[0], writes)
                    self.assertEqual(notes, [f"resolution {name}"])
            self.assertEqual(bp.build(elf, out, False, resolution="native"), ([], []))
            elf.write_bytes(synthetic_elf(image_with_e2d(three=2.5)))
            with self.assertRaises(ValueError):
                bp.build(elf, out, False, resolution="720p")


# The game's motion blur tile: uMotionBlurFilter sizes its tile grid (W + 19) / 20 x (H + 19) / 20.
# Kept here as a literal so the test does not follow a wrong tile size in the patcher.
MOTION_BLUR_TILE = 20


class RenderSizeTests(unittest.TestCase):
    """Every render size is whole motion blur tiles, at every preset and for any target size."""

    def test_every_preset_renders_at_whole_tiles(self):
        self.assertEqual(tuple(bp.RESOLUTION_TARGETS), PRESETS)
        self.assertEqual(tuple(bp.RESOLUTION_SIZES), PRESETS)
        for name in PRESETS:
            (tw, th), (w, h) = bp.RESOLUTION_TARGETS[name], bp.RESOLUTION_SIZES[name]
            with self.subTest(name=name):
                self.assertEqual((w % MOTION_BLUR_TILE, h % MOTION_BLUR_TILE), (0, 0))
                self.assertTrue(tw <= w < tw + MOTION_BLUR_TILE and th <= h < th + MOTION_BLUR_TILE)
                # Within 1% of the target's aspect, so the movie picture still fills the screen.
                self.assertLess(abs((w / h) / (tw / th) - 1), 0.01)

    def test_only_480p_changes(self):
        self.assertEqual(bp.RESOLUTION_SIZES, {
            "360p": (640, 360), "480p": (860, 480), "540p": (960, 540), "720p": (1280, 720),
            "900p": (1600, 900), "1440p": (2560, 1440), "1800p": (3200, 1800), "4K": (3840, 2160)})

    def test_any_target_size_rounds_up_to_whole_tiles(self):
        rng = random.Random(20261116)
        sizes = [(1, 1), (19, 21), (20, 20), (854, 480), (1360, 768), (2048, 1536), (7680, 4320)]
        sizes += [(rng.randint(1, 8192), rng.randint(1, 8192)) for _ in range(2000)]
        for tw, th in sizes:
            w, h = bp.render_size(tw, th)
            self.assertEqual((w % MOTION_BLUR_TILE, h % MOTION_BLUR_TILE), (0, 0), (tw, th))
            self.assertTrue(tw <= w < tw + MOTION_BLUR_TILE and th <= h < th + MOTION_BLUR_TILE, (tw, th))
            if tw % MOTION_BLUR_TILE == 0 and th % MOTION_BLUR_TILE == 0:
                self.assertEqual((w, h), (tw, th))

    def test_480p_patch_writes_860_into_every_render_site(self):
        image = synthetic_image()
        writes = dict(bp.resolution_patches(image, "480p"))
        self.assertEqual(writes[SITES["RENDER_PAT_1"] + 7], struct.pack("<I", 860))
        self.assertEqual(writes[SITES["RENDER_PAT_1"] + 18], struct.pack("<I", 480))
        self.assertEqual(writes[SITES["RENDER_PAT_2"] + 2], struct.pack("<II", 860, 480))
        self.assertEqual(writes[SITES["RENDER_PAT_4"] + 6], struct.pack("<I", 860))

    def test_the_runtime_uses_the_same_sizes(self):
        # gpu/shim/spartanport_graphics.h sizes the window and the host scene from the same targets with
        # the same rule; a drift between the two would render the guest and the host at different sizes.
        header = (ROOT / "gpu/shim/spartanport_graphics.h").read_text(encoding="utf-8")
        self.assertIn(f"RenderTile = {MOTION_BLUR_TILE};", header)
        cpp = {name: (int(w), int(h)) for name, w, h in
               re.findall(r'\{"(\w+)", RenderSize\((\d+), (\d+)\)\}', header)}
        for name in PRESETS:
            with self.subTest(name=name):
                self.assertEqual(cpp[name], bp.RESOLUTION_TARGETS[name])
        self.assertEqual(cpp["native"], (1920, 1080))


if __name__ == "__main__":
    unittest.main()
