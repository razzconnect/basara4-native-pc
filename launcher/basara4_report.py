"""Session reports: one file per game session that answers the questions a bug report always raises
(port and game version, machine, GPU/driver/Vulkan, settings, DLC/cheats, how the session ended, what
failed and where), so a user only has to send that file.

The launcher writes the report just before the game starts, so it exists even when the game never
writes a log line (exit 0xC0000139), and completes it when the game ends. Paths are masked: the
Windows user folder, the game folder and the program folder become <user>, <game> and <app>.

Everything here is plain data and text, with no window, so the tests drive it directly.
"""
import datetime
import io
import json
import locale
import os
import re
import time
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

SCHEMA = 1
# What people read on the error path (crash window, report, warnings before the game starts) is in English
# only, so players anywhere can read it and share it.

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


# Log lines that read like errors and are not. They are counted and explained in the report, so the team does not
# chase them; the lines themselves stay in last_run.log (and in the tail of it that the report shows).
KNOWN_HARMLESS = (
    ("HLE thread: join failed", re.compile(r"HLE thread: join failed"),
     "a game thread is waited for at most 5 s and then let go (on Windows a join can fail while the thread is "
     "fine); it shows up in most sessions, around the movies, and is not a crash"),
)


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
    harmless = {name: n for name, pattern, _why in KNOWN_HARMLESS
                for n in [sum(1 for line in lines if pattern.search(line))] if n}
    return {
        "lines": len(lines),
        "known_harmless": harmless,
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


MASK_TOKEN = re.compile(r"<(?:user|pc|game|app|dlc|folder|path)>")


def _nfc(text):
    return unicodedata.normalize("NFC", str(text))


def _plain_text(text):
    """Lower case without accents, for comparing a name with text that spells it another way."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class PrivacyError(Exception):
    """The report still held the account or computer name after masking: it is not written."""


class Masker:
    """Replaces the user's folders and names in text. Longest paths first, any case, either slash."""

    def __init__(self, user_home=None, game_dir=None, app_dir=None, user_name=None, computer_name=None,
                 folders=()):
        pairs, seen = [], set()
        # folders: more (path, token) pairs, such as every folder the user chose and the folders holding
        # them. A path given twice keeps its first token; a drive root alone ("D:\") is never masked.
        for path, token in ((game_dir, "<game>"), (app_dir, "<app>"), (user_home, "<user>"), *folders):
            if path:
                text = _nfc(str(path).rstrip("\\/"))
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
        names = sorted(((_nfc(name), token) for name, token in ((user_name, "<user>"), (computer_name, "<pc>"))
                        if name and len(name) >= 3), key=lambda pair: len(pair[0]), reverse=True)
        self.names = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I), token)
                      for name, token in names]
        # A one or two letter name cannot be told from ordinary text, but it can when it is a whole folder
        # name inside a path ("D:\Backup\Jo\x"): that form is masked, so such a name does not slip through.
        self.short_names = [(re.compile(r"(?<=[\\/])" + re.escape(_nfc(name)) + r"(?=[\\/])", re.I), token)
                            for name, token in ((user_name, "<user>"), (computer_name, "<pc>"))
                            if name and len(name) < 3]
        # What the leak check looks for: the same names, compared without case and without accents.
        self.watched = [(_plain_text(name), token) for name, token in names]

    @classmethod
    def from_environment(cls, game_dir=None, app_dir=None, folders=()):
        return cls(os.environ.get("USERPROFILE"), game_dir, app_dir, os.environ.get("USERNAME"),
                   os.environ.get("COMPUTERNAME"), folders)

    def __call__(self, text):
        if not isinstance(text, str):
            return text
        text = _nfc(text)  # "ü" written as one character or as u + a combining mark is the same name
        for pattern, token in self.paths + self.names + self.short_names:
            text = pattern.sub(token, text)
        return text

    def leaks(self, text):
        """The tokens ("<user>", "<pc>") of the account or computer name still readable in text, which must
        be nothing after masking. Looser than the masking itself (accents and case are ignored), so a form
        the masking missed is found here and the zip is refused instead of sent."""
        clean = _plain_text(MASK_TOKEN.sub(" ", text))
        return sorted({token for name, token in self.watched
                       if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", clean)})

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
        return ("The session had not ended when the report was made (launcher closed or game "
                              "still running).")
    if end.get("stopped_by_user"):
        if end.get("forced"):
            return "Stopped by the user (forced)."
        return "Stopped by the user."
    if not end.get("crashed"):
        return "The game closed normally."
    if log.get("fault"):
        fault = log["fault"]
        what = f"{fault['kind']} {address_class(fault['address'])}"
        return f"Crash: {what} at {fault['where']}."
    if log.get("primary"):
        return "Stopped" + f": {log['primary'][0]}"
    if not log.get("lines"):
        return f"The game stopped before writing a log (exit {end.get('exit_hex')})."
    return (f"The game exited with code {end.get('exit_hex')} and no error message in the log. "
            "No fault was logged, so it may have been closed from outside (Task Manager, taskkill) "
            "or ended without a crash report.")


def render_text(report, tail_lines=None, sections=()):
    """The report as text for people: the session report, the crash folder and the zip. `sections` are
    extra (title, text) parts placed before the end, such as the render trace summary in the zip."""
    def value(v):
        if isinstance(v, dict) and "unknown" in v:
            return f"unknown: {v['unknown']}"
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    out = ["=== Basara 4 PC - Session report ===",
           f"Summary       : {summary_line(report)}"]
    if report.get("signature"):
        out.append(f"Signature     : {report['signature']}")
    note = report.get("user_note")
    if isinstance(note, dict):
        # first, so the team reads what the user saw before the technical parts; it is the user's own text
        out.append("--- What the user wrote (typed by the user, not checked) ---")
        if not note.get("asked"):
            out.append("  (not asked in this report)")
        elif not note.get("text"):
            out.append("  (left empty)")
        else:
            out += [f"  {line}" for line in str(note["text"]).split("\n")]
            if note.get("cut"):
                out.append(f"  (cut at {note.get('limit', NOTE_MAX_CHARS)} characters)")
    for title, key in (("Port", "launcher"), ("Game", "game"), ("System", "system"),
                       ("Vulkan", "vulkan"), ("Settings", "settings"), ("Tracer", "tracer"),
                       ("Content", "content"),
                       ("Session end", "end")):
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
        none = "  (none)"
        out.append("--- Most important in the log ---")
        out += [f"  * {line}" for line in log["primary"]] or [
            "  (no Fault/STOP/ASSERT)"]
        if log["fault"] and log["fault"]["frames"]:
            out.append("  frames: " + ", ".join(log["fault"]["frames"]))
        out.append("--- Unusual files not found ---")
        out += [f"  * {p}" for p in log["unusual_not_found"]] or [none]
        if log.get("near_primary_stubs"):
            out.append("--- Unsupported PS4 functions (STUBBED) called just before the stop (unusual) ---")
            out += [f"  * {name}" for name in log["near_primary_stubs"]]
        if log.get("stubs"):
            out.append("  stubs called: "
                       + ", ".join(f"{name} x{count}" for name, count in log["stubs"].items()))
        if log["near_primary_not_found"]:
            out.append(f"--- Files not found just before the stop (last {NEAR_PRIMARY} lines; can be normal) ---")
            out += [f"  * {p}" for p in log["near_primary_not_found"]]
        out.append(f"  ({log['hidden_not_found']} normal not-found hidden)")
        if log.get("known_harmless"):
            out.append("--- Known and harmless (the lines stay in last_run.log) ---")
            for name, count in log["known_harmless"].items():
                why = next((text for known, _pattern, text in KNOWN_HARMLESS if known == name), "")
                out.append(f"  * {name} x{count}: {why}")
        if log["last_files"]:
            out.append(f"--- last {len(log['last_files'])} files opened before the stop ---")
            out += [f"  {f['path']} ({f['bytes']} bytes)" for f in log["last_files"]]
        present = "yes" if log["present_summary"] else "no"
        out.append(f"  log: {log['lines']} lines, present summary: {present}"
                   + (f", dump: {log['crash_dump']}" if log["crash_dump"] else ""))
    elif log is not None:
        out.append(f"--- Log ---\n  {value(log)}")
    if report.get("hints"):
        out.append("--- Hints ---")
        out += [f"  {h}" for h in report["hints"]]
    if tail_lines:
        out.append("--- Last lines of last_run.log ---")
        out += tail_lines
    for title, body in sections:
        out.append(f"--- {title} ---")
        out += body.splitlines()
    out.append("=== End of report ===")
    return "\n".join(out) + "\n"


def tail_without_trace(lines, count=10):
    """The last log lines, leaving out the File trace lines that would crowd them out."""
    return [line for line in lines if not line.startswith("File trace: ")][-count:]


ZIP_BUDGET_BYTES = 9 * 1024 * 1024  # a zip small enough to post on Discord; the exact limit is not verified
SESSION_SLACK_SECONDS = 2  # a file written this close before the session started still belongs to it
ZIP_ENTRY_OVERHEAD = 128  # file name (twice) and headers of one zip entry, with room to spare
ZIP_NOTE_RESERVE = 16 * 1024  # room for the "Not included" lines and headers of report.txt

# What the user typed in the Report window. Long enough to say what was seen and which keys were pressed, short
# enough that it stays a note and cannot become a way to paste a whole log (or a lot of private text).
NOTE_MAX_CHARS = 1000


def clean_note(text):
    """The note the user typed, ready for the report: line breaks and tabs kept, other control characters
    dropped, spaces around it trimmed, written the same way whatever way the keyboard produced it (NFC), and cut
    at NOTE_MAX_CHARS with a flag so the report can say so. None means the user was not asked (a crash report)."""
    if text is None:
        return {"asked": False}
    text = unicodedata.normalize("NFC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc").strip()
    return {"asked": True, "text": text[:NOTE_MAX_CHARS], "cut": len(text) > NOTE_MAX_CHARS, "limit": NOTE_MAX_CHARS}


def read_text(path):
    """A text file as text: UTF-8, or the Windows code page when the bytes are not valid UTF-8 (a name written
    in that code page must be readable to be masked); what neither can read is replaced, not dropped."""
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


# A path that is still a path after masking: a drive letter, and any folder up to the file name. Paths
# under the Windows and Program Files folders name no user and stay, as the driver's vulkan-1.dll does.
LEFTOVER_PATH = re.compile(r"(?<![\w<>])[A-Za-z]:[\\/]+(?:[^\\/\s\"'<>|*?]+[\\/]+)*([^\\/\s\"'<>|*?]*)")


def _system_roots():
    roots = (os.environ.get(name) for name in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                                               "ProgramData"))
    return tuple(r.rstrip("\\/").lower().replace("/", "\\") for r in roots if r)


def scrub_paths(text, keep=None):
    """Replaces every absolute path left in text by <path>\\<file name>, except paths under `keep` (default:
    the Windows and Program Files folders). Masking hides the user's own folders; this catches the rest."""
    keep = _system_roots() if keep is None else tuple(k.lower() for k in keep)

    def replace(match):
        shown = re.sub(r"[\\/]+", r"\\", match.group(0)).lower()
        if any(shown == k or shown.startswith(k + "\\") for k in keep):
            return match.group(0)
        return "<path>\\" + match.group(1)
    return LEFTOVER_PATH.sub(replace, text)


def _scrub_data(value):
    if isinstance(value, dict):
        return {scrub_paths(str(k)): _scrub_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_data(v) for v in value]
    return scrub_paths(value) if isinstance(value, str) else value


def _hidden(masker, name, text):
    """Text as it goes into the zip: masked, leftover paths scrubbed, and checked once more. Raises
    PrivacyError when the account or computer name is still readable."""
    shown = scrub_paths(masker(text))
    leaks = masker.leaks(shown)
    if leaks:
        raise PrivacyError(f"{name} still shows your {' and '.join('account name' if t == '<user>' else 'PC name' for t in leaks)} "
                           "after it was hidden")
    return shown


# The F11 pictures. A capture of a 1440p frame is a 7 MB PNG that one picture fills most of the zip; compressed for the
# zip only (the file in user\captures is never touched) several of them fit. First without any loss; when that
# is not enough, every colour value is rounded to fewer bits (never by more than a few of 255), the least that lets
# the pictures that matter fit, and report.txt says which and by how much.
PICTURE_LEVELS = (8, 7, 6, 5)  # bits kept per colour channel, best first; 8 is lossless
PICTURE_TARGET = 4  # how many of the leading pictures (the F11 ones) the level is chosen to fit
PICTURE_MIN_BYTES = 512 * 1024  # a smaller picture (an automatic capture) is not worth the time
PICTURE_MAX_PIXELS = 64_000_000  # larger than 8K: stored as it is rather than decoded into memory
PICTURE_MAX_ENCODES = 24  # most compressions in one zip; ~0.2-0.5 s each, so the Report never waits for long
PICTURE_COMPRESS_LEVEL = 6  # zlib: level 9 takes 8 times as long (2.3 s per 1440p picture) to save 6%


class PictureError(Exception):
    """A picture that cannot be compressed: it is stored as it is, and the report says why."""


def _load_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise PictureError(f"Pillow is not available ({exc})") from exc
    return Image


def compress_picture(path, bits=8):
    """The PNG at `path` written anew, smaller: (bytes, info). bits=8 loses nothing (a fully opaque alpha channel is
    dropped, the rows get PNG filters); fewer bits rounds each colour value to a multiple of 2**(8-bits) and keeps
    black and white exact, so no value changes by more than 2**(8-bits)-1. Nothing of the original file is copied
    but the pixels, so no metadata chunk comes along. PictureError when it cannot be done."""
    Image = _load_pillow()
    errors = (OSError, ValueError, SyntaxError, EOFError, zlib.error, Image.DecompressionBombError)
    try:
        original = Path(path).read_bytes()
        with Image.open(io.BytesIO(original)) as img:
            if img.format != "PNG":
                raise PictureError("it is not a PNG picture")
            width, height = img.size
            if width * height > PICTURE_MAX_PIXELS:
                raise PictureError(f"{width}x{height} is too many pixels to compress safely")
            img.load()
            mode = img.mode
            dropped = False
            if mode == "RGBA" and img.getchannel("A").getextrema() == (255, 255):
                work, dropped = img.convert("RGB"), True
            elif mode in ("RGB", "RGBA", "L"):
                work = img.copy()
            else:
                raise PictureError(f"picture mode {mode} is not handled")
        if bits < 8:
            shift = 8 - bits
            lut = [((i >> shift) << shift) | (i >> bits) for i in range(256)]  # black and white stay exact
            work = work.point(lut * 3 + list(range(256)) if work.mode == "RGBA" else lut * len(work.getbands()))
        buf = io.BytesIO()
        work.save(buf, "PNG", compress_level=PICTURE_COMPRESS_LEVEL)
    except PictureError:
        raise
    except errors as exc:
        raise PictureError(f"the picture cannot be read: {exc}") from exc
    data = buf.getvalue()
    info = {"method": "lossless" if bits == 8 else "near-lossless", "bits": bits,
            "max_change": 0 if bits == 8 else (1 << (8 - bits)) - 1, "alpha_dropped": dropped,
            "width": width, "height": height}
    if bits == 8 and len(data) >= len(original):
        return original, dict(info, method="unchanged")  # already as small as it gets: not rewritten
    return data, info


def _picture_line(name, info, before, after):
    if info["method"] == "lossless":
        return f"{name}: lossless, {before} -> {after} bytes" + (" (opaque alpha channel dropped)" if info["alpha_dropped"] else "")
    if info["method"] == "unchanged":
        return f"{name}: stored as it is, it is already as small as it gets"
    return (f"{name}: near-lossless, {info['bits']} bits per colour channel, no value changed by more than "
            f"{info['max_change']} of 255; finer detail than that is lost, {before} -> {after} bytes")


def _pack_binaries(entries, room, priority, limit_mb):
    """Which of `entries` (name, path, size), the screenshots in order of importance, go into the zip and as what.
    Returns (items, notes, infos, left_out): items are (name, path, data, mtime) with data None for "copy the file";
    notes and infos say what was done to the pictures (for report.txt and report.json); left_out are the
    "Not included" lines. The first `priority` entries are the ones that matter (the F11 pictures): one level is
    chosen for all of them, the best that lets them fit, and the pictures after them are added at that level
    while room is left. Without a room limit nothing is compressed."""
    def cost(name, nbytes):
        return nbytes + 2 * len(name) + ZIP_ENTRY_OVERHEAD

    def big(i):
        return entries[i][0].lower().endswith(".png") and entries[i][2] >= PICTURE_MIN_BYTES

    cache, count = {}, [0]

    def encoded(i, bits):
        if (i, bits) not in cache:
            if count[0] >= PICTURE_MAX_ENCODES:
                cache[i, bits] = PictureError("there are too many pictures to compress in one report")
            else:
                count[0] += 1
                try:
                    cache[i, bits] = compress_picture(entries[i][1], bits)
                except PictureError as exc:
                    cache[i, bits] = exc
        return cache[i, bits]

    lead = range(min(PICTURE_TARGET if priority is None else priority, len(entries)))
    wanted = [i for i in lead if big(i)][:PICTURE_TARGET] if room is not None else []
    level = PICTURE_LEVELS[-1]
    if wanted:
        for bits in PICTURE_LEVELS:
            total = sum(cost(entries[i][0], entries[i][2]) for i in lead if i not in wanted)
            for i in wanted:
                got = encoded(i, bits)
                total += cost(entries[i][0], entries[i][2] if isinstance(got, PictureError) else len(got[0]))
                if total > room:
                    break
            if total <= room:
                level = bits
                break
    items, notes, infos, left_out, remaining = [], [], [], [], room
    for i, (name, path, size) in enumerate(entries):
        options, why = [], None
        if room is not None and big(i):
            for bits in PICTURE_LEVELS:
                if bits > level:
                    continue
                got = encoded(i, bits)
                if isinstance(got, PictureError):
                    why = str(got)
                    break
                options.append(got)
        raw = (None, None)
        choices = ([(data, info, cost(name, len(data))) for data, info in options]
                   or [(raw[0], raw[1], cost(name, size))])
        picked = next((c for c in choices if remaining is None or c[2] <= remaining), None)
        if picked is None:
            left_out.append(f"{name}: {min(c[2] for c in choices)} bytes in the zip, over the {limit_mb} MB zip limit; "
                            "it stays on the PC")
            continue
        data, info, spent = picked
        remaining = None if remaining is None else remaining - spent
        if info is not None and info["method"] == "unchanged":
            data = None
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            mtime = time.time()
        items.append((name, path, data, mtime))
        if info is not None:
            notes.append(_picture_line(name, info, size, spent - 2 * len(name) - ZIP_ENTRY_OVERHEAD))
            infos.append({"file": name, "method": info["method"], "bits": info["bits"], "max_change": info["max_change"],
                          "alpha_dropped": info["alpha_dropped"], "from_bytes": size,
                          "to_bytes": spent - 2 * len(name) - ZIP_ENTRY_OVERHEAD})
        elif why:
            notes.append(f"{name}: stored as it is, it could not be compressed: {why}")
            infos.append({"file": name, "method": "stored as it is", "reason": why, "from_bytes": size})
    return items, notes, infos, left_out


def make_zip(target, report, report_text, masker, logs=(), binaries=(), withheld=(), max_file_bytes=64 << 20,
             max_total_bytes=None, priority=None):
    """One zip to send: report.json/.txt, masked text logs, and the screenshots. A big PNG is written anew, smaller
    (see compress_picture; the first `priority` pictures, the F11 ones, decide how much), and report.txt says so.
    Missing files are skipped; files over max_file_bytes are left out; both are listed, and so are the
    `withheld` notes (files kept on the PC on purpose, such as a crash dump, named but not included).

    `logs` then `binaries` are taken in the order given, which is the order of importance. With
    max_total_bytes the zip never grows past it: what does not fit is left out, whole, and listed in
    report.txt with its size, so a large screenshot never pushes a small text file out.
    Every text is masked, its leftover paths are scrubbed, and it is checked for the account and computer
    name; PrivacyError (nothing written) when one is still there."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    skipped = list(withheld)
    tmp = target.with_name(target.name + ".tmp")
    report_json = json.dumps(_scrub_data(masker.data(report)), indent=2, ensure_ascii=False)
    leaks = masker.leaks(report_json)
    if leaks:
        raise PrivacyError("report.json still shows your account or PC name after it was hidden")
    room = None
    if max_total_bytes is not None:
        room = (max_total_bytes - ZIP_NOTE_RESERVE - len(zlib.compress(report_json.encode("utf-8")))
                - len(zlib.compress(masker(report_text).encode("utf-8"))) - 2 * ZIP_ENTRY_OVERHEAD)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            def fits(name, cost):
                nonlocal room
                if room is not None and cost > room:
                    skipped.append(f"{name}: {cost} bytes in the zip, over the {max_total_bytes // (1 << 20)} MB zip "
                                   "limit; it stays on the PC")
                    return False
                room = None if room is None else room - cost
                return True
            for name, path in logs:
                try:
                    text = read_text(path)
                except OSError as exc:
                    skipped.append(f"{name}: {exc.strerror or exc}")
                    continue
                shown = _hidden(masker, name, text)
                if fits(name, len(zlib.compress(shown.encode("utf-8"))) + 2 * len(name) + ZIP_ENTRY_OVERHEAD):
                    zf.writestr(name, shown)
            entries = []
            for name, path in binaries:
                try:
                    size = Path(path).stat().st_size
                except OSError as exc:
                    skipped.append(f"{name}: {exc.strerror or exc}")
                    continue
                if size > max_file_bytes:
                    skipped.append(f"{name}: {size} bytes, over the {max_file_bytes} byte limit")
                    continue
                entries.append((name, path, size))
            items, picture_notes, picture_infos, left_out = _pack_binaries(
                entries, room, priority, (max_total_bytes or 0) // (1 << 20))
            skipped += left_out
            for name, path, data, mtime in items:
                if data is None:
                    zf.write(path, name)
                else:  # a picture written anew: PNG data is already compressed, so it is stored as it is
                    stamp = time.localtime(mtime)[:6]
                    info = zipfile.ZipInfo(name, date_time=stamp if stamp[0] >= 1980 else (1980, 1, 1, 0, 0, 0))
                    zf.writestr(info, data, compress_type=zipfile.ZIP_STORED)
            note = report_text
            if picture_notes:
                note += ("\nPictures in this zip (compressed for the zip only; the files in user\\captures are "
                         "not changed):\n" + "\n".join(f"  {s}" for s in picture_notes) + "\n")
                if any(i["method"] in ("lossless", "near-lossless") for i in picture_infos):
                    note += "  PNG metadata is not copied: each compressed picture is written anew.\n"
            if skipped:
                note += "\nNot included:\n" + "\n".join(f"  {s}" for s in skipped) + "\n"
            zf.writestr("report.txt", _hidden(masker, "report.txt", note))
            if picture_infos:
                report_json = json.dumps(_scrub_data(masker.data(dict(report, pictures=picture_infos))), indent=2,
                                         ensure_ascii=False)
                if masker.leaks(report_json):
                    raise PrivacyError("report.json still shows your account or PC name after it was hidden")
            zf.writestr("report.json", report_json)
    except BaseException:
        tmp.unlink(missing_ok=True)  # never leave half a zip behind
        raise
    os.replace(tmp, target)
    return skipped


class Artifacts(NamedTuple):
    """What one session left for the zip. logs: text files (zip name, path), most important first; binaries:
    screenshots, most important first; trace_log/trace_label: the render trace of this session; captures:
    names of this session's captures; notes: what could not be listed or read, for the "Not included" list;
    priority: how many of the leading screenshots are F11 captures (the ones the zip is sized to keep)."""
    logs: list
    binaries: list
    trace_log: object
    trace_label: str
    captures: list
    notes: list
    priority: int = 0


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def session_window(report, now=None):
    """(first, last) second a file may carry to belong to the session this report describes, or None when
    the report has no start. Written by the launcher into report["tracer"]; reports from before that were
    written with an ISO "started" and "ended"."""
    tracer = report.get("tracer") if isinstance(report.get("tracer"), dict) else {}
    start = tracer.get("started_epoch")
    if not isinstance(start, (int, float)):
        start = _epoch(report.get("started"))
    if start is None:
        return None
    end = tracer.get("ended_epoch")
    if not isinstance(end, (int, float)):
        end = _epoch(report.get("ended"))
    if end is None:  # still running, or the launcher closed first: up to now
        end = time.time() if now is None else now
    return start - SESSION_SLACK_SECONDS, end + SESSION_SLACK_SECONDS


CAPTURE_SAVED = re.compile(r"capture\.saved reason=(\S+) trace=.*?([^\\/]+)\.txt(?: screenshot=|\s*$)")


def collect_session_artifacts(report, *, traces_dir, captures_dir, fallback_trace_log, audio_log, run_log,
                              prepare_log, now=None):
    """The files of the session `report` describes, chosen by what the session says about itself (its trace
    folder, its start and end), never by "newest" or "latest N": a file from another session cannot get in.

    Order, most important first: the render trace, the text next to every capture, the audio log, the game
    log, the prepare log, then the screenshots (the F11 ones first). The same list serves the crash window,
    a session the launcher lost track of, and the Report button."""
    logs, binaries, notes, captures = [], [], [], []
    window = session_window(report, now)
    tracer = report.get("tracer") if isinstance(report.get("tracer"), dict) else {}

    def in_window(path):
        try:
            return window is not None and window[0] <= Path(path).stat().st_mtime <= window[1]
        except OSError:
            return False

    trace_log, label, reasons = None, "", {}
    folder = tracer.get("trace_folder")
    if isinstance(folder, str) and folder and not re.search(r"[\\/]", folder):
        candidate = Path(traces_dir) / folder / "render_trace.log"
        if candidate.is_file():
            trace_log, label = candidate, f"render_traces\\{folder}\\render_trace.log"
    elif tracer.get("on") and fallback_trace_log and in_window(fallback_trace_log):
        trace_log, label = Path(fallback_trace_log), "render_trace.log"
    if trace_log:
        logs.append(("render_trace.log", trace_log))
        try:
            for line in read_text(trace_log).splitlines():
                found = CAPTURE_SAVED.search(line)
                if found:
                    reasons[found.group(2)] = found.group(1)
        except OSError as exc:
            notes.append(f"{label}: {exc.strerror or exc}")
    texts, pictures = [], []
    if window is not None and captures_dir and Path(captures_dir).is_dir():
        try:
            entries = [p for p in Path(captures_dir).iterdir() if p.is_file() and in_window(p)]
        except OSError as exc:
            entries = []
            notes.append(f"user\\captures could not be listed: {exc.strerror or exc}")
        newest_first = sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)
        texts = [p for p in newest_first if p.suffix.lower() == ".txt"]
        pictures = [p for p in newest_first if p.suffix.lower() == ".png"]
        captures = [p.name for p in newest_first if p.suffix.lower() in (".txt", ".png")]
    logs += [(f"captures/{p.name}", p) for p in texts]
    if audio_log and in_window(audio_log):
        logs.append(("audio_debug.log", Path(audio_log)))
    for name, path in (("last_run.log", run_log), ("prepare.log", prepare_log)):
        if path and Path(path).is_file():
            logs.append((name, Path(path)))
    hotkey = [p for p in pictures if reasons.get(p.stem) == "hotkey_f11"]
    binaries = [(f"captures/{p.name}", p) for p in hotkey + [p for p in pictures if p not in hotkey]]
    return Artifacts(logs, binaries, trace_log, label, captures, notes, len(hotkey))


def mark_unfinished(folder):
    """Reports left 'started' by a launcher that closed before its game ended; returns their names. The end of
    such a session is fixed at the moment it is found (no game can have run between the old launcher closing
    and this one opening), so its window for files never stays open up to "now" and cannot reach into a
    later session."""
    names = []
    for path in sorted(Path(folder).glob("session-*.json")) if Path(folder).is_dir() else []:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if report.get("status") == "started":
            report["status"] = "unfinished"
            report["end"] = unknown("the launcher closed before the game ended")
            if not isinstance(report.get("tracer"), dict):
                report["tracer"] = {}  # a report from before the Tracer still gets an end
            if not isinstance(report["tracer"].get("ended_epoch"), (int, float)):
                report["tracer"]["ended_epoch"] = time.time()
            try:
                write_atomic(path, json.dumps(report, indent=2, ensure_ascii=False))
            except OSError:
                continue
            names.append(path.name)
    return names


KEEP_SESSIONS = 20  # session reports and zips the launcher keeps, and with them the Tracer files of those sessions


def prune(folder, keep=KEEP_SESSIONS):
    """Keep the newest `keep` session reports and zips (both written by the launcher)."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    # laporan-*.zip: the name the zips had before the messages were English only
    for pattern in ("session-*.json", "session-*.txt", "report-*.zip", "laporan-*.zip"):
        for old in sorted(folder.glob(pattern))[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass


# What the game and the launcher write into user\captures and user\render_traces, by exact name. Nothing else in
# those two folders is ever removed.
CAPTURE_NAME = re.compile(r"frame_[0-9]{8}-[0-9]{6}_[0-9]+\.(png|txt)", re.ASCII)
TRACE_FOLDER_NAME = re.compile(r"([0-9]{8}-[0-9]{6})(-[0-9]+)?", re.ASCII)
TRACE_FOLDER_FILES = frozenset(("render_trace.log", "launcher.json", "basara4.ini", "last_run.log"))


def _is_link(path):
    """A symbolic link or a junction: it may lead out of the folder, so it is never followed or removed."""
    try:
        return Path(path).is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))
    except OSError:
        return True  # cannot tell: treat it as something that must not be touched


