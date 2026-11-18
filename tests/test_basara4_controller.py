"""The Controller window (launcher/basara4_controller.py) that maps a controller without a console.

The real mapper needs a controller, so a small Python script plays it here: it speaks the same protocol
(one JSON event per stdout line, one command per stdin line) through real pipes. No game files; the
mapping file is written into a temporary folder. The windows are made fully transparent.
"""
import json
import queue
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from launcher_fixtures import bl
from paths import ROOT

import basara4_controller as bc

tk = bc.tk

# A stand-in for basara4-pad-mapper.exe --ui. It is started as [python, <this script>, "--ui"]
# (the window passes the mapping path first, so the script path takes that place) and writes the
# "mapping" to the file named by FAKE_MAPPING_OUT only on "save", like the real mapper.
FAKE_MAPPER = r'''
import json, os, sys
def emit(**event):
    print(json.dumps(event), flush=True)
args = sys.argv[1:]
with open(os.environ["FAKE_MAPPING_OUT"] + ".args", "w") as f:
    json.dump(args, f)
print("plain text the window must ignore", flush=True)
emit(ev="controller", name="Virtual pad", guid="0300")
emit(ev="prompt", target="a", index=0)
emit(ev="captured", target="a", binding="b0")
emit(ev="review", mapping="0300,Virtual pad,a:b0,platform:Windows,")
for line in sys.stdin:
    command = line.strip()
    if command == "save":
        with open(os.environ["FAKE_MAPPING_OUT"], "w") as f:
            f.write("0300,Virtual pad,a:b0,platform:Windows,\n")
        emit(ev="saved", mapping="0300,Virtual pad,a:b0,platform:Windows,")
        sys.exit(0)
    if command == "cancel":
        break
emit(ev="cancelled")
sys.exit(2)
'''


def destroy(widget):
    try:
        widget.destroy()
    except tk.TclError:
        pass  # already gone with its parent


class ControllerTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: destroy(self.root))
        self.folder = Path(self.tmp.name)
        self.mapping = self.folder / "gamepad_mapping.txt"

    def window(self, **kwargs):
        window = bc.ControllerWindow(self.root, self.folder / "mapper.exe", self.mapping, {}, **kwargs)
        window.attributes("-alpha", 0.0)
        self.addCleanup(lambda: destroy(window))
        return window

    def column(self, window, name):
        return window.rows.item(name, "values")[2]

    def enabled(self, window):
        return {button.cget("text") for button in (window.map_all, window.skip, window.cancel, window.reset,
                                                    window.done) if not button.instate(["disabled"])}

    def started(self, **kwargs):
        """A window whose mapper is a mock: handle() gets the events, sends are recorded. Its event queue
        is real and empty, so the window's pump finds nothing when Tk runs."""
        window = self.window(**kwargs)
        mapper = mock.Mock()
        mapper.events = queue.Queue()
        with mock.patch.object(bc, "MapperProcess", return_value=mapper) as process:
            window.start()
        return window, mapper, process


