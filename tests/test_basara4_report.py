"""Session reports (launcher/basara4_report.py) and the crash summary that reads the same log.

A report has to answer the team's questions without asking the user, so these tests check:
- healthy sessions show nothing fatal (negative corpus: lines from real healthy logs, paths masked);
- what ends a session (Fault/STOP/ASSERT) is always on top and never pushed out;
- the misses just before a crash are always shown, the usual ones are counted, not listed;
- the signature is the same for the same failure on any machine and differs otherwise;
- a session that never wrote a log line still has a complete report;
- no account name, computer name or user folder reaches a report or a zip.
All logs here are synthetic lines in the runtime's format, or the masked fixture.
"""
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl
from paths import ROOT

import basara4_report as rp

FIXTURE = ROOT / "tests" / "fixtures" / "report" / "healthy_session_lines.txt"
LEAK = "QA_LEAK_7f3a"  # a fake account name: it must never reach a report

ISSUE_10_LINES = [  # a character-intro crash: the patched archive lacked idrama00_ps4.gui
    "GPU: using NVIDIA GeForce RTX 5050",
    "Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found",
] + [f"File trace: /app0/nativePS4/rom/ui/pl/pl0{i:02d}/pl0{i:02d}.arc ({1000 + i} bytes)" for i in range(15)] + [
    "Runtime: open(/app0/nativePS4/ui/gui/idrama/idrama00_ps4.gui) -> not found",
    "Fault: read access to 0000000000000000 at guest offset 0xaebfb7 (thread 1234)",
    "  rax=0000000000000000 rbx=000000000d51afd0 rcx=0000000001af0000 rdx=0000000000001af0",
    "  rsi=000000000d51afc0 rdi=000000000b9b3bd0 rbp=000000000177e248 rsp=0000000010e9f800",
    "  #0 guest offset 0x5a1b20",
    "  #1 guest offset 0x4f00aa",
    r"Crash dump written: C:\Users\QA_LEAK_7f3a\Desktop\b4\user\crash\core-20261117-101010.dmp",
]
BUG_002_LINES = [
    "Fault: write access to 0000000000000010 at guest offset 0x9c859c (thread 77)",
    "  #0 guest offset 0x123456",
]


def summary(lines):
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "last_run.log"
        log.write_text("\n".join(lines), encoding="utf-8")
        return bl.extract_crash_summary(log)


def build(app_ver="01.02", sha="1a2b3c4d", version="1.0.9-beta"):
    return f"{app_ver}/{sha}/{version}"


class HealthySessionTests(unittest.TestCase):
    """Negative corpus: nothing from a healthy session may be called fatal or unusual."""

    def corpus(self):
        return [line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]

    def test_fixture_is_masked_and_not_empty(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("-> not found"), 29)
        self.assertNotIn("Users\\", text)

    def test_healthy_lines_are_never_fatal(self):
        s = summary(self.corpus())
        self.assertEqual(s["detected_errors"], ["Tidak ada fatal signature terdaftar (game keluar mendadak). / "
                         "No known fatal signature (the game exited suddenly)."])
        self.assertEqual(s["unusual_not_found"], [])
        self.assertEqual(s["hidden_not_found"], sum("-> not found" in line for line in self.corpus()))

    def test_each_healthy_miss_is_normal(self):
        for line in self.corpus():
            m = rp.NOT_FOUND.search(line)
            if m:
                self.assertTrue(rp.is_normal_not_found(m.group(1)), line)

    def test_stub_calls_are_counted_not_fatal(self):
        analysis = rp.analyze_log(self.corpus())
        self.assertGreaterEqual(analysis["stub_calls"], 1)
        self.assertEqual(analysis["primary"], [])

    def test_unusual_misses_stay_unusual(self):
        # Kept narrow: a GUI layout, any other archive, or a file outside the known folders is unusual.
        for path in ("/app0/nativePS4/ui/gui/idrama/idrama00_ps4.gui", "/app0/nativePS4/rom/ui/pl/pl012/pl012.arc",
                     "/app0/nativePS4/sa/PS4/m512.arc", "/app0/nativePS4/actorParam/pl008/pl008.arc",
                     "/app0/nativePS4/ui/tex/jpn/cd/cd039_002_00_ID.tex", "/app0/x"):
            self.assertFalse(rp.is_normal_not_found(path), path)

    @unittest.skipUnless(os.environ.get("B4_REPORT_CORPUS"), "set B4_REPORT_CORPUS to a folder of real healthy last_run.log files")
    def test_real_healthy_logs(self):
        logs = list(Path(os.environ["B4_REPORT_CORPUS"]).rglob("last_run.log"))
        self.assertTrue(logs)
        for log in logs:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            if any(rp.PRIMARY.search(line) for line in lines):
                continue
            self.assertEqual(bl.extract_crash_summary(log)["detected_errors"][0][:9], "Tidak ada", str(log))


