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
                         ["Log file not found (the game stopped before writing a log)."])
        self.assertEqual(summary_of("")["detected_errors"], ["Log is empty (the game stopped before it started up)."])

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
        self.assertEqual(summary["detected_errors"], ["No known error pattern in the log (the game closed suddenly)."])
        self.assertEqual(summary["unusual_not_found"], ["/app0/x"])
        normal = summary_of("Runtime: open(/app0/nativePS4/actorParam/pl008/pl008.pmc) -> not found")
        self.assertEqual((normal["unusual_not_found"], normal["hidden_not_found"]), ([], 1))

    def test_tail_context_length_and_no_signature(self):
        lines = [f"Runtime: line {i}" for i in range(30)]
        summary = summary_of("\n".join(lines), max_context_lines=3)
        self.assertEqual(summary["detected_errors"], ["No known error pattern in the log (the game closed suddenly)."])
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
            self.assertNotEqual(summary_of(sample)["detected_errors"][0][:22], "No known error pattern", sample)


class HardwareHintTests(unittest.TestCase):
    def test_memory_and_gpu_signatures(self):
        self.assertEqual(hint_titles(1, ["ERROR_COMMITMENT_LIMIT"]),
                         ["💾 Windows Virtual Memory (RAM / Page File) Ran Out [Error 1455]:"])
        self.assertEqual(hint_titles(1, ["cannot create 3200 MiB of guest memory"]),
                         ["💾 Windows Virtual Memory (RAM / Page File) Ran Out [Error 1455]:"])
        self.assertEqual(hint_titles(1, ["Failed allocating image with error x"]),
                         ["🎮 Not Enough Graphics Memory (VRAM / Shared GPU Memory):"])
        self.assertEqual(hint_titles(1, ["Vulkan 1.3 is required, 1.2 found"]),
                         ["⚠️ Graphics Card (GPU) or Driver Does Not Support Vulkan 1.3 Yet:"])
        self.assertEqual(hint_titles(1, ["VK_ERROR_DEVICE_LOST"]),
                         ["🔌 Graphics Card Connection Lost (GPU Device Lost / Driver Timeout):"])
        self.assertEqual(hint_titles(1, ["Failed to create compute pipeline"]),
                         ["🛠️ Vulkan Shader / Pipeline Compilation Failed:"])
        self.assertEqual(hint_titles(1, ["no rewrite table matches the image"]),
                         ["🖥️ CPU Instructions (x86-64-v3 / AVX2 / BMI1) Do Not Match:"])

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
        self.assertEqual(hint_titles(0xC0000139, ["Log is empty (the game stopped before it started up)."]),
                         ["🧩 Vulkan Not Found or Too Old (Exit Code 0xC0000139):"])
        self.assertEqual(hint_titles(-1073741511, []),  # the signed form of 0xC0000139
                         ["🧩 Vulkan Not Found or Too Old (Exit Code 0xC0000139):"])
        self.assertEqual(hint_titles(0xC0000135, []), ["📁 Game Files Incomplete (Exit Code 0xC0000135):"])
        self.assertEqual(hint_titles(-1073741515, []), ["📁 Game Files Incomplete (Exit Code 0xC0000135):"])
        self.assertEqual(hint_titles(1, []), [])
        self.assertEqual(hint_titles(None, []), [])
        # A recognised signature wins over the exit code.
        self.assertEqual(len(hint_titles(0xC0000005, ["VK_ERROR_DEVICE_LOST"])), 1)
        self.assertIn("Device Lost", hint_titles(0xC0000005, ["VK_ERROR_DEVICE_LOST"])[0])


INDONESIAN = ("Solusi", "tidak", "Tidak", "dan ", "yang", "Kartu", "Memori", "Gagal", "Berhenti", "berhenti",
              "Laporan", "laporan", "kosong", "ditemukan", "Pasang", "Kalau", "lalu")


class EnglishOnlyTests(unittest.TestCase):
    """Error messages are English only, so they read the same for players anywhere."""
    SIGNATURES = ["ERROR_COMMITMENT_LIMIT", "vk::Result=ErrorOutOfDeviceMemory", "Vulkan 1.3 is required",
                  "VK_ERROR_DEVICE_LOST", "Failed to create graphics pipeline", "missing MOVBE, BMI1, LZCNT"]
    CODES = [0xC0000409, 0xC0000005, 0x17, 0xC0000139, 0xC0000135]

    def test_every_hint_is_english(self):
        hints = [h for sig in self.SIGNATURES for h in bl.explain_hardware_crash(1, [sig], "")]
        hints += [h for code in self.CODES for h in bl.explain_hardware_crash(code, [], "")]
        self.assertEqual(len(hints), len(self.SIGNATURES) + len(self.CODES))  # one per branch, none missed
        for hint in hints:
            self.assertIn("👉 Fix", hint, hint)
            for word in INDONESIAN:
                self.assertNotIn(word, hint, hint)

    def test_fallback_lines_are_english(self):
        for text in (None, "", "Runtime: line"):
            line = summary_of(text)["detected_errors"][0]
            for word in INDONESIAN:
                self.assertNotIn(word, line, line)


