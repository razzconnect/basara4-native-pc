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


# What people read on the error path (crash window, report, warnings before the game starts) is Indonesian
# first and English after it, so players outside Indonesia can read it too.
def bilingual(indonesian, english):
    """A message of one or more lines: the Indonesian text, an empty line, then the English text."""
    if not indonesian or not english or indonesian == english:
        raise ValueError("a message needs an Indonesian text and a different English text")
    return f"{indonesian}\n\n{english}"


def bilingual_line(indonesian, english):
    """A title, label or one-line message: "Indonesian / English"."""
    if not indonesian or not english or indonesian == english:
        raise ValueError("a message needs an Indonesian text and a different English text")
    return f"{indonesian} / {english}"

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
# Every form in the runtime sources (71 places): "(STUBBED) called" alone (68), and three with values after
# it: "called, time (msec) = {}", "called speed = {}", "called top = {}, bottom = {}".
STUB_CALL = re.compile(r"\(STUBBED\) called\b")
STUB_NAME = re.compile(r"(\w+): \(STUBBED\) called\b")
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
    # Stubbed calls: every name is kept with its count; one made just before the fault is shown like a miss.
    stubs, near_stubs = {}, []
    for i, line in enumerate(lines):
        if STUB_CALL.search(line):
            m = STUB_NAME.search(line)
            name = m.group(1) if m else line.strip()[:120]
            stubs[name] = stubs.get(name, 0) + 1
            if first is not None and first - NEAR_PRIMARY <= i < first and name not in near_stubs:
                near_stubs.append(name)
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
        "stub_calls": sum(stubs.values()),
        "stubs": stubs,
        "near_primary_stubs": near_stubs,
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
        # No machine's folders in a line meant to match across machines: the message is cut where the first
        # path starts (a folder name may hold spaces, so where a path ends cannot be told).
        text = primary[0]
        m = PATH_START.search(text)
        if m:
            text = text[:m.start()] + "<path>"
        return f"exit={code} | {re.sub(r'0x[0-9a-fA-F]+|[0-9]+', '#', text)[:120]} | {tail}"
    return f"exit={code} | no fault | {tail}"


# Where a path starts in a message: a drive ("D:\", "d:/"), a UNC or doubled separator, or "/app0/"-style.
PATH_START = re.compile(r"[A-Za-z]:[\\/]|[\\/]{2}|(?<![\w])/[\w.-]+/")


class Masker:
    """Replaces the user's folders and names in text. Longest paths first, any case, either slash."""

    def __init__(self, user_home=None, game_dir=None, app_dir=None, user_name=None, computer_name=None,
                 folders=()):
        pairs, seen = [], set()
        # folders: more (path, token) pairs, such as every folder the user chose and the folders holding
        # them. A path given twice keeps its first token; a drive root alone ("D:\") is never masked.
        for path, token in ((game_dir, "<game>"), (app_dir, "<app>"), (user_home, "<user>"), *folders):
            if path:
                text = str(path).rstrip("\\/")
                if len(text) > 3 and text.lower() not in seen:
                    seen.add(text.lower())
                    pairs.append((text, token))
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        # A separator matches one or more slashes of either kind: "D:\\Games\\x" as Python writes it in an
        # error message (str(exc)), "D:/Games/x", and "D:\\\\Games" as escaped again in JSON, any case.
        self.paths = [(re.compile("".join(r"[\\/]+" if ch in "\\/" else re.escape(ch) for ch in text), re.I), token)
                      for text, token in pairs]
        # A bare account or computer name is only masked when long enough not to hit ordinary words.
        # Longest first: a computer name often contains the account name ("PC-<account>").
        names = sorted(((name, token) for name, token in ((user_name, "<user>"), (computer_name, "<pc>"))
                        if name and len(name) >= 3), key=lambda pair: len(pair[0]), reverse=True)
        self.names = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I), token)
                      for name, token in names]

    @classmethod
    def from_environment(cls, game_dir=None, app_dir=None, folders=()):
        return cls(os.environ.get("USERPROFILE"), game_dir, app_dir, os.environ.get("USERNAME"),
                   os.environ.get("COMPUTERNAME"), folders)

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


ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")
KNOWN_FOLDER_TOKENS = {"game": "<game>", "dlc_dir": "<dlc>"}