class CrashLogTests(unittest.TestCase):
    """Golden set: real crash shapes (issue #10, BUG-002, STOP, ASSERT)."""

    def test_issue_10_fault_on_top_with_the_missing_gui(self):
        s = summary(ISSUE_10_LINES)
        self.assertTrue(s["detected_errors"][0].startswith("Fault: read access to 0000000000000000 at guest offset 0xaebfb7"))
        self.assertIn("/app0/nativePS4/ui/gui/idrama/idrama00_ps4.gui", s["unusual_not_found"])
        a = rp.analyze_log(ISSUE_10_LINES)
        self.assertEqual(a["fault"], {"kind": "read", "address": 0, "where": "g:0xaebfb7",
                                      "frames": ["g:0x5a1b20", "g:0x4f00aa"]})
        self.assertEqual([f["path"] for f in a["last_files"]],
                         [f"/app0/nativePS4/rom/ui/pl/pl0{i:02d}/pl0{i:02d}.arc" for i in range(5, 15)])
        self.assertTrue(a["crash_dump"].endswith("core-20261117-101010.dmp"))
        self.assertEqual(a["gpu_used"], "NVIDIA GeForce RTX 5050")

    def test_stop_and_assert_stay_on_top_of_a_hundred_misses(self):
        misses = ["Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found"] * 100
        stop = "STOP: this CPU lacks MOVBE and no rewrite table matches the image"
        self.assertEqual(summary(misses + [stop] + misses)["detected_errors"][0], stop)
        assertion = "GPU [Debug] <Critical> Assertion Failed!"
        self.assertEqual(summary(misses + [assertion] + misses + ["GPU [Lib.Pad] <Error> x"])["detected_errors"][0],
                         assertion)

    def test_misses_just_before_a_crash_are_always_shown(self):
        normal = "Runtime: open(/app0/nativePS4/actorParam/pl013/pl013.pmc) -> not found"
        far = "Runtime: open(/app0/nativePS4/actorParam/pl001/pl001.pmc) -> not found"
        lines = [far] + ["Runtime: line"] * 50 + [normal] + ["Runtime: line"] * 3 + BUG_002_LINES
        a = rp.analyze_log(lines)
        self.assertEqual(a["near_primary_not_found"], ["/app0/nativePS4/actorParam/pl013/pl013.pmc"])
        self.assertEqual(a["unusual_not_found"], [])  # shown as "just before", not mislabelled unusual
        self.assertEqual(a["hidden_not_found"], 1)  # the far one is counted
        self.assertIn("pl013.pmc", rp.render_text(rp.finish_report({}, 0xC0000005, False, False, lines, [], "NVIDIA")))

    def test_last_files_are_the_ones_before_the_fault(self):
        lines = ([f"File trace: /app0/a{i} (1 bytes)" for i in range(3)] + BUG_002_LINES
                 + ["File trace: /app0/after (1 bytes)"])
        self.assertEqual([f["path"] for f in rp.analyze_log(lines)["last_files"]], ["/app0/a0", "/app0/a1", "/app0/a2"])
        self.assertEqual(bl.extract_crash_summary.__defaults__[0], 10)
        s = summary(lines + [f"File trace: /app0/t{i} (1 bytes)" for i in range(30)])
        self.assertNotIn("File trace", s["tail_context"])  # trace lines do not crowd the last lines out

    def test_host_module_fault(self):
        a = rp.analyze_log(["Fault: read access to 0000000000000000 at ntdll.dll+0x534e1 (thread 24304)"])
        self.assertEqual(a["fault"]["where"], "host:ntdll.dll+0x534e1")
        a = rp.analyze_log(["Fault: exception 0xc0000409 at basara4-core.exe+0x1f00 (thread 1)"])
        self.assertEqual((a["fault"]["kind"], a["fault"]["address"]), ("exc:C0000409", None))


class SignatureTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(), "NVIDIA"),
                         "exit=C0000005 | read:null | top=g:0xaebfb7 | next=g:0x5a1b20 | build=01.02/1a2b3c4d/1.0.9-beta"
                         " | gpu=NVIDIA")

    def test_same_failure_same_signature(self):
        other = [line.replace("thread 1234", "thread 9").replace("0000000000000000 at", "0000000000000008 at")
                 .replace("rax=0000000000000000", "rax=00000000deadbeef")
                 for line in ISSUE_10_LINES]
        other = ["Runtime: open(/app0/nativePS4/st/st001/st001-start-00.mic) -> not found"] * 7 + other
        other = [line.replace("QA_LEAK_7f3a", "someone") for line in other]
        self.assertEqual(rp.signature(-1073741819, rp.analyze_log(other), build(), "NVIDIA"),  # signed exit form
                         rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(), "NVIDIA"))

    def test_different_failures_differ(self):
        base = rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(), "NVIDIA")
        variants = [
            rp.signature(0xC0000005, rp.analyze_log(BUG_002_LINES), build(), "NVIDIA"),
            rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(app_ver="01.00"), "NVIDIA"),
            rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(sha="ffffffff"), "NVIDIA"),
            rp.signature(0xC0000005, rp.analyze_log(ISSUE_10_LINES), build(), "AMD"),
            rp.signature(0xC0000409, rp.analyze_log(ISSUE_10_LINES), build(), "NVIDIA"),
        ]
        self.assertEqual(len(set(variants + [base])), 6)

    def test_without_a_fault(self):
        self.assertEqual(rp.signature(0xC0000139, rp.analyze_log([]), build(), "none"),
                         "exit=C0000139 | no fault | build=01.02/1a2b3c4d/1.0.9-beta | gpu=none")
        stop = rp.signature(1, rp.analyze_log(["STOP: first unsupported PS4 import: sceFoo (index 12)"]), build(), "AMD")
        self.assertIn("STOP: first unsupported PS# import: sceFoo (index #)", stop)


def started_report():
    return rp.new_report("2026-11-17T10:00:00", {"version": "1.0.9-beta", "core_sha8": "aaaabbbb"},
                         {"app_ver": "01.02", "eboot_sha8": "1a2b3c4d", "title_id": "CUSA01159"},
                         {"os": "Windows 10 (build 19045)", "cpu": "Intel(R) Core(TM) i3-5005U"},
                         {"loader": "1.0", "check": "old"}, {"resolution": "720p"}, {"dlc": [], "cheats": []})


class ReportTests(unittest.TestCase):
    def started(self):
        return started_report()

    def test_failed_before_any_log_line(self):
        report = rp.finish_report(self.started(), 0xC0000139, False, False, [], ["hint"], "none")
        self.assertEqual(report["status"], "finished")
        self.assertTrue(report["end"]["crashed"])
        self.assertEqual(report["end"]["exit_hex"], "0xC0000139")
        self.assertEqual(report["log"]["lines"], 0)
        self.assertIn("sebelum menulis log", rp.summary_line(report))
        text = rp.render_text(report)
        for needle in ("01.02", "i3-5005U", "loader: 1.0", "check: old", "resolution: 720p", "0xC0000139", "hint"):
            self.assertIn(needle, text)

    def test_unreadable_parts_say_why(self):
        report = rp.finish_report(self.started(), 0, False, False, None, [], "NVIDIA")
        self.assertEqual(report["log"], {"unknown": "last_run.log could not be read"})
        self.assertIn("unknown: last_run.log could not be read", rp.render_text(report))
        report["game"]["app_ver"] = rp.unknown("param.sfo not readable: x")
        self.assertIn("app_ver: unknown: param.sfo not readable: x", rp.render_text(report))

    def test_how_the_session_ended(self):
        def line(code, stopped, forced):
            return rp.summary_line(rp.finish_report(self.started(), code, stopped, forced, ["Runtime: ok"], [], "x"))
        self.assertEqual(line(0, False, False), "Game ditutup normal. / The game closed normally.")
        self.assertEqual(line(1, True, True), "Dihentikan user (paksa). / Stopped by the user (forced).")
        self.assertEqual(line(1, True, False), "Dihentikan user. / Stopped by the user.")
        self.assertIn("tanpa pesan error", line(5, False, False))
        self.assertIn("no error message in the log", line(5, False, False))
        self.assertIn("sesi belum selesai", rp.summary_line(self.started()).lower())
        self.assertIn("the session had not ended", rp.summary_line(self.started()).lower())

    def test_write_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "r" / "session.json"
            rp.write_atomic(target, '{"old": 1}')
            with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    rp.write_atomic(target, '{"new": 2}')
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"old": 1})

    def test_unfinished_sessions_are_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rp.write_atomic(folder / "session-1.json", json.dumps(self.started()))
            rp.write_atomic(folder / "session-2.json",
                            json.dumps(rp.finish_report(self.started(), 0, False, False, [], [], "x")))
            self.assertEqual(rp.mark_unfinished(folder), ["session-1.json"])
            self.assertEqual(json.loads((folder / "session-1.json").read_text(encoding="utf-8"))["status"], "unfinished")
            self.assertEqual(rp.mark_unfinished(folder), [])


