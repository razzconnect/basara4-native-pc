"""Session reports: one file per game session that answers the questions a bug report always raises
(port and game version, machine, GPU/driver/Vulkan, settings, DLC/cheats, how the session ended, what
failed and where), so a user only has to send that file.

The launcher writes the report just before the game starts, so it exists even when the game never
writes a log line (exit 0xC0000139), and completes it when the game ends. Paths are masked: the
Windows user folder, the game folder and the program folder become <user>, <game> and <app>.

Everything here is plain data and text, with no window, so the tests drive it directly.
"""
import datetime
import json
import os
import re
import zipfile
from pathlib import Path

SCHEMA = 1

# The game probes for optional files and logs every miss. These are the misses healthy sessions show
# (50 session logs: optional character params, *_ex mission archives, stage .mic, sound banks,
# motion, HQ UI textures, stage .rtd). Kept narrow on purpose: a miss outside them, such as
# ui/gui/**, is reported as unusual.
NORMAL_NOT_FOUND = [re.compile(pattern) for pattern in (
    r"^/app0/nativePS4/actorParam/pl\d+/pl\d+\.pmc$",
    r"^/app0/nativePS4/s[ac]/PS4/[^/]+_ex\.(?:arc|spkg)$",
    r"^/app0/nativePS4/st/[^ ]+\.mic$",
    r"^/app0/nativePS4/st/st\d+/m\d+/st\d+_\d+\.rtd$",
    r"^/app0/nativePS4/sound/se/char/[^/]+/se_[^/]+\.(?:srqr|mse|ase)$",
    r"^/app0/nativePS4/sound/me/[^/]+\.stqr$",
    r"^/app0/nativePS4/motion/pl\d+/pl\d+_[^/]+\.lmt$",
    r"^/app0/nativePS4/ui/tex/jpn/[^/]+/[^/]+_HQ\.tex$",
)]
NOT_FOUND = re.compile(r"Runtime: open\((.*?)\) -> not found")
# What ends a session: a fault, a STOP from the runtime, or a failed assertion. Always shown first.
PRIMARY = re.compile(r"(Fault: .*|STOP: .*|Assertion Failed.*|ASSERT_MSG.*)")
FILE_TRACE = re.compile(r"^File trace: (.*) \((-?\d+) bytes\)$")
# A stubbed PS4 call is logged at <Error> level every time the game makes it (sceAvPlayerDisableStream
# in 40 of 48 healthy sessions). It is not what ends a session, so it is counted, not called fatal.
STUB_CALL = re.compile(r"\(STUBBED\) called\s*$")
FAULT = re.compile(r"^Fault: (?:(read|write|execute) access to (?:0x)?([0-9a-fA-F]+)|exception 0x([0-9a-fA-F]+)) "
                   r"at (.*?) \(thread \d+\)")
FRAME = re.compile(r"^\s+#(\d+) (.*)$")
NEAR_PRIMARY = 20  # not-found lines this close before a fault/STOP are always shown, whatever their kind
LAST_FILES = 10
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel", 0x13B5: "ARM", 0x5143: "Qualcomm"}


def unknown(reason):
    """A field that could not be read: kept, with the reason, never left out."""
    return {"unknown": str(reason)}


def is_normal_not_found(path):
    return any(pattern.match(path) for pattern in NORMAL_NOT_FOUND)


def where_of(text):
    """'g:0x…' for a guest offset, 'host:module+0x…' for a host module, 'raw' for a bare address."""
    m = re.match(r"guest offset (0x[0-9a-fA-F]+)", text)
    if m:
        return "g:" + m.group(1).lower()
    m = re.match(r"([^\s+]+)\+(0x[0-9a-fA-F]+)", text)
    if m:
        return f"host:{m.group(1).lower()}+{m.group(2).lower()}"
    return "raw"


