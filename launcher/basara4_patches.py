"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Sengoku Basara 4 Sumeragi (CUSA01159) memory patches for the runtime loader.

Writes patches.bin (G3PATCH2: patch base, count, then offset/length/bytes per write), found by code
signature in the game's eboot.elf so they apply to any build with the same code.

unlimited_fps: the game's own frame limiter (a sleep followed by a spin on the TSC until one frame
period has passed) is skipped. The game measures the real time each frame took and scales its update
by it, so a higher frame rate does not make it run faster.
"""
import argparse
import struct
import sys
from pathlib import Path

import basara4_cheats

EBOOT_BASE = 0x400000

# vsubss/vmulss, vucomiss, jb (sleep only when the wait is long) ... call usleep ... vucomiss, jbe (leave
# the spin loop at once when the period has passed). "??" bytes are displacements.
LIMITER = ("c5 e2 5c d0 c5 f2 59 ca c5 f8 2e 0d ?? ?? ?? ?? 72 20 c4 e1 fa 2c c1 69 f8 e8 03 00 00 "
           "81 c7 18 fc ff ff e8 ?? ?? ?? ?? c5 fa 10 5d cc c5 fa 10 43 60 c5 f8 2e d8 "
           "c5 fb 10 4d d0 0f 86 ?? ?? ?? ??")
SLEEP_JB, SPIN_JBE = 16, 59


def eboot_image(elf):
    """The PT_LOAD bytes of eboot.elf at their virtual addresses."""
    phoff, = struct.unpack_from('<Q', elf, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', elf, 0x36)
    loads = []
    for i in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from('<IIQQQQQQ', elf, phoff + i * phentsize)
        if kind == 1:
            loads.append((vaddr, offset, filesz))
    image = bytearray(max(v + f for v, _, f in loads))
    for vaddr, offset, filesz in loads:
        image[vaddr:vaddr + filesz] = elf[offset:offset + filesz]
    return bytes(image)


def eboot_load_segments(elf):
    """[(virtual address, memory size, flags)] for PT_LOAD segments."""
    phoff, = struct.unpack_from('<Q', elf, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', elf, 0x36)
    loads = []
    for i in range(phnum):
        kind, flags, _, vaddr, _, _, memsz, _ = struct.unpack_from(
            '<IIQQQQQQ', elf, phoff + i * phentsize)
        if kind == 1:
            loads.append((vaddr, memsz, flags))
    return loads


def find(image, text):
    pattern = [None if b == '??' else int(b, 16) for b in text.split()]
    # longest run of fixed bytes
    best, run_start = (0, 0), None
    for i, b in enumerate(pattern + [None]):
        if b is not None and run_start is None:
            run_start = i
        if b is None and run_start is not None:
            if i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    a, b = best
    anchor = bytes(pattern[a:b])
    found, at = [], image.find(anchor)
    while at >= 0:
        s = at - a
        if s >= 0 and s + len(pattern) <= len(image) and all(
                p is None or image[s + i] == p for i, p in enumerate(pattern)):
            found.append(s)
        at = image.find(anchor, at + 1)
    return found


def unlimited_fps(image):
    """[(image offset, bytes)] that remove the game's frame wait, or raise LookupError."""
    sites = find(image, LIMITER)
    if len(sites) != 1:
        raise LookupError('frame limiter code not found' if not sites else 'frame limiter code found twice')
    base = sites[0]
    jbe = base + SPIN_JBE
    disp, = struct.unpack_from('<i', image, jbe + 2)
    return [(base + SLEEP_JB, b'\xeb\x20'),
            (jbe, b'\xe9' + struct.pack('<i', disp + 1) + b'\x90')]