class PrivacyTests(unittest.TestCase):
    def test_no_account_computer_or_folder_names_in_report_or_zip(self):
        home = rf"C:\Users\{LEAK}"
        game = rf"D:\GAMES {LEAK}\Basara4"
        masker = rp.Masker(home, game, rf"{home}\Desktop\b4", LEAK, f"PC-{LEAK}")
        lines = ISSUE_10_LINES + [rf"Settings: {home}\Desktop\b4\user\basara4.ini",
                                  rf"io_file.cpp:202 Open: path={game}\sce_sys\x on PC-{LEAK} as {LEAK.lower()}",
                                  f"forward {game.replace(chr(92), '/')}/eboot.bin"]
        report = rp.finish_report(started_report(), 0xC0000005, False, False, lines, [], "NVIDIA")
        report["game"]["folder"] = game
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "last_run.log"
            log.write_text("\n".join(lines), encoding="utf-8")
            dump = Path(tmp) / "core.dmp"
            dump.write_bytes(b"MDMP" + b"\0" * 60)
            target = Path(tmp) / "laporan.zip"
            skipped = rp.make_zip(target, report, rp.render_text(report, lines[-10:]), masker,
                                  logs=[("last_run.log", log), ("missing.log", Path(tmp) / "nope.log")],
                                  binaries=[("crash/core.dmp", dump)])
            self.assertEqual(len(skipped), 1)
            with zipfile.ZipFile(target) as zf:
                names = sorted(zf.namelist())
                self.assertEqual(names, ["crash/core.dmp", "last_run.log", "report.json", "report.txt"])
                for name in ("last_run.log", "report.json", "report.txt"):
                    text = zf.read(name).decode("utf-8")
                    self.assertNotIn(LEAK.lower(), text.lower(), name)
                    self.assertIn("<app>", text, name)  # the crash dump path, inside the program folder
                self.assertIn("<game>", zf.read("report.json").decode("utf-8"))
                log_text = zf.read("last_run.log").decode("utf-8")
                for token in ("<game>", "<pc>", "<user>"):  # game folder, computer, account
                    self.assertIn(token, log_text)
            self.assertFalse(Path(str(target) + ".tmp").exists())

    def test_short_names_are_not_masked_everywhere(self):
        masker = rp.Masker(None, None, None, "ab", None)
        self.assertEqual(masker("tab ab cab"), "tab ab cab")

    def test_paths_in_every_written_form_and_case(self):
        game = r"D:\GAMES QAFOLDER 7f3a\Basara4"
        masker = rp.Masker(None, game, None, None, None)
        forms = [
            r"[Errno 2] No such file or directory: 'D:\\GAMES QAFOLDER 7f3a\\Basara4\\sce_sys\\param.sfo'",  # str(exc)
            json.dumps({"path": game + r"\sce_sys"}),  # escaped again in JSON
            "d:/games qafolder 7F3A/basara4/eboot.bin",  # forward slashes, other case
            r"D:\\\\GAMES QAFOLDER 7f3a\\\\Basara4",  # escaped twice
            r"D:\GAMES QAFOLDER 7f3a\Basara4\\",  # trailing separators
        ]
        for text in forms:
            masked = masker(text)
            self.assertNotIn("qafolder", masked.lower(), text)
            self.assertIn("<game>", masked, text)

    def test_unknown_reasons_never_reach_the_signature(self):
        def finished(folder):
            report = started_report()
            report["game"]["app_ver"] = rp.unknown(f"param.sfo not readable: [Errno 2] '{folder}\\sce_sys\\param.sfo'")
            return rp.finish_report(report, 0xC0000005, False, False, BUG_002_LINES, [], "NVIDIA")
        one, two = finished(r"D:\GAMES QAFOLDER 7f3a"), finished(r"E:\Other\Place")
        self.assertEqual(one["signature"], two["signature"])
        self.assertIn("build=unknown/1a2b3c4d/1.0.9-beta", one["signature"])
        self.assertNotIn("QAFOLDER", one["signature"])
        stop = rp.signature(1, rp.analyze_log([r"STOP: cannot read D:\GAMES QAFOLDER 7f3a\eboot.bin or /app0/x/y.arc"]),
                            "b", "AMD")
        self.assertNotIn("QAFOLDER", stop)
        self.assertNotIn("/app0", stop)
        self.assertIn("STOP: cannot read <path> | build=b", stop)
        guest = rp.signature(1, rp.analyze_log(["STOP: missing /app0/nativePS4/x.arc"]), "b", "AMD")
        self.assertIn("STOP: missing <path> |", guest)

    def test_reason_names_the_file_not_the_folder(self):
        err = FileNotFoundError(2, "No such file or directory", r"D:\GAMES QAFOLDER 7f3a\sce_sys\param.sfo")
        self.assertEqual(rp.reason(err), "FileNotFoundError: No such file or directory (param.sfo)")
        self.assertEqual(rp.reason(ValueError("bad")), "ValueError: bad")

    def test_every_folder_in_the_settings_and_the_folders_holding_them(self):
        settings = {"game": r"D:\GAMES QAPRIV 9c2e\Sengoku Basara 4\CUSA01159",
                    "dlc_dir": r"D:\GAMES QAPRIV 9c2e\Sengoku Basara 4 DLC",
                    "mods_dir": r"E:\QAMODS 41b",  # a folder setting added later is masked too
                    "share": r"\\NAS-QA\games\b4", "resolution": "720p", "cheats": ["gold"], "gpu_index": 1}
        folders = rp.user_folders(settings, r"F:\QAAPPS 77\Basara4")
        self.assertIn((settings["game"], "<game>"), folders)
        self.assertIn((settings["dlc_dir"], "<dlc>"), folders)
        self.assertIn((r"E:\QAMODS 41b", "<folder>"), folders)
        self.assertIn((r"D:\GAMES QAPRIV 9c2e", "<folder>"), folders)  # holds the DLC folder
        self.assertIn((r"F:\QAAPPS 77", "<folder>"), folders)  # holds the program
        self.assertNotIn(("E:\\", "<folder>"), folders)  # a drive root is never a folder name
        masker = rp.Masker(None, settings["game"], r"F:\QAAPPS 77\Basara4", None, None, folders)
        text = masker("\n".join([
            r"Game: d:\games qapriv 9c2e\sengoku basara 4\cusa01159\eboot.bin",
            r"DLC: 'D:\\GAMES QAPRIV 9c2e\\Sengoku Basara 4 DLC\\CUSA01159-AOC'",
            "Mods: e:/qamods 41b/x.arc", r"Share: \\NAS-QA\games\b4\y", r"Other: D:\GAMES QAPRIV 9c2e\notes.txt",
            r"App: F:\QAAPPS 77\Basara4\user", r"Next to the app: F:\QAAPPS 77\other"]))
        for word in ("qapriv", "qamods", "nas-qa", "qaapps"):
            self.assertNotIn(word, text.lower())
        self.assertIn("DLC: '<dlc>", text)
        self.assertIn("Game: <game>", text)
        self.assertEqual(masker(r"C:\Windows\System32 and D:\ and 720p"), r"C:\Windows\System32 and D:\ and 720p")

    def test_a_profile_folder_named_unlike_the_account(self):
        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\qa.renamed", "USERNAME": "qauser",
                                          "COMPUTERNAME": "QAPC"}):
            masker = rp.Masker.from_environment()
        text = masker(r"Settings: C:\Users\qa.renamed\AppData\x.ini by qauser on QAPC")
        self.assertNotIn("qa.renamed", text)
        self.assertEqual(text, r"Settings: <user>\AppData\x.ini by <user> on <pc>")


