#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Peak level and clipping of the captured mix.

  python tools/audio_master_check.py [user_dir]        (default: ./user, else the user\\ folder next to tools\\)

Reads user/audio_wav/master_b*.wav (written when "+ WAV capture" is on; float32, what the Ngs2 engine hands to AudioOut) and prints, per file,
its length, its peak, and how many samples are at or above 0.999 (a flat top = clipping). The exit code is 1 when any file has such samples.
Needs numpy. Nothing is written.
"""
import struct
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is needed: python -m pip install numpy")

CHUNK = 2_000_000
HOT = 0.999


def default_user():
    for candidate in (Path("user"), Path(__file__).resolve().parent.parent / "user"):
        if candidate.is_dir():
            return candidate
    return Path("user")


def main():
    user = Path(sys.argv[1]) if len(sys.argv) > 1 else default_user()
    files = sorted((user / "audio_wav").glob("master_b*.wav"))
    if not files:
        sys.exit(f"no master_b*.wav in {user / 'audio_wav'}: tick 'Tracer' and '+ WAV capture' in the launcher and play")
    clipped = False
    for path in files:
        head = path.read_bytes()[:44]
        channels, rate = struct.unpack_from("<H", head, 22)[0], struct.unpack_from("<I", head, 24)[0]
        data = np.memmap(path, dtype="<f4", mode="r", offset=44)
        data = data[: (data.shape[0] // channels) * channels].reshape(-1, channels)
        peak, hot = 0.0, 0
        for start in range(0, data.shape[0], CHUNK):
            block = np.abs(np.asarray(data[start:start + CHUNK]))
            peak = max(peak, float(block.max()))
            hot += int((block >= HOT).sum())
        clipped |= hot > 0
        print(f"{path.name}: {data.shape[0] / rate:.1f} s, {channels} ch, peak {peak:.4f}, samples at or above {HOT}: {hot}")
    sys.exit(1 if clipped else 0)


if __name__ == "__main__":
    main()
