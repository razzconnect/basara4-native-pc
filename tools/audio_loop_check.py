#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Checks that streamed music (BGM) loops the way the game asked for it.

  python tools/audio_loop_check.py [user_dir]        (default: ./user, else the user\\ folder next to tools\\)

Needs a session recorded with "Tracer" + "+ WAV capture" in the launcher (user\\audio_debug.log and user\\audio_wav\\voice_*.wav),
and numpy. For every ATRAC9 stereo stream that started with a calc_block line, it works out from the log

  b0    samples of the first pass   = samples requested - position            (the part the game plays before the loop point)
  loop  samples of one loop pass    = second value of extra_args of the calc_block line

and compares the captured voice WAV with itself. If the loop is right, everything after b0 repeats the last `loop` samples of the first
pass sample for sample (the decoder is deterministic):

  pass 2 start  == first pass at  b0 - loop          (the audio that follows the seam)
  pass 2 end    == first pass at  b0 - 10 s
  pass 3 start  == pass 2 start

Verdicts per voice
  PASS           loops with no shift: the audio after the seam is the loop body, no silence at the seam, no silent run afterwards
  SEAM-GAP       the content is right (correlation >= 0.99) but it starts late or early, or has silence at the seam (the next block
                 reached the decoder late). Reported in milliseconds.
  FAIL           the content after the seam is not the loop body, or a silent run of 0.2 s or more follows
  NOT-EXERCISED  the voice ended before the first loop point; nothing to say about the loop