class StubTests(unittest.TestCase):
    STUB = "GPU [Lib.AvPlayer] <Error> avplayer.cpp:55 sceAvPlayerDisableStream: (STUBBED) called"
    NEW = "GPU [Lib.SaveData] <Error> savedata.cpp:12 sceSaveDataMount5: (STUBBED) called"

    def test_names_and_counts_are_kept(self):
        a = rp.analyze_log([self.STUB] * 3 + [self.NEW])
        self.assertEqual(a["stubs"], {"sceAvPlayerDisableStream": 3, "sceSaveDataMount5": 1})
        self.assertEqual(a["stub_calls"], 4)
        self.assertEqual(a["near_primary_stubs"], [])  # no crash: nothing is unusual
        self.assertIn("sceSaveDataMount5 x1", rp.render_text(rp.finish_report({}, 0, False, False,
                                                                              [self.STUB, self.NEW], [], "x")))

    def test_a_stub_just_before_a_crash_is_shown(self):
        lines = [self.STUB] + ["Runtime: line"] * 40 + [self.NEW, "Runtime: line"] + BUG_002_LINES
        a = rp.analyze_log(lines)
        self.assertEqual(a["near_primary_stubs"], ["sceSaveDataMount5"])  # the far one is only counted
        s = summary(lines)
        self.assertEqual(s["near_primary_stubs"], ["sceSaveDataMount5"])
        self.assertTrue(s["detected_errors"][0].startswith("Fault: write"))  # still not called fatal
        self.assertNotIn(self.NEW, s["detected_errors"])
        text = rp.render_text(rp.finish_report({}, 0xC0000005, False, False, lines, [], "x"))
        self.assertIn("dipanggil tepat sebelum berhenti (tidak biasa)", text)
        self.assertIn("called just before the stop (unusual)", text)
        self.assertIn("  * sceSaveDataMount5", text)

    def test_healthy_sessions_still_show_nothing(self):
        corpus = HealthySessionTests().corpus()
        self.assertEqual(rp.analyze_log(corpus)["near_primary_stubs"], [])
        self.assertEqual(summary(corpus)["detected_errors"][0][:9], "Tidak ada")

    # The three runtime stubs that log values after "called" (avplayer.cpp:156, :217, video_out.cpp:465),
    # in the runtime's log line format.
    WITH_VALUES = {
        "sceAvPlayerJumpToTime":
            "GPU [Lib.AvPlayer] <Error> avplayer.cpp:156 sceAvPlayerJumpToTime: (STUBBED) called, time (msec) = 1500",
        "sceAvPlayerSetTrickSpeed":
            "GPU [Lib.AvPlayer] <Error> avplayer.cpp:217 sceAvPlayerSetTrickSpeed: (STUBBED) called speed = 2",
        "sceVideoOutSetWindowModeMargins": "GPU [Lib.VideoOut] <Error> video_out.cpp:465 "
                                           "sceVideoOutSetWindowModeMargins: (STUBBED) called top = 0, bottom = 0",
    }

    def test_stubs_that_log_values_are_stubs_like_the_rest(self):
        for name, line in self.WITH_VALUES.items():
            healthy = HealthySessionTests().corpus() + [line, line]
            self.assertEqual(summary(healthy)["detected_errors"][0][:9], "Tidak ada", name)  # not fatal
            a = rp.analyze_log(healthy)
            self.assertEqual(a["stubs"][name], 2, name)  # counted under its name
            crash = [line, "Runtime: line"] + BUG_002_LINES
            self.assertEqual(rp.analyze_log(crash)["near_primary_stubs"], [name])  # shown just before a crash
            self.assertEqual(summary(crash)["near_primary_stubs"], [name])
            self.assertTrue(summary(crash)["detected_errors"][0].startswith("Fault: write"))

    def test_every_stub_form_in_the_runtime_sources_is_recognised(self):
        sources = [p for d in ("src", "gpu") for p in (ROOT / d).rglob("*") if p.suffix in (".c", ".cpp", ".h")]
        forms = set()
        for path in sources:
            for m in re.finditer(r'"\(STUBBED\)([^"]*)"', path.read_text(encoding="utf-8", errors="replace")):
                forms.add(m.group(1))
        self.assertGreaterEqual(len(forms), 4)  # " called" and the three with values
        for form in forms:
            line = "GPU [Lib.Test] <Error> test.cpp:1 sceTestCall: (STUBBED)" + form.replace("{}", "7")
            self.assertTrue(rp.STUB_CALL.search(line), form)
            self.assertEqual(rp.STUB_NAME.search(line).group(1), "sceTestCall", form)