def analyze_log(lines):
    """What a session log says about how the session ended. Pure: lines in, a dict out."""
    primary = [(i, PRIMARY.search(line).group(1).strip()) for i, line in enumerate(lines) if PRIMARY.search(line)]
    first = primary[0][0] if primary else None
    misses = [(i, m.group(1)) for i, line in enumerate(lines) for m in [NOT_FOUND.search(line)] if m]
    unusual, near_primary, hidden = [], [], 0
    for i, path in misses:
        if first is not None and first - NEAR_PRIMARY <= i < first:
            if path not in near_primary:  # shown whatever its kind: it may be what the crash followed
                near_primary.append(path)
        elif not is_normal_not_found(path):
            if path not in unusual:
                unusual.append(path)
        else:
            hidden += 1
    unusual += [p for p in near_primary if not is_normal_not_found(p) and p not in unusual]
    end = first if first is not None else len(lines)
    traced = [(m.group(1), int(m.group(2))) for line in lines[:end] for m in [FILE_TRACE.match(line)] if m]
    fault = None
    for i, line in enumerate(lines):
        m = FAULT.match(line)
        if m:
            frames = []
            for follow in lines[i + 1:i + 40]:
                f = FRAME.match(follow)
                if f:
                    frames.append(where_of(f.group(2)))
                elif frames:
                    break
            fault = {"kind": m.group(1) or f"exc:{m.group(3).upper().zfill(8)}",
                     "address": int(m.group(2), 16) if m.group(2) else None,
                     "where": where_of(m.group(4)), "frames": frames[:8]}
            break
    dump = next((m.group(1) for line in lines for m in [re.match(r"^Crash dump written: (.*)$", line)] if m), None)
    return {
        "lines": len(lines),
        "primary": [text for _, text in reversed(primary)],
        "fault": fault,
        "unusual_not_found": unusual,
        "near_primary_not_found": near_primary,
        "hidden_not_found": hidden,
        "last_files": [{"path": p, "bytes": b} for p, b in traced[-LAST_FILES:]],
        "files_traced": len(traced),
        "stub_calls": sum(1 for line in lines if STUB_CALL.search(line)),
        "crash_dump": dump,
        "present_summary": any(line.startswith("Present summary:") for line in lines),
        "gpu_used": next((line[len("GPU: using "):].strip() for line in lines if line.startswith("GPU: using ")), None),
        "patch_writes": next((line.strip() for line in lines if line.startswith("Patches: ")), None),
    }


def address_class(address):
    if address is None:
        return "na"
    return "null" if address < 0x1000 else "addr"


def signature(exit_code, analysis, build, gpu_vendor):
    """One line that is the same for the same failure on any machine and differs otherwise: no raw
    addresses, thread ids, registers, times or paths."""
    code = f"{exit_code & 0xFFFFFFFF:08X}" if isinstance(exit_code, int) else "none"
    tail = f"build={build} | gpu={gpu_vendor}"
    fault = (analysis or {}).get("fault")
    if fault:
        top = fault["where"]
        nxt = fault["frames"][0] if fault["frames"] else "-"
        return f"exit={code} | {fault['kind']}:{address_class(fault['address'])} | top={top} | next={nxt} | {tail}"
    primary = (analysis or {}).get("primary") or []
    if primary:
        return f"exit={code} | {re.sub(r'0x[0-9a-fA-F]+|[0-9]+', '#', primary[0])[:120]} | {tail}"
    return f"exit={code} | no fault | {tail}"