# Render & UI resolution patch sites for Capcom MT Framework (Sengoku Basara 4 Sumeragi)
RENDER_PAT_1        = "41 c7 85 b8 00 00 00 80 07 00 00 41 c7 85 bc 00 00 00 38 04 00 00"
RENDER_PAT_2        = "48 b8 80 07 00 00 38 04 00 00 49 89 85 b8 00 00 00"
RENDER_PAT_3        = "48 b8 80 07 00 00 38 04 00 00 4a 89 84 2b 80 ab 07 01"
RENDER_PAT_4        = "c7 83 c0 00 00 00 80 07 00 00 c7 83 c4 00 00 00 38 04 00 00"
RENDER_PAT_5        = "c7 83 b8 00 00 00 80 07 00 00 c7 83 bc 00 00 00 38 04 00 00"
GARLIC_PAT          = "ba 09 00 00 00 b9 00 00 00 80 45 31 c0 41 b9 06 02 00 00"
GUI_SCALE_CLAMP_PAT = "c5 fa 10 1d d7 28 61 00 c5 62 5d c4 c5 e2 5f ec"
HUD_PROJ_CLAMP_PAT  = "c5 fa 10 25 41 bd ed 00 c5 da 5d c9 c5 fa 10 25 39 bd ed 00 c5 da 5d db"
ICON_SCALE_W_PAT    = "81 f9 81 07 00 00 7c 22 48 63 d1"
ICON_SCALE_H_PAT    = "3d 39 04 00 00 7c 22 48 63 c8"
RTX_MIP_LOOP_PAT    = (
    "eb 3f 66 2e 0f 1f 84 00 00 00 00 00 "
    "4c 89 d0 4c 0f a4 c0 3f " + "?? " * 45 +
    "4c 89 c3 48 c1 eb 26 81 e3 ff 1f 00 00 39 cb 77 ba "
    "4c 89 c3 48 c1 eb 33 39 d3 77 af"
)

# Proportional un-clamped scaling for all 10 cGUI resolution modes (replaces clamp at 0xeea909)
GUI_SCALE_UNCLAMP = (
    b'\xc5\x78\x28\xc4'                  # vmovaps xmm8,  xmm4 (max(x, y))
    b'\xc5\xf8\x28\xec'                  # vmovaps xmm5,  xmm4 (max(x, y))
    b'\xc5\x78\x28\xc8'                  # vmovaps xmm9,  xmm0 (x_scale)
    b'\xc5\x78\x28\xd1'                  # vmovaps xmm10, xmm1 (y_scale)
    b'\xc5\x78\x28\xd8'                  # vmovaps xmm11, xmm0 (x_scale)
    b'\xc5\x78\x28\xe1'                  # vmovaps xmm12, xmm1 (y_scale)
    b'\xc5\xf8\x28\xfa'                  # vmovaps xmm7,  xmm2 (min(x, y))
    b'\xc5\xf8\x28\xda'                  # vmovaps xmm3,  xmm2 (min(x, y))
    b'\x0f\x1f\x84\x00\x00\x00\x00\x00'  # 8-byte nop padding
)

# uBasaraGUI's helper that places a 2D effect on a GUI node (the pause menu cursor glow, and the
# highlights of the restart sub-menu, confirmation popup, gallery tabs, game over and setup menus)
# converts the node's screen position to the effect's 1280x720 space as (x + x) / 3.0 and
# (y + y) / 3.0, i.e. x 1280/1920: right only on a 1920x1080 screen. Elsewhere the effect lands at
# position x W/1920 (720p: 0.67, 1440p: 1.33). The site, 56 bytes:
#   vmovss xmm0,[rbx+40h]; vmovss xmm1,[rbx+44h]; vaddss xmm0,xmm0,xmm0; vmovss xmm2,[rip+3.0];
#   vdivss xmm0,xmm0,xmm2; vmovd eax,xmm0; vaddss xmm0,xmm1,xmm1; vdivss xmm0,xmm0,xmm2;
#   vmovd ecx,xmm0; shl rcx,20h; or rax,rcx; mov [r14+148h],rax        (then pop rbx)
E2D_NODE_POS_PAT = ("c5 fa 10 43 40 c5 fa 10 4b 44 c5 fa 58 c0 c5 fa 10 15 ?? ?? ?? ?? c5 fa 5e c2 "
                    "c5 f9 7e c0 c5 f2 58 c1 c5 fa 5e c2 c5 f9 7e c1 48 c1 e1 20 48 09 c8 "
                    "49 89 86 48 01 00 00")
E2D_NODE_POS_LEN = 56
# uBasaraGUI's own 1280x720-to-screen scale, only read: lea rax,[rip+viewport pointer];
# mov rax,[rax]; vcvtdq2ps xmm0,[rax+0D30h] (the screen rect the render size patches fill);
# vmovhlps xmm1,xmm0,xmm0; vdivss xmm1,xmm1,[rip+1280.0] (720.0 follows it); vmovss [rdi],xmm1.
UBGUI_SCALE_PAT = ("48 8d 05 ?? ?? ?? ?? 48 8b 00 c5 f8 5b 80 30 0d 00 00 c5 f8 12 c8 "
                   "c5 f2 5e 0d ?? ?? ?? ?? c5 fa 11 0f")


def _rip_target(image, disp_at, next_ip):
    """The image offset a rip-relative operand points at (its rel32 at disp_at)."""
    return next_ip + struct.unpack_from('<i', image, disp_at)[0]


