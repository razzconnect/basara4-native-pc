"""The Controller window (launcher/basara4_controller.py) that maps a controller without a console.

The real mapper needs a controller, so a small Python script plays it here: it speaks the same protocol
(one JSON event per stdout line, one command per stdin line) through real pipes. No game files; the
mapping file is written into a temporary folder. The windows are made fully transparent.
"""
import json
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


class WindowEventTests(ControllerTestCase):
    def started(self, **kwargs):
        """A window whose mapper is a mock: handle() gets the events, sends are recorded."""
        window = self.window(**kwargs)
        mapper = mock.Mock()
        with mock.patch.object(bc, "MapperProcess", return_value=mapper) as process:
            window.start()
        return window, mapper, process

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
        self.assertIn("then let it go back to the middle", window.prompt.cget("text"))
        window.handle({"ev": "warn", "target": "leftx", "reason": "stick_rotated"})
        self.assertEqual(window.detail.cget("text"), bc.TEXT["stick_rotated"])
        window.handle({"ev": "warn", "target": "leftx", "reason": "stick_two_axes"})
        self.assertEqual(window.detail.cget("text"), bc.TEXT["stick_two_axes"])
        window.handle({"ev": "used", "target": "leftx", "binding": "a0"})
        self.assertEqual(window.detail.cget("text"), bc.TEXT["used"])
        window.handle({"ev": "captured", "target": "leftx", "binding": "a1~"})
        self.assertEqual([self.column(window, n) for n in ("a", "b", "x", "leftx", "y")],
                         ["b0", "skipped (no input)", "skipped", "a1~", "not set"])
        window.handle({"ev": "review", "mapping": "..."})
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
        self.assertIn("2 of 4", window.detail.cget("text"))
        window.handle({"ev": "prompt", "target": "a", "index": 0})  # not in this list: ignored
        self.assertIn("2 of 4", window.detail.cget("text"))

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
            self.assertIs(app.pad_window, window)  # one window at a time
            popen.assert_not_called()  # no console mapper either
            window.map_all.invoke()
            popen.assert_called_once()
            call = popen.call_args
            self.assertEqual(call.args[0], [str(mapper), str(paths.mapping), "--ui"])
            self.assertEqual(call.kwargs["creationflags"], 0x08000000)  # CREATE_NO_WINDOW: no console
            self.assertTrue(call.kwargs["env"]["PATH"].startswith(str(paths.bin)))
            window.close()
            app.map_pad(["--sticks"])
            app.pad_window.attributes("-alpha", 0.0)
            self.assertTrue(app.pad_window.sticks_only)
            app.pad_window.map_all.invoke()
            self.assertEqual(popen.call_args.args[0], [str(mapper), str(paths.mapping), "--sticks", "--ui"])
            app.pad_window.close()


if __name__ == "__main__":
    unittest.main()