class Masker:
    """Replaces the user's folders and names in text. Longest paths first, any case, either slash."""

    def __init__(self, user_home=None, game_dir=None, app_dir=None, user_name=None, computer_name=None):
        pairs = []
        for path, token in ((game_dir, "<game>"), (app_dir, "<app>"), (user_home, "<user>")):
            if path:
                text = str(path).rstrip("\\/")
                if len(text) > 3:
                    pairs.append((text, token))
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        self.paths = [(re.compile("".join(r"[\\/]" if ch in "\\/" else re.escape(ch) for ch in text), re.I), token)
                      for text, token in pairs]
        # A bare account or computer name is only masked when long enough not to hit ordinary words.
        # Longest first: a computer name often contains the account name ("PC-<account>").
        names = sorted(((name, token) for name, token in ((user_name, "<user>"), (computer_name, "<pc>"))
                        if name and len(name) >= 3), key=lambda pair: len(pair[0]), reverse=True)
        self.names = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I), token)
                      for name, token in names]

    @classmethod
    def from_environment(cls, game_dir=None, app_dir=None):
        return cls(os.environ.get("USERPROFILE"), game_dir, app_dir, os.environ.get("USERNAME"),
                   os.environ.get("COMPUTERNAME"))

    def __call__(self, text):
        if not isinstance(text, str):
            return text
        for pattern, token in self.paths + self.names:
            text = pattern.sub(token, text)
        return text

    def data(self, value):
        if isinstance(value, dict):
            return {self(str(k)): self.data(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.data(v) for v in value]
        return self(value)


def write_atomic(path, text):
    """Write text to path so a reader sees the old file or the new one, never half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def new_report(stamp, launcher, game, system, vulkan, settings, content):
    return {"schema": SCHEMA, "status": "started", "started": stamp, "launcher": launcher, "game": game,
            "system": system, "vulkan": vulkan, "settings": settings, "content": content}


def finish_report(report, exit_code, stopped_by_user, forced, log_lines, hints, gpu_vendor, ended=None):
    report = dict(report)
    analysis = analyze_log(log_lines) if log_lines is not None else None
    build = "/".join(str(x) for x in (_plain(report.get("game", {}).get("app_ver")),
                                       _plain(report.get("game", {}).get("eboot_sha8")),
                                       _plain(report.get("launcher", {}).get("version"))))
    report.update({
        "status": "finished",
        "ended": ended or datetime.datetime.now().isoformat(timespec="seconds"),
        "end": {"exit_code": exit_code, "exit_hex": f"0x{exit_code & 0xFFFFFFFF:08X}" if isinstance(exit_code, int) else None,
                "stopped_by_user": bool(stopped_by_user), "forced": bool(forced),
                "crashed": isinstance(exit_code, int) and exit_code != 0 and not stopped_by_user},
        "log": analysis if analysis is not None else unknown("last_run.log could not be read"),
        "signature": signature(exit_code, analysis, build, gpu_vendor),
        "hints": list(hints or []),
    })
    return report


def _plain(value):
    return f"unknown({value['unknown']})" if isinstance(value, dict) and "unknown" in value else value


def summary_line(report):
    end = report.get("end") or {}
    log = report.get("log") if isinstance(report.get("log"), dict) and "unknown" not in report.get("log") else {}
    if report.get("status") != "finished":
        return "Sesi belum selesai saat laporan dibuat (launcher ditutup atau game masih jalan)."
    if end.get("stopped_by_user"):
        return "Dihentikan user" + (" (paksa)." if end.get("forced") else ".")
    if not end.get("crashed"):
        return "Game ditutup normal."
    if log.get("fault"):
        fault = log["fault"]
        return f"Crash: {fault['kind']} {address_class(fault['address'])} di {fault['where']}."
    if log.get("primary"):
        return f"Berhenti: {log['primary'][0]}"
    if not log.get("lines"):
        return f"Game berhenti sebelum menulis log (exit {end.get('exit_hex')})."
    return f"Game keluar dengan exit {end.get('exit_hex')} tanpa pesan error di log."


def render_text(report, tail_lines=None):
    """The report as text for people: the clipboard, the crash folder and the zip."""
    def value(v):
        if isinstance(v, dict) and "unknown" in v:
            return f"unknown: {v['unknown']}"
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    out = ["=== Basara 4 PC - Laporan sesi ===", f"Ringkasan     : {summary_line(report)}"]
    if report.get("signature"):
        out.append(f"Signature     : {report['signature']}")
    for title, key in (("Port", "launcher"), ("Game", "game"), ("Sistem", "system"), ("Vulkan", "vulkan"),
                       ("Setting", "settings"), ("Konten", "content"), ("Akhir sesi", "end")):
        section = report.get(key)
        if section is None:
            continue
        out.append(f"--- {title} ---")
        if isinstance(section, dict) and "unknown" not in section:
            out += [f"  {k}: {value(v)}" for k, v in section.items()]
        else:
            out.append(f"  {value(section)}")
    log = report.get("log")
    if isinstance(log, dict) and "unknown" not in log:
        out.append("--- Yang paling penting di log ---")
        out += [f"  * {line}" for line in log["primary"]] or ["  (tidak ada Fault/STOP/ASSERT)"]
        if log["fault"] and log["fault"]["frames"]:
            out.append("  frames: " + ", ".join(log["fault"]["frames"]))
        out.append("--- File tidak ditemukan yang tidak biasa ---")
        out += [f"  * {p}" for p in log["unusual_not_found"]] or ["  (tidak ada)"]
        if log["near_primary_not_found"]:
            out.append(f"--- File tidak ditemukan tepat sebelum berhenti ({NEAR_PRIMARY} baris terakhir; bisa wajar) ---")
            out += [f"  * {p}" for p in log["near_primary_not_found"]]
        out.append(f"  ({log['hidden_not_found']} not-found wajar disembunyikan)")
        if log["last_files"]:
            out.append(f"--- {len(log['last_files'])} file terakhir dibuka sebelum berhenti ---")
            out += [f"  {f['path']} ({f['bytes']} bytes)" for f in log["last_files"]]
        out.append(f"  log: {log['lines']} baris, present summary: {'ada' if log['present_summary'] else 'tidak ada'}"
                   + (f", dump: {log['crash_dump']}" if log["crash_dump"] else ""))
    elif log is not None:
        out.append(f"--- Log ---\n  {value(log)}")
    if report.get("hints"):
        out.append("--- Petunjuk ---")
        out += [f"  {h}" for h in report["hints"]]
    if tail_lines:
        out.append("--- Baris terakhir last_run.log ---")
        out += tail_lines
    out.append("=== Akhir laporan ===")
    return "\n".join(out) + "\n"


def tail_without_trace(lines, count=10):
    """The last log lines, leaving out the File trace lines that would crowd them out."""
    return [line for line in lines if not line.startswith("File trace: ")][-count:]


def make_zip(target, report, report_text, masker, logs=(), binaries=(), max_file_bytes=64 << 20):
    """One zip to send: report.json/.txt, masked text logs, and binary files (dump, capture) as they
    are. Missing files are skipped; files over max_file_bytes are left out and listed."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    skipped = []
    tmp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in logs:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                skipped.append(f"{name}: {exc.strerror or exc}")
                continue
            zf.writestr(name, masker(text))
        for name, path in binaries:
            try:
                size = Path(path).stat().st_size
            except OSError as exc:
                skipped.append(f"{name}: {exc.strerror or exc}")
                continue
            if size > max_file_bytes:
                skipped.append(f"{name}: {size} bytes, over the {max_file_bytes} byte limit")
                continue
            zf.write(path, name)
        note = report_text + ("\nTidak ikut:\n" + "\n".join(f"  {s}" for s in skipped) + "\n" if skipped else "")
        zf.writestr("report.txt", masker(note))
        zf.writestr("report.json", json.dumps(masker.data(report), indent=2, ensure_ascii=False))
    os.replace(tmp, target)
    return skipped


def mark_unfinished(folder):
    """Reports left 'started' by a launcher that closed before its game ended; returns their names."""
    names = []
    for path in sorted(Path(folder).glob("session-*.json")) if Path(folder).is_dir() else []:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if report.get("status") == "started":
            report["status"] = "unfinished"
            report["end"] = unknown("the launcher closed before the game ended")
            try:
                write_atomic(path, json.dumps(report, indent=2, ensure_ascii=False))
            except OSError:
                continue
            names.append(path.name)
    return names


def prune(folder, keep=20):
    """Keep the newest `keep` session reports and zips (both written by the launcher)."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    for pattern in ("session-*.json", "session-*.txt", "laporan-*.zip"):
        for old in sorted(folder.glob(pattern))[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