@dataclass
class CleanResult:
    files: int = 0
    folders: int = 0
    bytes: int = 0
    failed: list = field(default_factory=list)  # one English line per thing that could not be read or removed
    skipped: str = ""  # why nothing was looked at, when that is the case
    problem: bool = False  # `skipped` is something the player should hear about, not just "nothing to do"

    def summary(self):
        """One short English line for the launcher status, or "" when there is nothing to tell."""
        if self.skipped and self.problem:
            return f"Tracer cleanup skipped: {self.skipped}."
        parts = []
        if self.files or self.folders:
            size = (f"{self.bytes / (1024 * 1024):.1f} MB" if self.bytes >= 1024 * 1024
                    else f"{max(1, round(self.bytes / 1024))} KB")
            parts.append(f"removed {self.files} file{'s' * (self.files != 1)} and "
                         f"{self.folders} folder{'s' * (self.folders != 1)} ({size})")
        if self.failed:
            parts.append(f"{len(self.failed)} could not be removed ({self.failed[0]})")
        return f"Tracer cleanup: {'; '.join(parts)}." if parts else ""


def clean_tracer_files(reports_dir, captures_dir, traces_dir, keep=KEEP_SESSIONS, now=None):
    """Remove the Tracer files that belong to no kept session: the F11 capture pairs in `captures_dir` and the
    per-run folders in `traces_dir`. Called once when the launcher starts (never while a game runs). Never raises:
    what cannot be read or removed is listed in the result and the rest goes on.

    Rule: a session stays as long as its report stays (the newest `keep` session reports, the same set prune()
    keeps), and so does everything the Tracer wrote for it. A session's files are found the way the report finds
    them (session_window: its started and ended time, and the trace folder it names), so the oldest kept session
    gives one line, `cutoff`: its start. A capture pair or trace folder is removed only when it is OLDER than the
    cutoff and no kept report points to it. Everything at or after the cutoff stays, even a file in a gap between
    two sessions, one written after a session was marked unfinished (its game may still be running from an earlier
    launcher) or one from the session being run now: when it is not certain that a file belongs to no kept
    session, it stays. Without a readable kept report there is no cutoff, and nothing is removed.

    Only exact names are touched: captures frame_<date>-<time>_<n>.png/.txt (a pair stays whole: it goes only
    when both are older, the picture first), and render_traces\\<date>-<time>[-n] folders that hold nothing but
    the four files the tracer puts there. Links, other names, other folders, the zips and reports are never
    touched."""
    result = CleanResult()
    try:
        names = sorted(Path(reports_dir).glob("session-*.json"))[-keep:] if Path(reports_dir).is_dir() else []
    except OSError as exc:
        result.failed.append(f"user\\reports could not be listed: {exc.strerror or exc}")
        result.skipped, result.problem = "the session reports could not be listed", True
        return result
    if not names:
        result.skipped = "no session report is kept, so nothing can be told apart"
        return result
    starts, pointed = [], set()
    for path in names:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            window = session_window(report, now)
        except (OSError, ValueError, AttributeError) as exc:
            result.failed.append(f"{path.name} cannot be read ({getattr(exc, 'strerror', None) or exc})")
            result.skipped, result.problem = "a kept session report cannot be read, so nothing was removed", True
            return result
        if window is None:
            result.failed.append(f"{path.name} has no start time")
            result.skipped, result.problem = "a kept session report has no start time, so nothing was removed", True
            return result
        starts.append(window[0])
        tracer = report.get("tracer") if isinstance(report.get("tracer"), dict) else {}
        if isinstance(tracer.get("trace_folder"), str):
            pointed.add(tracer["trace_folder"])
    cutoff = min(starts)

    def remove(path, folder=False):
        try:
            size = 0 if folder else path.stat().st_size
            path.rmdir() if folder else path.unlink()
        except OSError as exc:
            result.failed.append(f"{path.name}: {exc.strerror or exc}")
            return False
        result.bytes += size
        result.folders += folder
        result.files += not folder
        return True

    for folder, what in ((captures_dir, "captures"), (traces_dir, "render_traces")):
        folder = Path(folder)
        try:
            if not folder.is_dir() or _is_link(folder):
                continue
            entries = list(folder.iterdir())
        except OSError as exc:
            result.failed.append(f"user\\{what} could not be listed: {exc.strerror or exc}")
            continue
        if what == "captures":
            pairs = {}
            for entry in entries:
                if CAPTURE_NAME.fullmatch(entry.name) and entry.is_file():
                    pairs.setdefault(entry.stem, []).append(entry)
            for members in pairs.values():
                try:
                    old = not any(_is_link(m) for m in members) and max(m.stat().st_mtime for m in members) < cutoff
                except OSError:
                    continue
                if old:
                    for member in sorted(members, key=lambda m: m.suffix != ".png"):  # the picture first
                        if not remove(member):
                            break  # its text stays with it
            continue
        for entry in entries:
            match = TRACE_FOLDER_NAME.fullmatch(entry.name)
            if not match or entry.name in pointed or _is_link(entry) or not entry.is_dir():
                continue
            try:
                made = time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S"))
                inside = list(entry.iterdir())
            except (OSError, ValueError, OverflowError) as exc:
                result.failed.append(f"{entry.name}: {getattr(exc, 'strerror', None) or exc}")
                continue
            if made >= cutoff or any(i.name not in TRACE_FOLDER_FILES or _is_link(i) or not i.is_file() for i in inside):
                continue  # not older than every kept session, or something in it the tracer did not write
            if all([remove(i) for i in inside]):
                remove(entry, folder=True)
    return result