def _f32_at(image, at):
    if not 0 <= at <= len(image) - 4:
        raise ValueError(f'2D effect position patch: constant at {at + EBOOT_BASE:#x} outside the image')
    return struct.unpack_from('<f', image, at)[0]


def e2d_node_pos_patch(image):
    """[(image offset, bytes)] that place 2D effects on GUI nodes with the screen's real size:
    effect = node / (W / 1280, H / 720), W x H from the screen rect, per axis. [] when this eboot
    does not have the code; ValueError when it has it but not as expected (nothing is written)."""
    sites = find(image, E2D_NODE_POS_PAT)
    if not sites:
        return []
    if len(sites) > 1:
        raise ValueError(f'2D effect position code found {len(sites)} times')
    scales = find(image, UBGUI_SCALE_PAT)
    if len(scales) != 1:
        raise ValueError(f'GUI screen scale code found {len(scales)} times (need 1)')
    a, u = sites[0], scales[0]
    if _f32_at(image, _rip_target(image, a + 18, a + 22)) != 3.0:
        raise ValueError(f'2D effect position code at {a + EBOOT_BASE:#x} does not divide by 3.0')
    viewport = _rip_target(image, u + 3, u + 7)  # a pointer in .bss, filled at run time
    rect = image[u + 14:u + 18]  # the game's own displacement of the screen rect (vcvtdq2ps)
    const = _rip_target(image, u + 26, u + 30)
    if (_f32_at(image, const), _f32_at(image, const + 4)) != (1280.0, 720.0):
        raise ValueError(f'GUI screen scale code at {u + EBOOT_BASE:#x} does not divide by 1280/720')
    if a + E2D_NODE_POS_LEN >= len(image) or image[a + E2D_NODE_POS_LEN] != 0x5b:
        raise ValueError(f'2D effect position code at {a + EBOOT_BASE:#x} is not followed by pop rbx')

    def rel32(target, next_ip):
        return struct.pack('<i', target - next_ip)

    code = (b'\x48\x8d\x05' + rel32(viewport, a + 7) +                 # lea rax,[rip+viewport ptr]
            b'\x48\x8b\x00' +                                          # mov rax,[rax]
            b'\xc5\xf8\x5b\x90' + rect +                               # vcvtdq2ps xmm2,[rax+rect]
            b'\xc5\xe8\x12\xd2' +                                      # vmovhlps xmm2,xmm2,xmm2: W, H
            b'\xc5\xe8\x5e\x15' + rel32(const, a + 30) +               # vdivps xmm2,xmm2,[1280, 720]
            b'\xc5\xfa\x7e\x43\x40' +                                  # vmovq xmm0,[rbx+40h]: x, y
            b'\xc5\xf8\x5e\xc2' +                                      # vdivps xmm0,xmm0,xmm2
            b'\xc4\xc1\x79\xd6\x86\x48\x01\x00\x00' +                  # vmovq [r14+148h],xmm0
            b'\xc4\xe1\xf9\x7e\xc0' +                                  # vmovq rax,xmm0 (as before)
            b'\x0f\x1f\x00')                                           # 3-byte nop
    assert len(code) == E2D_NODE_POS_LEN
    return [(a, code)]


# The game's motion blur (uMotionBlurFilter) splits the render target into 20x20 pixel tiles and
# rounds the tile count up, (W + 19) / 20 x (H + 19) / 20: a side that is not a whole number of tiles
# leaves the last tile partly outside the image, where it reads motion that is not there and blurs a
# band along that edge (854x480: every column from 820). Every render size is therefore whole tiles.
RENDER_TILE = 20