class OrderAndLimitTests(unittest.TestCase):
    def test_newest_fault_or_stop_first(self):
        lines = ["STOP: first unsupported PS4 import: sceFoo (index 1)", "Runtime: line",
                 "Fault: read access to 0000000000000000 at guest offset 0x10 (thread 1)"]
        self.assertTrue(rp.analyze_log(lines)["primary"][0].startswith("Fault:"))
        self.assertTrue(summary(lines)["detected_errors"][0].startswith("Fault:"))
        self.assertTrue(summary(lines)["detected_errors"][1].startswith("STOP:"))

    def test_non_null_addresses_do_not_change_the_signature(self):
        def sig(address):
            return rp.signature(0xC0000005, rp.analyze_log(
                [f"Fault: read access to {address} at guest offset 0xaebfb7 (thread 1)"]), "b", "AMD")
        self.assertEqual(sig("00007ff600001234"), sig("0000000000002000"))
        self.assertIn("read:addr", sig("00007ff600001234"))
        self.assertNotEqual(sig("0000000000000000"), sig("0000000000002000"))  # null is its own class

    def test_files_over_the_limit_are_left_out_and_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            big, small = Path(tmp) / "big.png", Path(tmp) / "small.png"
            big.write_bytes(b"x" * 20)
            small.write_bytes(b"x" * 5)
            target = Path(tmp) / "r.zip"
            skipped = rp.make_zip(target, {"schema": 1}, "text\n", rp.Masker(), binaries=[
                ("captures/big.png", big), ("captures/small.png", small)],
                withheld=["crash/core.dmp (9 bytes): kept"], max_file_bytes=10)
            self.assertEqual(skipped, ["crash/core.dmp (9 bytes): kept",
                                       "captures/big.png: 20 bytes, over the 10 byte limit"])
            with zipfile.ZipFile(target) as zf:
                self.assertEqual(sorted(zf.namelist()), ["captures/small.png", "report.json", "report.txt"])
                note = zf.read("report.txt").decode("utf-8")
            self.assertIn("over the 10 byte limit", note)
            self.assertIn("crash/core.dmp (9 bytes): kept", note)

    def test_a_write_that_fails_half_way_leaves_the_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.json"
            target.write_text('{"old": 1}', encoding="utf-8")
            real = Path.write_text

            def half(self, text, *args, **kwargs):
                real(self, text[:3], *args, **kwargs)  # part of the file reaches the disk, then the write fails
                raise OSError(28, "No space left on device")
            with mock.patch.object(Path, "write_text", half):
                with self.assertRaises(OSError):
                    rp.write_atomic(target, '{"new": 2}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"old": 1}')