class GoldenHintTests(unittest.TestCase):
    """The full text of every crash hint, word for word."""
    GOLDEN = [
        ('ERROR_COMMITMENT_LIMIT', 0x1,
         "💾 Windows Virtual Memory (RAM / Page File) Ran Out [Error 1455]:\nRAM plus the Windows page file is not enough to allocate the game's memory.\n👉 Fix: open Windows Settings -> Advanced System Settings -> Performance -> Advanced -> Virtual Memory, tick 'Automatically manage paging file size for all drives', close heavy apps (Chrome/Discord), then restart the PC."),
        ('vk::Result=ErrorOutOfDeviceMemory', 0x1,
         '🎮 Not Enough Graphics Memory (VRAM / Shared GPU Memory):\nThe GPU ran out of VRAM or system memory while loading textures/buffers.\n👉 Fix: lower Resolution in the launcher to 540p or 720p, turn the Upscaler Off, and close other apps that use GPU acceleration.'),
        ('Vulkan 1.3 is required', 0x1,
         "⚠️ Graphics Card (GPU) or Driver Does Not Support Vulkan 1.3 Yet:\nThe selected GPU lacks the Vulkan 1.3 features the game needs to render.\n👉 Fix: update the graphics driver (Intel / AMD Adrenalin / NVIDIA GeForce) to the latest version. If the laptop/PC has 2 GPUs, choose the dedicated GPU (NVIDIA/AMD) in the launcher's GPU menu."),
        ('VK_ERROR_DEVICE_LOST', 0x1,
         '🔌 Graphics Card Connection Lost (GPU Device Lost / Driver Timeout):\nThe GPU driver stopped responding because of heavy rendering load or an unstable driver.\n👉 Fix: lower Resolution to 720p, turn the Upscaler off, and make sure the GPU driver is up to date.'),
        ('Failed to create graphics pipeline', 0x1,
         "🛠️ Vulkan Shader / Pipeline Compilation Failed:\nThe GPU driver failed to compile a graphics pipeline.\n👉 Fix: update your GPU's Vulkan driver or turn off Async Shaders in the launcher."),
        ('missing MOVBE, BMI1, LZCNT', 0x1,
         "🖥️ CPU Instructions (x86-64-v3 / AVX2 / BMI1) Do Not Match:\nThe processor needs an instruction translation table (isa-rewrite.bin).\n👉 Fix: click 'Re-prepare' at the bottom of the launcher so isa-rewrite.bin is made again."),
        ('', 0xc0000409,
         '💥 Graphics Driver Crash / System Memory Limit (Exit Code 0xC0000409):\nWindows or the GPU driver force-stopped the process (common on PCs with 8 GB RAM or an iGPU such as Intel UHD / AMD Radeon Graphics when system memory/VRAM is full or a driver feature is not supported).\n👉 Fix: use Resolution 540p or 720p, turn the Upscaler Off, enable the Windows Paging File (Virtual Memory), and update your graphics driver.'),
        ('', 0xc0000005,
         "💥 Memory Access Violation (0xC0000005):\nA memory access conflict happened in the GPU driver or the game data.\n👉 Fix: lower Resolution to 720p, make sure the GPU driver is up to date, or click 'Re-prepare' if the eboot file was changed."),
        ('', 0x17,
         "⚠️ GPU Initialization / Assertion Failure (Exit Code 0x17):\nThe Vulkan renderer stopped because a GPU/VRAM resource limit or a driver feature was not met.\n👉 Fix: lower Resolution to 540p/720p, turn the Upscaler off, and check your GPU's Vulkan driver."),
        ('', 0xc0000139,
         "🧩 Vulkan Not Found or Too Old (Exit Code 0xC0000139):\nWindows could not load the game because the PC's Vulkan components are missing or too old.\n👉 Fix: update the graphics driver (NVIDIA / AMD / Intel), then start again. If the PC has old graphics without Vulkan support, the game cannot run on this PC yet."),
        ('', 0xc0000135,
         '📁 Game Files Incomplete (Exit Code 0xC0000135):\nWindows could not find one of the files the game needs.\n👉 Fix: extract the port ZIP (not the game files) completely into a new folder, and make sure the antivirus did not quarantine any file in it.'),
    ]

    def test_every_hint_word_for_word(self):
        for signature, code, english in self.GOLDEN:
            with self.subTest(signature=signature, code=hex(code)):
                errors = [signature] if signature else []
                self.assertEqual(bl.explain_hardware_crash(code, errors, ""), [english])

    def test_the_golden_list_covers_every_hint(self):
        source = Path(bl.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("hints.append("), len(self.GOLDEN))

if __name__ == "__main__":
    unittest.main()
