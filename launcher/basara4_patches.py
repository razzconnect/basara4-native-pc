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
RENDER_PAT_1 = "41 c7 85 b8 00 00 00 80 07 00 00 41 c7 85 bc 00 00 00 38 04 00 00"
RENDER_PAT_2 = "48 b8 80 07 00 00 38 04 00 00 49 89 85 b8 00 00 00"
RENDER_PAT_3 = "48 b8 80 07 00 00 38 04 00 00 4a 89 84 2b 80 ab 07 01"
RENDER_PAT_4 = "c7 83 c0 00 00 00 80 07 00 00 c7 83 c4 00 00 00 38 04 00 00"
RENDER_PAT_5 = "c7 83 b8 00 00 00 80 07 00 00 c7 83 bc 00 00 00 38 04 00 00"
UI_PAT_1     = "c7 80 80 00 00 00 80 07 00 00 c7 80 84 00 00 00 38 04 00 00"

RESOLUTION_SIZES = {
    "360p": (640, 360),
    "480p": (854, 480),
    "540p": (960, 540),
    "720p": (1280, 720),
    "900p": (1600, 900),
    "1440p": (2560, 1440),
    "1800p": (3200, 1800),
    "4K": (3840, 2160),
}


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

    # 6. UI size: mov dword ptr [rax+80h], W; mov dword ptr [rax+84h], H
    for off in find(image, UI_PAT_1):
        writes.append((off + 6, wb))
        writes.append((off + 16, hb))

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