def user_folders(settings, app_dir=None):
    """Every folder named in the settings (any key whose value is an absolute path: the game folder, the
    DLC folder, and any added later), and the folder holding each of them and the program, unless that is
    a drive root. All of these are the user's own folder names, to be masked."""
    folders, holders = [], []
    for key, value in (settings or {}).items():
        if isinstance(value, str) and ABSOLUTE_PATH.match(value):
            folders.append((value, KNOWN_FOLDER_TOKENS.get(key, "<folder>")))
            holders.append(value)
    if app_dir:
        holders.append(str(app_dir))
    for path in holders:
        parent = Path(path.rstrip("\\/")).parent
        if str(parent) != parent.anchor:
            folders.append((str(parent), "<folder>"))
    return folders


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
    """A build part for the signature: an unreadable value is the fixed word "unknown", never its reason
    (the reason may hold a path, and differs between machines)."""
    return "unknown" if isinstance(value, dict) and "unknown" in value else value


def reason(exc):
    """Why something could not be read, without the full path: the error and the file name only."""
    if isinstance(exc, OSError):
        name = Path(exc.filename).name if getattr(exc, "filename", None) else ""
        return f"{type(exc).__name__}: {exc.strerror or exc}" + (f" ({name})" if name else "")
    return f"{type(exc).__name__}: {exc}"


def summary_line(report):
    end = report.get("end") or {}
    log = report.get("log") if isinstance(report.get("log"), dict) and "unknown" not in report.get("log") else {}
    if report.get("status") != "finished":
        return bilingual_line("Sesi belum selesai saat laporan dibuat (launcher ditutup atau game masih jalan).",
                              "The session had not ended when the report was made (launcher closed or game "
                              "still running).")
    if end.get("stopped_by_user"):
        if end.get("forced"):
            return bilingual_line("Dihentikan user (paksa).", "Stopped by the user (forced).")
        return bilingual_line("Dihentikan user.", "Stopped by the user.")
    if not end.get("crashed"):
        return bilingual_line("Game ditutup normal.", "The game closed normally.")
    if log.get("fault"):
        fault = log["fault"]
        what = f"{fault['kind']} {address_class(fault['address'])}"
        return bilingual_line(f"Crash: {what} di {fault['where']}.", f"Crash: {what} at {fault['where']}.")
    if log.get("primary"):
        return bilingual_line("Berhenti", "Stopped") + f": {log['primary'][0]}"
    if not log.get("lines"):
        return bilingual_line(f"Game berhenti sebelum menulis log (exit {end.get('exit_hex')}).",
                              f"The game stopped before writing a log (exit {end.get('exit_hex')}).")
    return bilingual_line(f"Game keluar dengan exit {end.get('exit_hex')} tanpa pesan error di log.",
                          f"The game exited with exit {end.get('exit_hex')} and no error message in the log.")


