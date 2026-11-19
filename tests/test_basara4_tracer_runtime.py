"""The runtime side of the Tracer, as far as Python can see it without a build: the in-game text names the Tracer, F11
marks the audio log, both logs carry the epoch of their 0 ms, no text still sends the player to a removed
option, and the report labels the log line that looks like an error and is not. The behaviour of the C and C++
code is covered by audio-levels-test, render-trace-test and the other CTest targets."""
import re
import unittest
from pathlib import Path

from launcher_fixtures import bl

rp = bl.basara4_report
ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / name).read_text(encoding="utf-8")


class NamesTests(unittest.TestCase):
    def test_the_in_game_text_and_its_translation_name_the_tracer(self):
        for name in ("gpu/shim/spartanport_overlay.cpp", "gpu/shim/spartanport_ui_language.h"):
            text = source(name)
            self.assertIn("F11 captures a trace when Tracer is enabled in the launcher.", text, name)
            self.assertNotIn("Render debug trace", text, name)
        self.assertIn("“Tracer”", source("gpu/shim/spartanport_ui_language.h"))  # the Chinese line names it too

    def test_no_text_outside_the_old_docs_sends_the_player_to_a_removed_option(self):
        for name in ("README.id.md", "tools/audio_log_summary.py", "tools/audio_loop_check.py",
                     "tools/audio_master_check.py", "tools/audio_session_report.py"):
            text = source(name)
            self.assertNotIn("Audio debug log", text, name)
            self.assertNotIn("Audio Debug Log", text, name)
            self.assertNotIn("Render debug trace", text, name)
        self.assertNotIn('launcher\'s "Audio debug log" option', source("NOTICE.md"))
        self.assertIn("**Tracer**", source("README.id.md"))


class MarkerTests(unittest.TestCase):
    def test_f11_marks_the_audio_log_at_the_moment_it_is_pressed(self):
        overlay = source("gpu/shim/spartanport_overlay.cpp")
        self.assertRegex(overlay, r'extern "C" void runtime_audio_marker\(const char\* reason\);')
        self.assertRegex(overlay, r'Request\("hotkey_f11"\);\s*runtime_audio_marker\("hotkey_f11"\);')

    def test_the_runtime_declares_it_and_the_renderer_tests_stub_it(self):
        self.assertIn("void runtime_audio_marker(const char *reason);", source("src/runtime.h"))
        self.assertIn("void runtime_audio_marker(const char *reason) {", source("src/runtime_audio.c"))
        self.assertIn("void runtime_audio_marker(const char*)", source("tests/test_gpu_runtime_stubs.cpp"))

    def test_both_logs_start_with_the_epoch_of_their_zero(self):
        audio = source("src/runtime_audio.c")
        self.assertRegex(audio, r"log\.start format=1 audio_mode=%s epoch_ms=%llu")
        trace = source("gpu/shadps4/video_core/renderer_vulkan/vk_render_trace.h")
        self.assertIn('"[     0 ms] log.start format=1 epoch_ms="', trace)
        self.assertLess(trace.index("log.start format=1 epoch_ms="), trace.index('"[     0 ms] gpu.info "'))

    def test_the_level_lines_only_run_while_the_audio_log_is_on(self):
        """The 100 ms bins sit in the block that already computes the peak, which only runs with the log on, and
        the silent-buffer feed is guarded the same way: nothing is added to the game when the Tracer is off."""
        audio = source("src/runtime_audio.c")
        feed = [m.start() for m in re.finditer(r"level_feed\(\(int\)\(p-ports\)", audio)]
        self.assertEqual(len(feed), 2)
        self.assertIn("if (runtime_audio_log_enabled()) { /* level of what the port is given", audio[:feed[0]][-2500:])
        self.assertIn("if (!data && runtime_audio_log_enabled())", audio[:feed[1]][-200:])
        self.assertIn("if (!runtime_audio_log_enabled()) return;", audio[audio.index("void runtime_audio_marker"):][:200])


class HarmlessTests(unittest.TestCase):
    JOIN = "HLE thread: join failed (Operation not permitted); waiting for it to finish"

    def report(self, lines):
        return rp.finish_report(rp.new_report("2026-11-19T10:00:00", {}, {}, {}, {}, {}, {}), 1, False, False,
                                lines, [], "NVIDIA")

    def test_the_join_message_is_counted_and_explained_as_harmless(self):
        lines = ["Runtime: ok"] + [self.JOIN] * 9 + ["Audio: playing"]
        self.assertEqual(rp.analyze_log(lines)["known_harmless"], {"HLE thread: join failed": 9})
        text = rp.render_text(self.report(lines))
        self.assertIn("--- Known and harmless (the lines stay in last_run.log) ---", text)
        self.assertIn("  * HLE thread: join failed x9: a game thread is waited for at most 5 s and then let go", text)
        self.assertIn("is not a crash", text)

    def test_the_raw_lines_are_not_hidden(self):
        lines = ["Runtime: ok", self.JOIN, "Audio: playing"]
        text = rp.render_text(self.report(lines), tail_lines=lines)
        self.assertIn(self.JOIN, text)  # still in the tail the report shows, and in last_run.log itself

    def test_a_session_without_it_has_no_such_section(self):
        lines = ["Runtime: ok", "Audio: playing"]
        self.assertEqual(rp.analyze_log(lines)["known_harmless"], {})
        self.assertNotIn("Known and harmless", rp.render_text(self.report(lines)))

    def test_the_label_does_not_turn_a_crash_into_a_harmless_one(self):
        lines = [self.JOIN, "Fault: access violation at 0x10 (code 0xc0000005)", "STOP: crash"]
        analysis = rp.analyze_log(lines)
        self.assertEqual(analysis["known_harmless"], {"HLE thread: join failed": 1})
        self.assertTrue(analysis["primary"])  # the real stop is still the primary line


if __name__ == "__main__":
    unittest.main()