def render_size(width, height):
    """The render size for a target screen size: each side rounded up to whole RENDER_TILE tiles."""
    return (-(-width // RENDER_TILE) * RENDER_TILE, -(-height // RENDER_TILE) * RENDER_TILE)


# The screen size each preset aims at; the render size is that rounded to whole motion blur tiles.
RESOLUTION_TARGETS = {
    "360p": (640, 360),
    "480p": (854, 480),
    "540p": (960, 540),
    "720p": (1280, 720),
    "900p": (1600, 900),
    "1440p": (2560, 1440),
    "1800p": (3200, 1800),
    "4K": (3840, 2160),
}
RESOLUTION_SIZES = {name: render_size(w, h) for name, (w, h) in RESOLUTION_TARGETS.items()}


def resolution_patches(image, res_name):
    """Patch MT Framework internal render resolution in eboot.bin code."""
    if res_name not in RESOLUTION_SIZES:
        return []
    w, h = RESOLUTION_SIZES[res_name]
    writes = []
    wb = struct.pack('<I', w)
    hb = struct.pack('<I', h)
    pair = struct.pack('<II', w, h)

    # 1. mov dword ptr [r13+0B8h], W; mov dword ptr [r13+0BCh], H
    for off in find(image, RENDER_PAT_1):
        writes.append((off + 7, wb))
        writes.append((off + 18, hb))

    # 2. mov rax, (H << 32) | W
    for off in find(image, RENDER_PAT_2):
        writes.append((off + 2, pair))

    # 3. mov rax, (H << 32) | W
    for off in find(image, RENDER_PAT_3):
        writes.append((off + 2, pair))

    # 4. mov dword ptr [rbx+0C0h], W; mov dword ptr [rbx+0C4h], H
    for off in find(image, RENDER_PAT_4):
        writes.append((off + 6, wb))
        writes.append((off + 16, hb))

    # 5. mov dword ptr [rbx+0B8h], W; mov dword ptr [rbx+0BCh], H
    for off in find(image, RENDER_PAT_5):
        writes.append((off + 6, wb))
        writes.append((off + 16, hb))

    # 6. Unclamp cGUI scaling modes so 2D UI scales proportionally to resolution without zooming
    for off in find(image, GUI_SCALE_CLAMP_PAT):
        writes.append((off, GUI_SCALE_UNCLAMP))

    # 7. Keep 3D-to-2D world projection (floating HP bars, target lock-on) in 1920x1080 design space
    #    so cGUI scales it once to the screen size across all resolutions (<720p and >1080p).
    for off in find(image, HUD_PROJ_CLAMP_PAT):
        writes.append((off + 8, b'\xc5\xf8\x28\xcc'))
        writes.append((off + 20, b'\xc5\xf8\x28\xdc'))

    # 8. Below 960x540 (480p/360p), stop rRenderTexture (.rtx) mip-downscale before mip_count < 1;
    #    1920x1080 mips=1 textures (movie\RenderTargetTexture) otherwise underflowed 1 -> 0 -> 63
    #    and failed GPU allocation (white video screen).
    if w < 960 or h < 540:
        for off in find(image, RTX_MIP_LOOP_PAT):
            writes.append((off + 2, b'\xf6\x45\xd8\x3e\x74\x55\x90\x90\x90\x90'))
            writes.append((off + 0x50, b'\x77\xb0'))
            writes.append((off + 0x5b, b'\x77\xa5'))

    # 9. For >1080p, bypass uGUIIcon's redundant integer scaling multiplier and expand Garlic heap
    if w > 1920 or h > 1080:
        for off in find(image, ICON_SCALE_W_PAT):
            writes.append((off + 6, b'\xeb'))
        for off in find(image, ICON_SCALE_H_PAT):
            writes.append((off + 5, b'\xeb'))
        for off in find(image, GARLIC_PAT):
            writes.append((off + 6, struct.pack('<I', 0xE0000000)))

    # 10. 2D effects on GUI nodes (pause menu cursor glow and the other menu highlights) follow the
    #     node at every screen size: the effect position is computed from the real screen size.
    writes += e2d_node_pos_patch(image)

    return writes


def write_patches(path, writes):
    blob = struct.pack('<8sQQ', b'G3PATCH2', EBOOT_BASE, len(writes))
    for offset, data in writes:
        blob += struct.pack('<QQ', offset, len(data)) + data
    Path(path).write_bytes(blob)


def build(elf_path, out_path, unlimited, cheats=(), game_version=None, resolution=None):
    writes = []
    notes = []
    elf = Path(elf_path).read_bytes()
    image = eboot_image(elf)
    if unlimited:
        try:
            writes += unlimited_fps(image)
            notes.append('unlimited fps')
        except LookupError as error:
            notes.append(f'unlimited fps skipped: {error}')
    if resolution and resolution != "native":
        res_writes = resolution_patches(image, resolution)
        if res_writes:
            writes += res_writes
            notes.append(f'resolution {resolution}')
    if cheats:
        writes += basara4_cheats.compile_cheats(
            cheats, game_version, image, eboot_load_segments(elf))
        notes.append(f'{len(cheats)} cheat(s)')
    write_patches(out_path, writes)
    return writes, notes


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('elf')
    ap.add_argument('out')
    ap.add_argument('--unlimited-fps', action='store_true')
    a = ap.parse_args()
    writes, notes = build(a.elf, a.out, a.unlimited_fps)
    for offset, data in writes:
        print(f'  {offset + EBOOT_BASE:#x}: {data.hex()}')
    print('Patches:', ', '.join(notes) or 'none', f'({len(writes)} writes)')
