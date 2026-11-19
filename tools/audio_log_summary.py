#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Summarises a user\\audio_debug.log (written with SPARTAN_AUDIO_DEBUG, or "Tracer" in the launcher).

  python tools/audio_log_summary.py user/audio_debug.log

Lines look like "[   12345 ms] kind key=value key=value". The report answers: which AudioOut ports got
sound and when, which Ngs2 waveform types the game used, which long sounds (music, dialogue) started and
whether they were routed to the output, and every kind of failure the runtime noticed.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LINE = re.compile(r"^\[\s*(\d+) ms\] (\S+)\s?(.*)$")
PAIR = re.compile(r"(\w+)=(\S+)")


def parse(path):
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if m:
            yield int(m.group(1)), m.group(2), dict(PAIR.findall(m.group(3))), m.group(3)


def mmss(ms):
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    events = list(parse(sys.argv[1]))
    if not events:
        sys.exit("no audio log lines found")
    kinds = Counter(k for _, k, _, _ in events)
    end = events[-1][0]
    print(f"== {len(events)} lines, {mmss(end)} of play ==")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind:34} {count}")

    print("\n== AudioOut ports ==")
    ports = defaultdict(lambda: dict(type="?", buffers=0, loud=0, peak=0.0, first=None, windows=0, loud_windows=0,
                                     last_loud=None))
    for ms, kind, kv, _ in events:
        if "port" not in kv:
            continue
        p = ports[kv["port"]]
        p["type"] = kv.get("type", p["type"]).split("(")[0]
        if kind == "audioout.first_sound":
            p["first"] = ms
        elif kind == "audioout.stats":
            p["windows"] += 1
            p["buffers"] += int(kv["buffers"])
            p["loud"] += int(kv["loud"])
            p["peak"] = max(p["peak"], float(kv["peak"]))
            if int(kv["loud"]):
                p["loud_windows"] += 1
                p["last_loud"] = ms
    for port, p in sorted(ports.items(), key=lambda x: int(x[0])):
        pct = 100.0 * p["loud"] / p["buffers"] if p["buffers"] else 0.0
        first = mmss(p["first"]) if p["first"] is not None else "never"
        last = mmss(p["last_loud"]) if p["last_loud"] is not None else "-"
        print(f"  port {port:>2} {p['type']:8} buffers={p['buffers']:<8} with sound={pct:5.1f}%  peak={p['peak']:.3f}  "
              f"first sound {first}, last {last}  ({p['loud_windows']}/{p['windows']} five-second windows)")
    for ms, kind, kv, text in events:
        if kind in ("audioout.open", "audioout.open_failed", "audioout.backend", "audioout.output_failed",
                    "audioout.volume_rejected"):
            print(f"  {mmss(ms)} {kind}: {text}")
    volumes = [(ms, text) for ms, k, _, text in events if k == "audioout.volume"]
    if volumes:
        print(f"  {len(volumes)} volume calls; first and last:")
        for ms, text in (volumes[0], volumes[-1]):
            print(f"    {mmss(ms)} {text}")

    print("\n== Ngs2 ==")
    for ms, kind, kv, text in events:
        if kind in ("ngs2.system", "ngs2.rack"):
            print(f"  {mmss(ms)} {kind}: {text}")
    starts = [(ms, kv) for ms, k, kv, _ in events if k == "ngs2.start"]
    by_type = Counter((kv.get("type"), kv.get("major")) for _, kv in starts)
    print(f"  {len(starts)} logged starts (effects after the first 300 are only counted)")
    for (wave, major), count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    type {wave} major={major}: {count}")
    summaries = [(ms, kv) for ms, k, kv, _ in events if k == "ngs2.summary"]
    if summaries:
        last = summaries[-1][1]
        print(f"  voice starts over the run: total={last['starts']} major={last['major']} minor={last['minor']}")
        silent = [ms for ms, kv in summaries if float(kv["master_peak"]) < 0.001 and int(kv["sampler"]) > 0]
        print(f"  windows with sampler voices playing but a silent mix: {len(silent)} of {len(summaries)}")
        buckets = defaultdict(lambda: [0, 0.0, 0])
        for ms, kv in summaries:
            b = buckets[ms // 60000]
            b[0] += 1
            b[1] = max(b[1], float(kv["master_peak"]))
            b[2] = max(b[2], int(kv["sampler"]))
        print("  minute  windows  loudest mix  most sampler voices")
        for minute in sorted(buckets):
            w, peak, voices = buckets[minute]
            print(f"  {minute:>6}  {w:>7}  {peak:>11.3f}  {voices:>19}")
    print("\n  long sounds (major starts), first 60:")
    shown = 0
    for ms, kv in starts:
        if kv.get("major") == "1" and shown < 60:
            shown += 1
            print(f"    {mmss(ms)} voice {kv.get('voice')}: type {kv.get('type')} ch={kv.get('channels')} "
                  f"rate={kv.get('rate')} bytes={kv.get('bytes')} streaming={kv.get('streaming')} "
                  f"routes={[k + '->' + v for k, v in kv.items() if re.fullmatch(r'p\d+->.*', k)]}")
    unrouted = Counter()
    for ms, kind, kv, _ in events:
        if kind == "ngs2.voice" and kv.get("routed") == "0":
            unrouted[(kv.get("voice"), kv.get("type"))] += 1
    if unrouted:
        print("\n  voices playing with NO route to the output (voice, type: summary windows):")
        for (voice, wave), count in unrouted.most_common(20):
            print(f"    voice {voice} {wave}: {count}")

    print("\n== Problems the runtime noticed ==")
    problem_kinds = ("ngs2.unsupported_waveform", "ngs2.at9_no_config", "ngs2.at9_init_failed", "ngs2.at9_decode_failed",
                     "ngs2.at9_no_audio", "ngs2.blocks_truncated", "ngs2.parse_data_failed", "ngs2.calc_block",
                     "ngs2.param_not_applied", "log.limit_reached")
    found = False
    for ms, kind, kv, text in events:
        if kind in problem_kinds:
            found = True
            print(f"  {mmss(ms)} {kind}: {text}")
    if not found:
        print("  none")

    print("\n== AvPlayer (video sound) ==")
    for ms, kind, kv, text in events:
        if kind.startswith("avplayer.") and kind != "avplayer.audio_data":
            print(f"  {mmss(ms)} {kind}: {text}")
    data = [(ms, kv) for ms, k, kv, _ in events if k == "avplayer.audio_data"]
    if data:
        print(f"  {len(data)} audio-data samples; last: {data[-1][1]}")

    print("\n== Sound files opened (first 80) ==")
    for ms, kind, kv, text in [e for e in events if e[1] in ("file.open", "ngs2.parse_file")][:80]:
        print(f"  {mmss(ms)} {kind}: {text}")


if __name__ == "__main__":
    main()
