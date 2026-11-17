"""Crash diagnosis in launcher/basara4_launcher.py (replaces the upstream test_launcher_exit_hint.py).

Locks the current behaviour of extract_crash_summary and explain_hardware_crash. Logs are synthetic
lines in the runtime's format. Changing the hint wording is a separate change.
"""
import tempfile
import unittest
from pathlib import Path

from launcher_fixtures import bl


def summary_of(text, **kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "last_run.log"
        if text is not None:
            log.write_text(text, encoding="utf-8")
        return bl.extract_crash_summary(log, **kwargs)


def hint_titles(exit_code, errors, tail=""):
    return [hint.splitlines()[0] for hint in bl.explain_hardware_crash(exit_code, errors, tail)]


class CrashSummaryTests(unittest.TestCase):
    def test_missing_or_empty_log(self):
        self.assertEqual(summary_of(None)["detected_errors"],
                         ["File log tidak ditemukan (game belum sempat menulis log). / "
                          "Log file not found (the game stopped before writing a log)."])
        self.assertEqual(summary_of("")["detected_errors"], ["Log kosong (game berhenti sebelum inisialisasi). / "
                                                             "Log is empty (the game stopped before it started up)."])

    def test_newest_fatal_lines_first_without_duplicates_at_most_four(self):
        lines = ["Runtime: started"] + [f"STOP: reason {i}" for i in range(6)] + [
            "STOP: reason 5", "GPU [Render.Vulkan] <Error> vk::Result=ErrorDeviceLost", "Runtime: exit"]
        summary = summary_of("\n".join(lines))
        # Fault/STOP/ASSERT come first, newest first, without duplicates, at most four lines in all.
        self.assertEqual(summary["detected_errors"],
                         ["STOP: reason 5", "STOP: reason 4", "STOP: reason 3", "STOP: reason 2"])
        self.assertEqual(summary["total_lines"], len(lines))
        self.assertEqual(summary["tail_context"].splitlines(), lines[-10:])
        # Several signatures on one line come in FATAL_PATTERNS order.
        self.assertEqual(summary_of("GPU [Render.Vulkan] <Error> vk::Result=ErrorDeviceLost")["detected_errors"],
                         ["vk::Result=ErrorDeviceLost", "GPU [Render.Vulkan] <Error> vk::Result=ErrorDeviceLost"])

    def test_a_fault_is_never_pushed_out_by_later_errors(self):
        lines = ["Fault: write access to 0000000000000010 at guest offset 0x9c859c (thread 7)"] + [
            f"GPU [Lib.Pad] <Error> late error {i}" for i in range(8)] + [
            "Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found"] * 100
        detected = summary_of("\n".join(lines))["detected_errors"]
        self.assertEqual(detected[0], lines[0][:lines[0].index(" (thread")] + " (thread 7)")
        self.assertEqual(len(detected), 4)

    def test_not_found_is_not_fatal_but_unusual_ones_are_listed(self):
        summary = summary_of("Runtime: open(/app0/x) -> not found")
        self.assertEqual(summary["detected_errors"], ["Tidak ada fatal signature terdaftar (game keluar mendadak). / "
                         "No known fatal signature (the game exited suddenly)."])
        self.assertEqual(summary["unusual_not_found"], ["/app0/x"])
        normal = summary_of("Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found")
        self.assertEqual((normal["unusual_not_found"], normal["hidden_not_found"]), ([], 1))

    def test_tail_context_length_and_no_signature(self):
        lines = [f"Runtime: line {i}" for i in range(30)]
        summary = summary_of("\n".join(lines), max_context_lines=3)
        self.assertEqual(summary["detected_errors"], ["Tidak ada fatal signature terdaftar (game keluar mendadak). / "
                         "No known fatal signature (the game exited suddenly)."])
        self.assertEqual(summary["tail_context"].splitlines(), lines[-3:])

    def test_every_fatal_pattern_is_recognised(self):
        samples = ["STOP: reserving failed", "Fault: at 0x10", "GPU [Debug] <Critical> boom",
                   "Failed to create graphics pipeline x", "ASSERT_MSG failed", "vk::Result=ErrorUnknown",
                   "VK_ERROR_DEVICE_LOST", "GPU [Render.Vulkan] <Error> x", "GPU [Lib.Pad] <Error> x",
                   "guest raised debug exception 3",
                   "cannot create 3200 MiB of guest memory (1455)", "error 1455 at commit",
                   "ERROR_COMMITMENT_LIMIT", "no rewrite table matches the image",
                   "missing MOVBE, BMI1, LZCNT", "Failed allocating image with error x",
                   "Required Vulkan extension unavailable: VK_x", "Vulkan 1.3 is required, 1.2 found",
                   "Failed to create Vulkan logical device"]
        self.assertEqual(len(samples), len(bl.FATAL_PATTERNS))
        for sample in samples:
            self.assertNotEqual(summary_of(sample)["detected_errors"][0][:9], "Tidak ada", sample)


class HardwareHintTests(unittest.TestCase):
    def test_memory_and_gpu_signatures(self):
        self.assertEqual(hint_titles(1, ["ERROR_COMMITMENT_LIMIT"]),
                         ["💾 Memori Virtual Windows (RAM / Pagefile) Habis [Error 1455]:"])
        self.assertEqual(hint_titles(1, ["cannot create 3200 MiB of guest memory"]),
                         ["💾 Memori Virtual Windows (RAM / Pagefile) Habis [Error 1455]:"])
        self.assertEqual(hint_titles(1, ["Failed allocating image with error x"]),
                         ["🎮 Memori Kartu Grafis (VRAM / Shared GPU Memory) Tidak Cukup:"])
        self.assertEqual(hint_titles(1, ["Vulkan 1.3 is required, 1.2 found"]),
                         ["⚠️ Kartu Grafis (GPU) atau Driver Belum Mendukung Vulkan 1.3:"])
        self.assertEqual(hint_titles(1, ["VK_ERROR_DEVICE_LOST"]),
                         ["🔌 Koneksi Kartu Grafis Terputus (GPU Device Lost / Driver Timeout):"])
        self.assertEqual(hint_titles(1, ["Failed to create compute pipeline"]),
                         ["🛠️ Gagal Kompilasi Shader / Pipeline Vulkan:"])
        self.assertEqual(hint_titles(1, ["no rewrite table matches the image"]),
                         ["🖥️ Instruksi CPU (x86-64-v3 / AVX2 / BMI1) Tidak Cocok:"])

    def test_signatures_in_the_tail_count_and_several_hints_add_up(self):
        titles = hint_titles(1, [], "error 1455\nVK_ERROR_DEVICE_LOST")
        self.assertEqual(len(titles), 2)
        self.assertIn("Error 1455", titles[0])
        self.assertIn("Device Lost", titles[1])

    def test_exit_codes_only_without_a_signature(self):
        self.assertIn("0xC0000409", hint_titles(0xC0000409, [])[0])
        self.assertIn("0xC0000409", hint_titles(-1073740791, [])[0])  # the signed form of 0xC0000409
        self.assertIn("0xC0000005", hint_titles(0xC0000005, [])[0])
        self.assertIn("0x17", hint_titles(0x17, [])[0])
        self.assertEqual(hint_titles(0xC0000139, ["Log kosong (game berhenti sebelum inisialisasi)."]),
                         ["🧩 Vulkan Tidak Ditemukan atau Terlalu Lama (Exit Code 0xC0000139):"])
        self.assertEqual(hint_titles(-1073741511, []),  # the signed form of 0xC0000139
                         ["🧩 Vulkan Tidak Ditemukan atau Terlalu Lama (Exit Code 0xC0000139):"])
        self.assertEqual(hint_titles(0xC0000135, []), ["📁 File Game Tidak Lengkap (Exit Code 0xC0000135):"])
        self.assertEqual(hint_titles(-1073741515, []), ["📁 File Game Tidak Lengkap (Exit Code 0xC0000135):"])
        self.assertEqual(hint_titles(1, []), [])
        self.assertEqual(hint_titles(None, []), [])
        # A recognised signature wins over the exit code.
        self.assertEqual(len(hint_titles(0xC0000005, ["VK_ERROR_DEVICE_LOST"])), 1)
        self.assertIn("Device Lost", hint_titles(0xC0000005, ["VK_ERROR_DEVICE_LOST"])[0])


class BilingualTests(unittest.TestCase):
    """Every hint the crash window can show is the Indonesian text, an empty line, then the English text."""
    SIGNATURES = ["ERROR_COMMITMENT_LIMIT", "vk::Result=ErrorOutOfDeviceMemory", "Vulkan 1.3 is required",
                  "VK_ERROR_DEVICE_LOST", "Failed to create graphics pipeline", "missing MOVBE, BMI1, LZCNT"]
    CODES = [0xC0000409, 0xC0000005, 0x17, 0xC0000139, 0xC0000135]

    def all_hints(self):
        hints = [h for sig in self.SIGNATURES for h in bl.explain_hardware_crash(1, [sig], "")]
        hints += [h for code in self.CODES for h in bl.explain_hardware_crash(code, [], "")]
        return hints

    def test_every_hint_is_indonesian_then_english(self):
        hints = self.all_hints()
        self.assertEqual(len(hints), len(self.SIGNATURES) + len(self.CODES))  # one per branch, none missed
        for hint in hints:
            indonesian, english = hint.split("\n\n")
            self.assertIn("👉 Solusi", indonesian, hint)
            self.assertIn("👉 Fix", english, hint)
            self.assertNotIn("Solusi", english, hint)
            self.assertEqual(indonesian.splitlines()[0][:2], english.splitlines()[0][:2])  # same icon

    def test_the_english_hint_names_the_same_problem(self):
        self.assertEqual(bl.explain_hardware_crash(0xC0000139, [], "")[0].split("\n\n")[1].splitlines()[0],
                         "🧩 Vulkan Not Found or Too Old (Exit Code 0xC0000139):")
        self.assertEqual(bl.explain_hardware_crash(0xC0000135, [], "")[0].split("\n\n")[1].splitlines()[0],
                         "📁 Game Files Incomplete (Exit Code 0xC0000135):")

    def test_fallback_lines_are_bilingual(self):
        for text in (None, "", "Runtime: line"):
            line = summary_of(text)["detected_errors"][0]
            indonesian, english = line.split(" / ")
            self.assertTrue(indonesian and english and indonesian != english, line)

    def test_the_bilingual_helpers_need_two_texts(self):
        rp = bl.basara4_report
        self.assertEqual(rp.bilingual("a", "b"), "a\n\nb")
        self.assertEqual(rp.bilingual_line("a", "b"), "a / b")
        for args in (("", "b"), ("a", ""), ("a", "a")):
            with self.assertRaises(ValueError):
                rp.bilingual(*args)
            with self.assertRaises(ValueError):
                rp.bilingual_line(*args)

if __name__ == "__main__":
    unittest.main()