def render_text(report, tail_lines=None):
    """The report as text for people: the clipboard, the crash folder and the zip."""
    def value(v):
        if isinstance(v, dict) and "unknown" in v:
            return f"unknown: {v['unknown']}"
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    out = ["=== Basara 4 PC - " + bilingual_line("Laporan sesi", "Session report") + " ===",
           f"{bilingual_line('Ringkasan', 'Summary')} : {summary_line(report)}"]
    if report.get("signature"):
        out.append(f"Signature     : {report['signature']}")
    for title, key in (("Port", "launcher"), ("Game", "game"), (bilingual_line("Sistem", "System"), "system"),
                       ("Vulkan", "vulkan"), (bilingual_line("Setting", "Settings"), "settings"),
                       (bilingual_line("Konten", "Content"), "content"),
                       (bilingual_line("Akhir sesi", "Session end"), "end")):
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
        none = "  (" + bilingual_line("tidak ada", "none") + ")"
        out.append("--- " + bilingual_line("Yang paling penting di log", "Most important in the log") + " ---")
        out += [f"  * {line}" for line in log["primary"]] or [
            "  (" + bilingual_line("tidak ada Fault/STOP/ASSERT", "no Fault/STOP/ASSERT") + ")"]
        if log["fault"] and log["fault"]["frames"]:
            out.append("  frames: " + ", ".join(log["fault"]["frames"]))
        out.append("--- " + bilingual_line("File tidak ditemukan yang tidak biasa", "Unusual files not found") + " ---")
        out += [f"  * {p}" for p in log["unusual_not_found"]] or [none]
        if log.get("near_primary_stubs"):
            out.append("--- " + bilingual_line(
                "Fungsi PS4 yang belum didukung (STUBBED), dipanggil tepat sebelum berhenti (tidak biasa)",
                "Unsupported PS4 functions (STUBBED) called just before the stop (unusual)") + " ---")
            out += [f"  * {name}" for name in log["near_primary_stubs"]]
        if log.get("stubs"):
            out.append("  " + bilingual_line("stub dipanggil", "stubs called") + ": "
                       + ", ".join(f"{name} x{count}" for name, count in log["stubs"].items()))
        if log["near_primary_not_found"]:
            out.append("--- " + bilingual_line(
                f"File tidak ditemukan tepat sebelum berhenti ({NEAR_PRIMARY} baris terakhir; bisa wajar)",
                f"Files not found just before the stop (last {NEAR_PRIMARY} lines; can be normal)") + " ---")
            out += [f"  * {p}" for p in log["near_primary_not_found"]]
        out.append("  (" + bilingual_line(f"{log['hidden_not_found']} not-found wajar disembunyikan",
                                          f"{log['hidden_not_found']} normal not-found hidden") + ")")
        if log["last_files"]:
            count = len(log["last_files"])
            out.append("--- " + bilingual_line(f"{count} file terakhir dibuka sebelum berhenti",
                                               f"last {count} files opened before the stop") + " ---")
            out += [f"  {f['path']} ({f['bytes']} bytes)" for f in log["last_files"]]
        present = (bilingual_line("ada", "yes") if log["present_summary"]
                   else bilingual_line("tidak ada", "no"))
        out.append(f"  log: {log['lines']} " + bilingual_line("baris", "lines") + f", present summary: {present}"
                   + (f", dump: {log['crash_dump']}" if log["crash_dump"] else ""))
    elif log is not None:
        out.append(f"--- Log ---\n  {value(log)}")
    if report.get("hints"):
        out.append("--- " + bilingual_line("Petunjuk", "Hints") + " ---")
        out += [f"  {h}" for h in report["hints"]]
    if tail_lines:
        out.append("--- " + bilingual_line("Baris terakhir last_run.log", "Last lines of last_run.log") + " ---")
        out += tail_lines
    out.append("=== " + bilingual_line("Akhir laporan", "End of report") + " ===")
    return "\n".join(out) + "\n"


def tail_without_trace(lines, count=10):
    """The last log lines, leaving out the File trace lines that would crowd them out."""
    return [line for line in lines if not line.startswith("File trace: ")][-count:]


def make_zip(target, report, report_text, masker, logs=(), binaries=(), withheld=(), max_file_bytes=64 << 20):
    """One zip to send: report.json/.txt, masked text logs, and binary files (a capture) as they are.
    Missing files are skipped; files over max_file_bytes are left out; both are listed, and so are the
    `withheld` notes (files kept on the PC on purpose, such as a crash dump, named but not included)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    skipped = list(withheld)
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
        note = report_text + ("\n" + bilingual_line("Tidak ikut", "Not included") + ":\n"
                              + "\n".join(f"  {s}" for s in skipped) + "\n" if skipped else "")
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