class BilingualReportTests(unittest.TestCase):
    def test_every_heading_of_the_report_text_has_english(self):
        lines = BUG_002_LINES
        report = rp.finish_report({"launcher": {}, "game": {}, "system": {}, "vulkan": {}, "settings": {},
                                   "content": {}}, 0xC0000005, False, False, lines, [], "x")
        report["hints"] = ["h"]
        text = rp.render_text(report, ["tail"])
        headings = [line for line in text.splitlines() if line.startswith(("---", "==="))]
        self.assertGreaterEqual(len(headings), 10)
        for heading in headings:
            if heading.strip("-= ") in ("Port", "Game", "Vulkan"):
                continue  # the same word in both languages
            self.assertIn(" / ", heading, heading)
        for english in ("Session report", "Summary", "Most important in the log", "Unusual files not found",
                        "Session end", "End of report", "Last lines of last_run.log", "Hints"):
            self.assertIn(english, text)

    def test_every_way_a_session_ends_has_english(self):
        def ended(code, stopped=False, forced=False, lines=("Runtime: ok",)):
            return rp.summary_line(rp.finish_report({}, code, stopped, forced, list(lines), [], "x"))
        for line in (ended(0), ended(1, True), ended(1, True, True), ended(5), ended(5, lines=()),
                     ended(0xC0000005, lines=BUG_002_LINES), ended(1, lines=["STOP: x"]),
                     rp.summary_line({"status": "started"})):
            self.assertIn(" / ", line, line)

if __name__ == "__main__":
    unittest.main()