class WindowEventTests(ControllerTestCase):
    def test_all_entries_in_the_mappers_order(self):
        source = (ROOT / "tools" / "gamepad_mapper.c").read_text(encoding="utf-8")
        table = source[source.index("targets[] = {"):source.index("};", source.index("targets[] = {"))]
        names = re.findall(r'\{"([a-z]+)", "', table)
        self.assertEqual(names, [name for name, _pad, _game in bc.ENTRIES])
        sticks = re.findall(r'\{"([a-z]+)", "[^"]*", 1\}', table)
        self.assertEqual(set(sticks), bc.STICKS)

    def test_every_mapper_event_is_handled(self):
        """Contract with tools/gamepad_mapper.c: each event it can send has a branch here."""
        source = (ROOT / "tools" / "gamepad_mapper.c").read_text(encoding="utf-8")
        sent = set(re.findall(r'emit\("([a-z]+)"', source))
        sent |= set(re.findall(r'\{\\"ev\\":\\"([a-z]+)\\"', source))  # the two written with printf
        handler = Path(bc.__file__).read_text(encoding="utf-8")
        handled = set(re.findall(r'kind == "([a-z]+)"', handler)) | set(
            sum((re.findall(r'"([a-z]+)"', group) for group in re.findall(r'kind in \(([^)]*)\)', handler)), []))
        self.assertTrue({"devices", "controller", "prompt", "saved"} <= sent, sent)
        self.assertEqual(sent - handled, set())
        reasons = set(re.findall(r'"(stick_[a-z_]+)"', source))
        self.assertEqual(reasons, {"stick_rotated", "stick_two_axes"})
        self.assertTrue(reasons <= set(bc.TEXT))

    def test_a_run_fills_the_list_and_done_saves(self):
        saved = mock.Mock()
        window, mapper, process = self.started(on_saved=saved)
        self.assertEqual(process.call_args.args[2], [])
        self.assertEqual(self.enabled(window), {"Skip", "Cancel"})
        window.handle({"ev": "controller", "name": "Xbox Controller", "guid": "0300"})
        window.handle({"ev": "prompt", "target": "a", "index": 0})
        self.assertEqual(self.column(window, "a"), "press now")
        self.assertIn("A (bottom)", window.prompt.cget("text"))
        self.assertIn("1 of 21", window.detail.cget("text"))
        window.handle({"ev": "captured", "target": "a", "binding": "b0"})
        window.handle({"ev": "timeout", "target": "b"})
        window.handle({"ev": "skipped", "target": "x"})
        window.handle({"ev": "prompt", "target": "leftx", "index": 17})
        self.assertIn("Left stick (1 of 4): push it straight RIGHT and hold", window.prompt.cget("text"))
        window.handle({"ev": "warn", "target": "leftx", "reason": "stick_rotated"})
        self.assertEqual(window.detail.cget("text"),
                         "That was a circle or a push back. Push straight RIGHT and hold, then let go.")
        window.handle({"ev": "warn", "target": "leftx", "reason": "stick_two_axes"})
        self.assertEqual(window.detail.cget("text"),
                         "The stick moved in two directions. Push straight RIGHT and hold, then let go.")
        window.handle({"ev": "used", "target": "leftx", "binding": "a0"})
        self.assertEqual(window.detail.cget("text"), bc.TEXT["used"])
        window.handle({"ev": "captured", "target": "leftx", "binding": "a1~"})
        self.assertEqual([self.column(window, n) for n in ("a", "b", "x", "leftx", "y")],
                         ["b0", "skipped (no input)", "skipped", "a1~", "not set"])
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "check")  # a stick was set: the check comes first
        mapper.send.assert_called_with("check left")
        window.looks_right.invoke()
        self.assertEqual(self.enabled(window), {"Done", "Cancel"})
        window.done.invoke()
        mapper.send.assert_called_with("save")
        window.handle({"ev": "saved", "mapping": "..."})
        saved.assert_called_once()
        window.handle({"ev": "exit", "code": 0})
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["saved"])
        self.assertEqual(self.enabled(window), {"Map all", "Reset", "Done"})
        self.assertIsNone(window.mapper)

    def test_skip_and_cancel_send_commands(self):
        window, mapper, _ = self.started()
        window.skip.invoke()
        window.cancel.invoke()
        self.assertEqual([c.args[0] for c in mapper.send.call_args_list], ["skip", "cancel"])
        window.handle({"ev": "cancelled"})
        window.handle({"ev": "exit", "code": 2})
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["cancelled"])

    def test_how_the_mapper_ended_is_always_shown(self):
        window, _mapper, _ = self.started()
        window.handle({"ev": "error", "message": "No controller found. Plug one in, then try again."})
        window.handle({"ev": "exit", "code": 1})
        self.assertEqual(window.prompt.cget("text"), "No controller found. Plug one in, then try again.")
        for events in ([{"ev": "prompt", "target": "a", "index": 0}, {"ev": "exit", "code": 3221225477}],
                       [{"ev": "exit", "code": 0}],  # exit 0 without "saved"
                       [{"ev": "error", "message": "Unknown command."}, {"ev": "prompt", "target": "a"},
                        {"ev": "exit", "code": 1}]):  # an old refused command does not explain the end
            window, _mapper, _ = self.started()
            for event in events:
                window.handle(event)
            self.assertEqual(window.prompt.cget("text"), bc.TEXT["ended"], events)
            self.assertEqual(window.state, "idle")

    def test_unknown_or_foreign_events_change_nothing(self):
        window, _mapper, _ = self.started()
        before = [window.rows.item(n, "values") for n in window.rows.get_children()]
        for event in ({"ev": "prompt", "target": "nosuch", "index": 0}, {"ev": "captured", "target": "zz"},
                      {"ev": "warn", "target": "a", "reason": "other"}, {"ev": "brand_new"}, {}):
            window.handle(event)
        self.assertEqual([window.rows.item(n, "values") for n in window.rows.get_children()], before)

    def test_sticks_only_lists_and_maps_the_four_stick_entries(self):
        window, _mapper, process = self.started(sticks_only=True)
        self.assertEqual(process.call_args.args[2], ["--sticks"])
        self.assertEqual(window.map_all.cget("text"), "Map sticks")
        self.assertEqual(list(window.rows.get_children()), ["leftx", "lefty", "rightx", "righty"])
        window.handle({"ev": "prompt", "target": "lefty", "index": 1})
        self.assertIn("(2 of 4): push it straight DOWN (toward you)", window.prompt.cget("text"))
        window.handle({"ev": "prompt", "target": "a", "index": 0})  # not in this list: ignored
        self.assertIn("(2 of 4)", window.prompt.cget("text"))

    def test_two_controllers_ask_which_one(self):
        window, mapper, _ = self.started()
        window.handle({"ev": "devices", "list": [{"i": 0, "name": "Xbox Controller", "guid": "03"},
                                                 {"i": 1, "name": "Arcade stick", "guid": "05"}]})
        self.assertTrue(window.chooser.winfo_manager())
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["choose"])
        window.device_list.current(1)
        window._use_device()
        mapper.send.assert_called_with("select 1")
        self.assertFalse(window.chooser.winfo_manager())
        window, mapper, _ = self.started()
        window.handle({"ev": "devices", "list": [{"i": 0, "name": "Xbox Controller", "guid": "03"}]})
        self.assertFalse(window.chooser.winfo_manager())  # one controller: no question
        mapper.send.assert_not_called()

    def test_closing_the_window_cancels_and_never_saves(self):
        window, mapper, _ = self.started()
        window.close()
        mapper.close.assert_called_once()
        mapper.send.assert_not_called()
        self.assertFalse(window.winfo_exists())
        window, mapper, _ = self.started()
        self.root.destroy()  # the launcher closing takes the window and its mapper with it
        mapper.close.assert_called_once()

    def test_reset_calls_the_launcher_and_says_the_state(self):
        reset = mock.Mock(side_effect=lambda: self.mapping.unlink())
        self.mapping.write_text("x", encoding="utf-8")
        window = self.window(on_reset=reset)
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["idle_saved"])
        window.reset.invoke()
        reset.assert_called_once()
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["idle"])

    def test_a_mapper_that_cannot_start_is_reported(self):
        window = self.window()
        with mock.patch.object(bc.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file")):
            window.start()
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["not_started"].format(reason="No such file"))
        self.assertEqual(window.state, "idle")
        self.assertIsNone(window.mapper)