The exit code is 1 when any voice FAILs. Reads the WAV files through memory maps; nothing is written.
"""
import re
import struct
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is needed: python -m pip install numpy")

LINE = re.compile(r"^\[\s*(\d+) ms\] (\S+)\s?(.*)$")
RACK = re.compile(r"rack=0x1000/(\d+)")
VOICE = re.compile(r"voice=(\d+)")
CALC = re.compile(r"position=(\d+) samples=(\d+) -> .*?extra_args=(\d+),(\d+)")
PARSED = re.compile(r"blocks=2 b0\{[^}]*samples=(\d+)\}.*?b1\{[^}]*samples=(\d+)\}")
RATE = 48000
ID_HEAD = 2000          # samples after a seam in which the decoder is still settling
SILENT = 1e-4


def default_user():
    for candidate in (Path("user"), Path(__file__).resolve().parent.parent / "user"):
        if candidate.is_dir():
            return candidate
    return Path("user")


def read_log(path):
    starts, calcs, parsed = [], [], []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        ms, kind, rest = int(m.group(1)), m.group(2), m.group(3)
        if kind == "ngs2.calc_block" and "type=0x40" in rest and "channels=2" in rest:
            c = CALC.search(rest)
            if c:
                calcs.append((ms, int(c.group(1)), int(c.group(2)), int(c.group(4))))
        elif kind == "ngs2.parse_file":
            p = PARSED.search(rest)
            if p:
                parsed.append((ms, int(p.group(1)), int(p.group(2))))
        elif kind == "ngs2.start" and "type=0x40" in rest and "streaming=1" in rest and "channels=2" in rest:
            r, v = RACK.search(rest), VOICE.search(rest)
            if r and v:
                starts.append((ms, int(r.group(1)), int(v.group(1))))
    return starts, calcs, parsed


def open_wav(path):
    head = path.read_bytes()[:44]
    channels = struct.unpack_from("<H", head, 22)[0]
    rate = struct.unpack_from("<I", head, 24)[0]
    data = np.memmap(path, dtype="<f4", mode="r", offset=44)
    return data[: (data.shape[0] // channels) * channels].reshape(-1, channels), rate


def compare(x, a, b, n):
    """(identical share, largest difference) of x[a:a+n] against x[b:b+n]; None if the audio is too short."""
    if a < 0 or b + n > x.shape[0]:
        return None
    d = np.abs(np.asarray(x[a:a + n], dtype=np.float64) - np.asarray(x[b:b + n], dtype=np.float64))
    return float(np.mean(d < 1e-6)), float(d.max())


def silent_runs(x, min_blocks=20):
    """Runs of 10 ms blocks whose peak is below SILENT: (start s, length s)."""
    block = RATE // 100
    runs, start, index = [], None, 0
    step = block * 1000
    for off in range(0, x.shape[0], step):
        chunk = np.asarray(x[off:off + step], dtype=np.float32)
        n = chunk.shape[0] // block
        if n == 0:
            break
        peaks = np.abs(chunk[: n * block]).reshape(n, block, -1).max(axis=(1, 2))
        for q in peaks < SILENT:
            if q and start is None:
                start = index
            if not q and start is not None:
                if index - start >= min_blocks:
                    runs.append((start / 100.0, (index - start) / 100.0))
                start = None
            index += 1
    if start is not None and index - start >= min_blocks:
        runs.append((start / 100.0, (index - start) / 100.0))
    return runs


def align(x, b0, loop):
    """Where the audio 2 s after the seam sits in the first pass: (shift in samples, correlation)."""
    ref_start, probe_at = b0 - loop, b0 + 96000
    if ref_start < 0 or probe_at + 48000 > x.shape[0] or ref_start + 480000 > x.shape[0]:
        return None
    ref = np.asarray(x[ref_start:ref_start + 480000, 0], dtype=np.float64)
    probe = np.asarray(x[probe_at:probe_at + 48000, 0], dtype=np.float64)
    n = 1 << int(np.ceil(np.log2(len(ref) + len(probe))))
    corr = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(probe, n)), n)[: len(ref) - len(probe)]
    energy = np.sqrt(np.convolve(ref ** 2, np.ones(len(probe)), "valid")[: len(corr)] * np.sum(probe ** 2))
    best = int(np.argmax(corr))
    score = float(corr[best] / energy[best]) if energy[best] else 0.0
    return best - 96000, score


def check(wav, b0, loop):
    x, rate = open_wav(wav)
    if rate != RATE:
        return "FAIL", [f"unexpected sample rate {rate}"]
    notes = [f"{x.shape[0] / RATE:.1f} s captured, first pass {b0 / RATE:.2f} s, loop {loop / RATE:.2f} s"]
    if loop <= 0 or loop > b0:
        return "NOT-EXERCISED", notes + ["the stream has no loop block"]
    if x.shape[0] < b0 + RATE:
        return "NOT-EXERCISED", notes + [f"the voice ended {max(0.0, (b0 - x.shape[0]) / RATE):.1f} s before the first loop point"]
    after = np.abs(np.asarray(x[b0:b0 + 96000], dtype=np.float32)).max(axis=1) if x.shape[0] >= b0 + 96000 else \
        np.abs(np.asarray(x[b0:], dtype=np.float32)).max(axis=1)
    ms_peaks = after[: (len(after) // 48) * 48].reshape(-1, 48).max(axis=1)
    lead_silence = 0
    for q in ms_peaks < SILENT:
        if not q:
            break
        lead_silence += 1
    shifted = align(x, b0, loop)
    runs = [r for r in silent_runs(x) if r[0] * RATE >= b0 - RATE]
    verdict = "PASS"
    head = compare(x, b0 - loop, b0, ID_HEAD)
    rest = compare(x, b0 - loop + ID_HEAD, b0 + ID_HEAD, min(480000, x.shape[0] - b0 - ID_HEAD)) if x.shape[0] > b0 + ID_HEAD + 1000 else None
    if head and rest:
        notes.append(f"pass 2 vs first pass: first {ID_HEAD} samples {100 * head[0]:.1f}% identical (max diff {head[1]:.5f}), "
                     f"the next {min(480000, x.shape[0] - b0 - ID_HEAD) / RATE:.1f} s {100 * rest[0]:.2f}% identical (max diff {rest[1]:.5f})")
    tail = compare(x, b0 - 480000, b0 + loop - 480000, 480000)
    if tail:
        notes.append(f"end of pass 2 vs end of the first pass: {100 * tail[0]:.2f}% identical (max diff {tail[1]:.5f})")
    third = compare(x, b0, b0 + loop, 240000)
    if third:
        notes.append(f"start of pass 3 vs start of pass 2: {100 * third[0]:.2f}% identical (max diff {third[1]:.5f})")
    if lead_silence > 1:
        notes.append(f"silence right after the seam: {lead_silence} ms")
    if shifted:
        notes.append(f"the audio 2 s after the seam is {shifted[0]:+d} samples ({1000.0 * shifted[0] / RATE:+.1f} ms) from where a perfect loop puts it, correlation {shifted[1]:.4f}")
        if shifted[1] < 0.99:
            verdict = "FAIL"
        elif shifted[0] != 0 or lead_silence > 1:
            verdict = "SEAM-GAP"
    elif rest and rest[0] < 0.999:
        verdict = "FAIL"
    elif lead_silence > 1:
        verdict = "SEAM-GAP"
    if rest and not shifted and rest[0] < 0.999 and verdict == "PASS":
        verdict = "FAIL"
    if runs:
        notes.append("silent runs of 0.2 s or more after the seam (start s, length s): " + ", ".join(f"{s:.1f}/{l:.1f}" for s, l in runs[:6]))
        verdict = "FAIL"
    return verdict, notes


def main():
    user = Path(sys.argv[1]) if len(sys.argv) > 1 else default_user()
    log, wavs = user / "audio_debug.log", user / "audio_wav"
    if not log.is_file() or not wavs.is_dir():
        sys.exit(f"{log} or {wavs} is missing: tick 'Tracer' and '+ WAV capture' in the launcher and play")
    starts, calcs, parsed = read_log(log)
    failed = False
    seen = 0
    for ms, serial, voice in starts:
        before = [c for c in calcs if ms - 3000 <= c[0] <= ms + 5]
        if before:
            calc_ms, position, samples, loop = before[-1]
        else:
            # menu music is opened without a calc_block: the whole file is the first block
            files = [q for q in parsed if ms - 3000 <= q[0] <= ms + 5]
            if not files:
                continue
            samples, loop, position = files[-1][1], files[-1][2], 0
        wav = next((p for off in range(-5, 6) if (p := wavs / f"voice_r{serial}_v{voice}_{ms + off}.wav").is_file()), None)
        if wav is None:
            continue
        seen += 1
        verdict, notes = check(wav, samples - position, loop)
        failed |= verdict == "FAIL"
        print(f"{verdict:14} {wav.name}  (log {ms / 1000:.1f} s)")
        for note in notes:
            print(f"               {note}")
    if not seen:
        print("no looping music stream with a captured WAV in this session")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
