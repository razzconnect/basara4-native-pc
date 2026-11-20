#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Finds audio problems in a play session without being told when they happened.

  python tools/audio_session_report.py [user_dir]        (default: user next to this repo's package, or ./user)

Reads user/audio_debug.log, user/last_run.log and, when "+ WAV capture" was on, user/audio_wav/*.wav, and prints a list of
findings with the time in the session (m:ss on the audio log's clock). Each finding says what was measured.

  SILENT-VOICE   a long voice produced nothing (all zeros) or nothing for a long stretch while it was playing
  STUCK-STARVED  a streaming voice with blocks queued that never recovers
  DECODE         ATRAC9 decode failures / waiting-for-data
  RESTART        the same data restarted on the same voice within a short time (a repeated word)
  TWIN           two voices started on the same data within 150 ms (a doubled line)
  MIX-GAP        the mix is silent for seconds while a long voice is playing
  CLIP           the mix reached full scale
  FAULT          the game crashed or asserted (last_run.log), with the audio context just before it
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

LINE = re.compile(r"^\[\s*(\d+) ms\] (\S+)\s?(.*)$")
PAIR = re.compile(r"([\w^.]+)=(\S+)")


def mmss(ms):
    return f"{int(ms) // 60000}:{int(ms) // 1000 % 60:02d}"


def parse(path):
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if m:
            yield int(m.group(1)), m.group(2), dict(PAIR.findall(m.group(3))), m.group(3)


def wav_info(path):
    with open(path, "rb") as f:
        head = f.read(44)
    channels = int.from_bytes(head[22:24], "little")
    rate = int.from_bytes(head[24:28], "little")
    return channels, rate


def wav_samples(path, channels):
    raw = np.memmap(path, dtype="<f4", mode="r", offset=44)
    n = (raw.shape[0] // channels) * channels
    return raw[:n].reshape(-1, channels)


def silent_runs(mono_abs, rate, threshold=1e-4, min_seconds=1.5):
    """(start_s, length_s) of runs below the threshold, measured on 10 ms blocks."""
    block = max(1, rate // 100)
    n = mono_abs.shape[0] // block
    if n == 0:
        return []
    peaks = mono_abs[: n * block].reshape(n, block).max(axis=1)
    quiet = peaks < threshold
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        if (not q or i == n - 1) and start is not None:
            end = i + 1 if q else i
            if (end - start) * 0.01 >= min_seconds:
                runs.append((start * 0.01, (end - start) * 0.01))
            start = None
    return runs


def main():
    user = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("user")
    log = user / "audio_debug.log"
    if not log.is_file():
        sys.exit(f"{log} not found (tick 'Tracer' in the launcher and play)")
    events = list(parse(log))
    if not events:
        sys.exit("empty audio log")
    end_ms = events[-1][0]
    findings = []
    print(f"audio log: {len(events)} lines, {mmss(end_ms)} of play\n")

    # --- starts, per rack/voice
    starts = [(ms, kv) for ms, k, kv, _ in events if k == "ngs2.start"]
    rack_of = lambda kv: kv.get("rack", "?")
    major = [(ms, kv) for ms, kv in starts if kv.get("major") == "1"]
    print(f"voice starts: {len(starts)} logged, {len(major)} long")

    # --- RESTART / TWIN: same data pointer
    last_by_voice = {}
    for ms, kv in starts:
        key = (rack_of(kv), kv.get("voice"))
        data = kv.get("data")
        if kv.get("channels") == "1" or kv.get("major") == "1":
            prev = last_by_voice.get(key)
            if prev and prev[1] == data and data not in (None, "0000000000000000") and 50 <= ms - prev[0] < 2500:
                findings.append((ms, "RESTART", f"{key[0]} voice {key[1]}: same data {data} started again {ms - prev[0]} ms after its previous start"))
        last_by_voice[key] = (ms, data)
    by_data = defaultdict(list)
    for ms, kv in starts:
        if kv.get("channels") == "1" and kv.get("data") not in (None, "0000000000000000"):
            by_data[kv["data"]].append((ms, rack_of(kv), kv.get("voice")))
    for data, lst in by_data.items():
        lst.sort()
        for (a, ra, va), (b, rb, vb) in zip(lst, lst[1:]):
            if b - a <= 150 and (ra, va) != (rb, vb):
                findings.append((b, "TWIN", f"{ra}/v{va} and {rb}/v{vb} started on the same data {data} {b - a} ms apart (the game also does this on purpose for stereo pairs)"))

    # --- STUCK-STARVED
    open_starved = {}
    for ms, k, kv, _ in events:
        key = (kv.get("rack"), kv.get("voice"))
        if k == "ngs2.starved":
            open_starved[key] = (ms, kv.get("queued"))
        elif k in ("ngs2.refilled", "ngs2.end", "ngs2.stop", "ngs2.kill", "ngs2.start", "ngs2.blocks") and key in open_starved:
            began, queued = open_starved[key]
            if k in ("ngs2.refilled", "ngs2.start") or (k == "ngs2.blocks" and kv.get("append") == "0"):
                if ms - began > 3000 and queued not in (None, "0"):
                    findings.append((began, "STUCK-STARVED", f"{key[0]} voice {key[1]} starved with {queued} blocks queued for {(ms - began) / 1000:.1f} s"))
                del open_starved[key]
    for key, (began, queued) in open_starved.items():
        if end_ms - began > 3000 and queued not in (None, "0"):
            findings.append((began, "STUCK-STARVED", f"{key[0]} voice {key[1]} still starved at the end ({(end_ms - began) / 1000:.1f} s, {queued} blocks queued)"))

    # --- DECODE
    decode = [(ms, k, t) for ms, k, _, t in events if k in ("ngs2.at9_decode_failed", "ngs2.at9_waiting_for_data", "ngs2.at9_init_failed", "ngs2.at9_no_audio")]
    for ms, k, t in decode[:12]:
        findings.append((ms, "DECODE", f"{k}: {t[:150]}"))
    if len(decode) > 12:
        findings.append((decode[12][0], "DECODE", f"... and {len(decode) - 12} more decode events"))

    # --- WAV analysis
    wav_dir = user / "audio_wav"
    if np is not None and wav_dir.is_dir():
        for path in sorted(wav_dir.glob("voice_r*_v*_*.wav")):
            m = re.match(r"voice_r(\d+)_v(\d+)_(\d+)\.wav", path.name)
            if not m:
                continue
            serial, voice, began = m.group(1), m.group(2), int(m.group(3))
            channels, rate = wav_info(path)
            if not channels or path.stat().st_size < 48:
                continue
            x = np.abs(wav_samples(path, channels)).max(axis=1)
            seconds = x.shape[0] / rate
            if seconds < 0.5:
                continue
            if float(x.max()) < 1e-4:
                findings.append((began, "SILENT-VOICE", f"rack #{serial} voice {voice}: {seconds:.1f} s of pure silence ({path.name})"))
                continue
            for s, length in silent_runs(x, rate, min_seconds=2.0)[:3]:
                findings.append((began + int(s * 1000), "SILENT-VOICE", f"rack #{serial} voice {voice}: {length:.1f} s silent inside a {seconds:.1f} s voice ({path.name})"))
        # a voice that is audible in its own file but absent from every mix buffer
        def envelope(path):
            channels, rate = wav_info(path)
            x = np.abs(wav_samples(path, channels)).max(axis=1)
            block = max(1, rate // 100)
            n = x.shape[0] // block
            return x[: n * block].reshape(n, block).max(axis=1) if n else np.zeros(0)
        master_env = [envelope(p) for p in sorted(wav_dir.glob("master_b*.wav"))]
        for p in sorted(wav_dir.glob("master_b*.wav")):
            channels, rate = wav_info(p)
            x = np.abs(wav_samples(p, channels)).max(axis=1)
            if x.shape[0] and float(x.max()) >= 0.999:
                findings.append((int(int(np.argmax(x >= 0.999)) / rate * 1000), "CLIP", f"{p.name}: reached full scale ({int((x >= 0.999).sum())} samples)"))
        if master_env:
            length = min(len(e) for e in master_env)
            mix_env = np.max([e[:length] for e in master_env], axis=0)
            for path in sorted(wav_dir.glob("voice_r*_v*_*.wav")):
                m = re.match(r"voice_r(\d+)_v(\d+)_(\d+)\.wav", path.name)
                if not m or path.stat().st_size < 48:
                    continue
                v_env = envelope(path)
                offset = int(m.group(3)) // 10
                end = min(len(v_env), max(0, length - offset))
                if end <= 100:
                    continue
                audible = v_env[:end] > 0.02
                missing = audible & (mix_env[offset: offset + end] < 1e-4)
                # runs of at least 1 s
                run = 0
                for i, bad in enumerate(missing):
                    run = run + 1 if bad else 0
                    if run == 100:
                        findings.append(((offset + i - 99) * 10, "MIX-GAP", f"rack #{m.group(1)} voice {m.group(2)} is audible in {path.name} but absent from every master buffer"))
                        break
    elif wav_dir.is_dir():
        print("(numpy missing: WAV analysis skipped)")
    else:
        print("(no audio_wav folder: tick '+ WAV capture' to also measure the sound itself)")

    # --- FAULT
    last = user / "last_run.log"
    if last.is_file():
        text = last.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^(Fault: .*|.*Assertion Failed.*|.*Unhandled.*)$", text, re.M):
            findings.append((end_ms, "FAULT", m.group(1)[:200]))

    findings.sort()
    if not findings:
        print("\nNo findings.")
        return
    print(f"\n{len(findings)} findings:")
    for ms, kind, text in findings:
        print(f"  {mmss(ms):>6}  {kind:14} {text}")


if __name__ == "__main__":
    main()