class StickGuideAndCheckTests(ControllerTestCase):
    """P2: the animated stick guide (Mock 3), the player's live stick, and the check with Flip (Mock 4b)."""

    CENTER = bc.GUIDE_SIZE / 2

    def sent(self, mapper):
        return [c.args[0] for c in mapper.send.call_args_list]

    def pump_ui(self, ms):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)

    def guide_dot(self, window):
        x0, y0, x1, y1 = window.stick.canvas.coords(window.stick.guide_dot)
        return (x0 + x1) / 2, (y0 + y1) / 2

    def all_sticks_captured(self, window):
        for target, binding in (("leftx", "a0"), ("lefty", "a1~"), ("rightx", "a2"), ("righty", "a3")):
            window.handle({"ev": "prompt", "target": target})
            window.handle({"ev": "captured", "target": target, "binding": binding})

    def test_game_sees_names_the_direction_the_game_receives(self):
        self.assertEqual(bc.game_sees(30000, -30000), "UP-RIGHT")
        self.assertEqual(bc.game_sees(-30000, 30000), "DOWN-LEFT")
        self.assertEqual(bc.game_sees(0, -32768), "UP")
        self.assertEqual(bc.game_sees(32767, 2000), "RIGHT")
        self.assertEqual(bc.game_sees(500, -500), "CENTER")

    def test_idle_window_runs_no_animation(self):
        window = self.window()
        self.pump_ui(100)
        self.assertIsNone(window.stick.anim_job)
        self.assertFalse(window.stick_area.winfo_manager())

    def test_a_stick_step_shows_the_direction_moving(self):
        window, _mapper, _ = self.started()
        window.handle({"ev": "prompt", "target": "a"})
        self.assertFalse(window.stick_area.winfo_manager())  # buttons: no stick panel
        window.handle({"ev": "prompt", "target": "leftx"})
        self.assertTrue(window.stick_area.winfo_manager())
        self.assertIsNotNone(window.stick.anim_job)
        steps = [label.cget("text") for label in window.steps]
        self.assertIn("1. Left stick → push RIGHT  (now)", steps[0])
        self.assertIn("2. Left stick → push DOWN (toward you)  (next)", steps[1])
        # the white dot moves along the asked direction (right), never up or down
        positions, colours = [], set()
        for _ in range(12):
            self.pump_ui(90)
            positions.append(self.guide_dot(window))
            colours.add(window.stick.canvas.itemcget(window.stick.arrow, "fill"))
        xs = {round(x) for x, _y in positions}
        self.assertGreater(len(xs), 3)  # it moves
        self.assertTrue(all(x >= self.CENTER - 1 for x, _y in positions))  # to the right only
        self.assertTrue(all(abs(y - self.CENTER) < 1 for _x, y in positions))
        self.assertEqual(len(colours), 2)  # the arrow blinks
        # the vertical step moves the dot down (toward the player)
        window.handle({"ev": "prompt", "target": "lefty"})
        self.pump_ui(600)
        x, y = self.guide_dot(window)
        self.assertAlmostEqual(x, self.CENTER, delta=1)
        self.assertGreater(y, self.CENTER + 5)

    def test_the_players_stick_follows_live_values(self):
        window, _mapper, _ = self.started()
        window.handle({"ev": "prompt", "target": "leftx"})
        self.assertIn("Waiting for you to push RIGHT", window.you.cget("text"))
        window.handle({"ev": "live", "target": "leftx", "push": 32768, "side": 0})
        x, y = window.stick.live_dot_center()
        self.assertAlmostEqual(x, self.CENTER + bc.GUIDE_RADIUS, delta=1)
        self.assertAlmostEqual(y, self.CENTER, delta=1)
        self.assertIn("Keep pushing RIGHT", window.you.cget("text"))
        window.handle({"ev": "live", "target": "leftx", "push": 16384, "side": 16384})  # wobbles sideways
        x, y = window.stick.live_dot_center()
        self.assertAlmostEqual(x, self.CENTER + bc.GUIDE_RADIUS / 2, delta=1)
        self.assertAlmostEqual(y, self.CENTER + bc.GUIDE_RADIUS / 2, delta=1)
        before = window.stick.live_dot_center()
        window.handle({"ev": "live", "target": "rightx", "push": 32768, "side": 0})  # another entry: ignored
        self.assertEqual(window.stick.live_dot_center(), before)

    def test_the_guide_stops_with_the_window(self):
        window, _mapper, _ = self.started()
        window.handle({"ev": "prompt", "target": "leftx"})
        self.assertIsNotNone(window.stick.anim_job)
        window.handle({"ev": "prompt", "target": "a"})  # a button step: the animation stops
        self.assertIsNone(window.stick.anim_job)
        self.pump_ui(120)  # and stays stopped while Tk runs
        self.assertIsNone(window.stick.anim_job)
        self.assertFalse(window.stick_area.winfo_manager())
        window.handle({"ev": "prompt", "target": "rightx"})
        window.close()
        self.assertIsNone(window.stick.anim_job)
        self.pump_ui(150)  # no callback left on a destroyed canvas (it would raise in Tk)
        pending = [self.root.tk.call("after", "info", job) for job in self.root.tk.splitlist(
            self.root.tk.call("after", "info"))]
        self.assertFalse([p for p in pending if "_animate" in str(p)])

    def test_after_the_sticks_the_check_shows_what_the_game_receives(self):
        window, mapper, _ = self.started()
        self.all_sticks_captured(window)
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "check")
        self.assertEqual(self.sent(mapper)[-1], "check left")
        self.assertEqual(self.enabled(window), {"Cancel"})  # Done waits until the sticks are checked
        self.assertIn("Check your left stick: push it up-right", window.prompt.cget("text"))
        self.assertIsNone(window.stick.anim_job)  # the check has no animation, only the live dot
        window.handle({"ev": "live", "target": "left", "x": 30000, "y": -30000})
        self.assertEqual(window.sees.cget("text"), "Game sees: UP-RIGHT")
        x, y = window.stick.live_dot_center()
        self.assertGreater(x, self.CENTER + 40)
        self.assertLess(y, self.CENTER - 40)
        self.assertLessEqual(((x - self.CENTER) ** 2 + (y - self.CENTER) ** 2) ** 0.5, bc.GUIDE_RADIUS + 1)
        window.handle({"ev": "live", "target": "right", "x": -30000, "y": 0})  # not the stick checked
        self.assertEqual(window.sees.cget("text"), "Game sees: UP-RIGHT")
        # Flip up / down: the mapper turns lefty around; the check goes on with the tick kept
        window.flip_y.invoke()
        self.assertEqual(self.sent(mapper)[-1], "flip lefty")
        window.handle({"ev": "flipped", "target": "lefty", "binding": "a1"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "check")
        self.assertTrue(window.flip_y_var.get())
        self.assertEqual(self.column(window, "lefty"), "a1")
        window.flip_x.invoke()
        self.assertEqual(self.sent(mapper)[-1], "flip leftx")
        # Looks right: on to the right stick, the ticks start empty
        window.looks_right.invoke()
        self.assertEqual(self.sent(mapper)[-1], "check right")
        self.assertIn("right stick", window.prompt.cget("text"))
        self.assertFalse(window.flip_x_var.get() or window.flip_y_var.get())
        window.flip_x.invoke()
        self.assertEqual(self.sent(mapper)[-1], "flip rightx")
        window.looks_right.invoke()
        self.assertEqual(self.sent(mapper)[-1], "check off")
        self.assertEqual(window.state, "review")
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["check_done"])
        self.assertFalse(window.stick_area.winfo_manager())
        window.done.invoke()
        self.assertEqual(self.sent(mapper)[-1], "save")

    def test_redo_this_stick_asks_both_axes_then_checks_again(self):
        window, mapper, _ = self.started()
        self.all_sticks_captured(window)
        window.handle({"ev": "review", "mapping": "..."})
        window.redo_stick.invoke()
        self.assertEqual(self.sent(mapper)[-3:], ["check off", "redo leftx", "redo lefty"])
        window.handle({"ev": "prompt", "target": "leftx"})
        self.assertEqual(window.state, "mapping")
        self.assertIsNotNone(window.stick.anim_job)  # the guide is back for the redo
        window.handle({"ev": "captured", "target": "leftx", "binding": "a0"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "mapping")  # lefty still to come: no check yet
        window.handle({"ev": "prompt", "target": "lefty"})
        window.handle({"ev": "captured", "target": "lefty", "binding": "a1"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "check")
        self.assertEqual(self.sent(mapper)[-1], "check left")

    def test_between_the_two_redo_axes_no_guide_runs_and_a_refusal_returns_to_the_check(self):
        window, mapper, _ = self.started()
        self.all_sticks_captured(window)
        window.handle({"ev": "review", "mapping": "..."})
        window.redo_stick.invoke()
        window.handle({"ev": "prompt", "target": "leftx"})
        self.assertIsNotNone(window.stick.anim_job)
        window.handle({"ev": "captured", "target": "leftx", "binding": "a0"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertIsNone(window.stick.anim_job)  # waiting for the second axis: the guide does not run on
        self.pump_ui(100)
        self.assertIsNone(window.stick.anim_job)
        # the mapper refuses the second redo (it is in its review): the check comes back, with the reason
        window.handle({"ev": "error", "message": "Unknown command."})
        self.assertEqual(window.state, "check")
        self.assertEqual(window.redo_left, 0)
        self.assertEqual(self.sent(mapper)[-1], "check left")
        self.assertEqual(window.detail.cget("text"), "Unknown command.")
        self.assertTrue(window.looks_right.winfo_ismapped() or window.check_buttons.winfo_manager())

    def test_flip_of_an_unset_axis_is_off_and_sends_nothing(self):
        window, mapper, _ = self.started()
        window.handle({"ev": "prompt", "target": "leftx"})
        window.handle({"ev": "timeout", "target": "leftx"})
        window.handle({"ev": "prompt", "target": "lefty"})
        window.handle({"ev": "captured", "target": "lefty", "binding": "a1"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(self.sent(mapper)[-1], "check left")
        self.assertTrue(window.flip_x.instate(["disabled"]))  # left / right is not set
        self.assertFalse(window.flip_y.instate(["disabled"]))
        window.flip_x.invoke()
        self.assertNotIn("flip leftx", self.sent(mapper))
        window.flip_y.invoke()
        self.assertEqual(self.sent(mapper)[-1], "flip lefty")

    def test_no_stick_set_means_no_check(self):
        window, mapper, _ = self.started()
        window.handle({"ev": "prompt", "target": "leftx"})
        window.handle({"ev": "timeout", "target": "leftx"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(window.state, "review")
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["review"])
        self.assertFalse([c for c in self.sent(mapper) if c.startswith("check")])

    def test_only_the_set_stick_is_checked_and_unset_axes_cannot_flip(self):
        window, mapper, _ = self.started(sticks_only=True)
        window.handle({"ev": "prompt", "target": "rightx"})
        window.handle({"ev": "captured", "target": "rightx", "binding": "a2"})
        window.handle({"ev": "skipped", "target": "righty"})
        window.handle({"ev": "review", "mapping": "..."})
        self.assertEqual(self.sent(mapper)[-1], "check right")
        self.assertFalse(window.flip_x.instate(["disabled"]))
        self.assertTrue(window.flip_y.instate(["disabled"]))
        window.looks_right.invoke()
        self.assertEqual(self.sent(mapper)[-1], "check off")

    def test_sticks_only_switch_waits_for_a_running_mapping(self):
        window = self.window()
        self.assertTrue(window.set_sticks_only(True))
        self.assertEqual(list(window.rows.get_children()), ["leftx", "lefty", "rightx", "righty"])
        self.assertEqual(window.map_all.cget("text"), "Map sticks")
        window, _mapper, _ = self.started()
        self.assertFalse(window.set_sticks_only(True))
        self.assertFalse(window.sticks_only)
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["busy"])


class RealPipeTests(ControllerTestCase):
    """The window with a real child process: pipes, the reader thread and closing."""

    def run_fake(self, finish):
        script = self.folder / "fake_mapper.py"
        script.write_text(FAKE_MAPPER, encoding="utf-8")
        out = self.folder / "written.txt"
        window = bc.ControllerWindow(self.root, sys.executable, script, {**__import__("os").environ,
                                     "FAKE_MAPPING_OUT": str(out)})
        window.attributes("-alpha", 0.0)
        popen = subprocess.Popen
        with mock.patch.object(bc.subprocess, "Popen", side_effect=popen) as spy:
            window.start()
        self.assertEqual(spy.call_args.kwargs["creationflags"], 0x08000000)  # CREATE_NO_WINDOW
        self.assertEqual(spy.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(spy.call_args.kwargs["stdout"], subprocess.PIPE)
        proc = window.mapper.proc
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.wait(lambda: window.state == "review")
        self.assertEqual(self.column(window, "a"), "b0")
        self.assertEqual(json.loads((out.parent / (out.name + ".args")).read_text()), ["--ui"])
        finish(window)
        return window, out, proc

    def wait(self, condition, seconds=10):
        end = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), end, "timed out")
            self.root.update()
            time.sleep(0.01)

    def test_done_saves_through_the_pipe(self):
        window, out, proc = self.run_fake(lambda w: w.done.invoke())
        self.wait(lambda: window.mapper is None)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(window.prompt.cget("text"), bc.TEXT["saved"])
        self.assertTrue(out.read_text().startswith("0300,Virtual pad,a:b0"))
        window.destroy()

    def test_closing_mid_run_ends_the_mapper_without_saving(self):
        window, out, proc = self.run_fake(lambda w: w.close())
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(out.exists())
        self.assertFalse(window.winfo_exists())


class HungMapperTests(unittest.TestCase):
    def test_a_mapper_that_ignores_cancel_is_killed(self):
        """A stuck mapper (ignores "cancel" and the closed input) must not stay behind holding the
        controller: close() kills it after 2 seconds."""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "stuck_mapper.py"
            script.write_text("import time\nprint('started', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
            mapper = bc.MapperProcess(sys.executable, script, [], None)
            try:
                start = time.monotonic()
                mapper.close()
                elapsed = time.monotonic() - start
                self.assertIsNotNone(mapper.proc.poll())  # it is gone
                self.assertGreaterEqual(elapsed, 1.9)  # it got its 2 seconds
                self.assertLess(elapsed, 6)
            finally:
                if mapper.proc.poll() is None:
                    mapper.proc.kill()
                    mapper.proc.wait()


class LauncherButtonTests(unittest.TestCase):
    def test_map_buttons_open_one_controller_window(self):
        from test_basara4_launcher_window import launcher
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, _game, _), \
                mock.patch.object(bc.subprocess, "Popen") as popen, \
                mock.patch.object(bl.messagebox, "showerror") as showerror:
            self.assertIs(bc.subprocess, bl.subprocess)  # one mock sees every process either module starts
            app.map_pad([])
            showerror.assert_called_once()  # the mapper is missing: no window
            self.assertIsNone(getattr(app, "pad_window", None))
            paths.bin.mkdir()
            mapper = paths.bin / "basara4-pad-mapper.exe"
            mapper.write_bytes(b"MZ")
            app.map_pad([])
            window = app.pad_window
            window.attributes("-alpha", 0.0)
            self.assertIsInstance(window, bc.ControllerWindow)
            popen.assert_not_called()  # nothing runs before Map all
            app.map_pad(["--sticks"])
            self.assertIs(app.pad_window, window)  # one window at a time...
            self.assertTrue(window.sticks_only)  # ...which switches to the sticks (nothing runs there yet)
            self.assertEqual(list(window.rows.get_children()), ["leftx", "lefty", "rightx", "righty"])
            app.map_pad([])
            self.assertFalse(window.sticks_only)
            self.assertEqual(len(window.rows.get_children()), 21)
            popen.assert_not_called()  # no console mapper either
            window.map_all.invoke()
            popen.assert_called_once()
            call = popen.call_args
            self.assertEqual(call.args[0], [str(mapper), str(paths.mapping), "--ui"])
            self.assertEqual(call.kwargs["creationflags"], 0x08000000)  # CREATE_NO_WINDOW: no console
            self.assertTrue(call.kwargs["env"]["PATH"].startswith(str(paths.bin)))
            app.map_pad(["--sticks"])  # while it maps: the running mapping is not switched
            self.assertFalse(window.sticks_only)
            self.assertEqual(window.prompt.cget("text"), bc.TEXT["busy"])
            popen.assert_called_once()
            window.close()
            app.map_pad(["--sticks"])
            app.pad_window.attributes("-alpha", 0.0)
            self.assertTrue(app.pad_window.sticks_only)
            app.pad_window.map_all.invoke()
            self.assertEqual(popen.call_args.args[0], [str(mapper), str(paths.mapping), "--sticks", "--ui"])
            app.pad_window.close()


if __name__ == "__main__":
    unittest.main()
