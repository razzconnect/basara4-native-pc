"""Controller window: maps a controller without a console.

The mapper (bin\\basara4-pad-mapper.exe <mapping file> --ui) detects the inputs and writes
user\\gamepad_mapping.txt; the file format and the game side do not change. It sends one JSON event per
line on stdout and reads one command per line on stdin (select N, skip, cancel, redo <entry>,
flip <entry>, save). This window shows what it asks and what it recorded and sends those commands. No
console window opens: the mapper runs with CREATE_NO_WINDOW and is closed with the window.
"""
import json
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

CREATE_NO_WINDOW = 0x08000000

# The 21 entries in the mapper's order: SDL name, what to press on the controller, what the game gets.
ENTRIES = [
    ("a", "A (bottom)", "Cross"), ("b", "B (right)", "Circle"), ("x", "X (left)", "Square"),
    ("y", "Y (top)", "Triangle"), ("leftshoulder", "LB", "L1"), ("rightshoulder", "RB", "R1"),
    ("lefttrigger", "LT", "L2"), ("righttrigger", "RT", "R2"), ("back", "Back / View", "Touchpad (pause menu)"),
    ("start", "Start / Menu", "Options"), ("leftstick", "Left stick click", "L3"),
    ("rightstick", "Right stick click", "R3"), ("guide", "Guide / Home", "PS button"),
    ("dpup", "D-pad up", "D-pad up"), ("dpdown", "D-pad down", "D-pad down"),
    ("dpleft", "D-pad left", "D-pad left"), ("dpright", "D-pad right", "D-pad right"),
    ("leftx", "Left stick: push RIGHT", "Left stick"), ("lefty", "Left stick: push DOWN (toward you)", "Left stick"),
    ("rightx", "Right stick: push RIGHT", "Right stick"),
    ("righty", "Right stick: push DOWN (toward you)", "Right stick"),
]
STICKS = {"leftx", "lefty", "rightx", "righty"}

# Every text of the window in one place (English, like the launcher's error messages).
TEXT = {
    "title": "Controller",
    "idle": "Press Map all, then press each button on your controller when it is asked for.",
    "idle_saved": "A custom mapping is in use. Press Map all to make a new one.",
    "starting": "Looking for your controller...",
    "choose": "More than one controller is connected. Choose the one to map:",
    "button": "Press {pad} on your controller  ->  {game} in the game",
    "stick": "{pad}, then let it go back to the middle  ->  {game} in the game",
    "progress": "{n} of {total}  |  15 seconds without input skips this one",
    "used": "That input is already used by another entry. Let go and press the right one.",
    "stick_rotated": "The stick was turned or pushed back. Push it straight in the one direction shown, then let go.",
    "stick_two_axes": "The stick moved in two directions. Push it straight in the one direction shown, then let go.",
    "review": "All entries done. Check the list, then press Done to save.",
    "saved": "Saved. The game uses this mapping from its next start.",
    "cancelled": "Cancelled. Nothing was saved.",
    "ended": "The mapper stopped without saving.",
    "not_started": "The mapper could not start: {reason}",
    "set": "not set", "skipped": "skipped", "no_input": "skipped (no input)", "waiting": "press now",
    "map_all": "Map all", "map_sticks": "Map sticks", "skip": "Skip", "cancel": "Cancel", "reset": "Reset",
    "done": "Done", "use": "Use this controller",
}


class MapperProcess:
    """basara4-pad-mapper.exe --ui as a child process. Events are read on a thread and queued for the Tk
    thread; commands are written one per line. A line that is not JSON (a plain message) is ignored."""

    def __init__(self, exe, mapping, extra, env):
        self.events = queue.Queue()
        self.proc = subprocess.Popen(
            [str(exe), str(mapping), *extra, "--ui"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8",
            errors="replace", bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and "ev" in event:
                    self.events.put(event)
        except (OSError, ValueError):
            pass  # the pipe broke: the mapper is gone, and its exit code below says how it ended
        finally:
            self.proc.stdout.close()
        self.events.put({"ev": "exit", "code": self.proc.wait()})

    def send(self, command):
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False  # the mapper already ended; its "exit" event says how

    def close(self):
        """Ends the mapper without saving: "cancel", then closed input (which the mapper also reads as
        cancel). A mapper that does not end in 2 seconds is killed; it writes the file only on "save"."""
        if self.proc.poll() is None:
            self.send("cancel")
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class ControllerWindow(tk.Toplevel):
    """Map all / Skip / Cancel / Reset / Done, the list of entries, and what to press now."""

    def __init__(self, parent, exe, mapping, env, sticks_only=False, on_saved=None, on_reset=None,
                 colors=None):
        super().__init__(parent)
        self.exe, self.mapping, self.env, self.sticks_only = exe, mapping, env, sticks_only
        self.on_saved, self.on_reset = on_saved, on_reset
        self.mapper = None
        self.pump_job = None
        self.state = "idle"
        self.finished = False  # the mapper said saved or cancelled
        self.error_shown = False  # the mapper's last message was an error
        colors = colors or {}
        bg, panel = colors.get("bg", "#12100e"), colors.get("panel", "#1c1916")
        fg, accent = colors.get("fg", "#eadfcb"), colors.get("accent", "#c9a24a")
        self.title(TEXT["title"])
        self.configure(bg=bg)
        self.transient(parent)
        self.minsize(560, 480)

        banner = tk.Frame(self, bg=panel, padx=14, pady=10)
        banner.pack(fill="x", padx=12, pady=(12, 6))
        self.prompt = tk.Label(banner, text="", bg=panel, fg=accent, font=("Segoe UI", 12, "bold"),
                               wraplength=520, justify="left", anchor="w")
        self.prompt.pack(fill="x")
        self.detail = tk.Label(banner, text="", bg=panel, fg=fg, font=("Segoe UI", 9), wraplength=520,
                               justify="left", anchor="w")
        self.detail.pack(fill="x", pady=(4, 0))
        self.chooser = ttk.Frame(banner)
        self.device_var = tk.StringVar()
        self.device_list = ttk.Combobox(self.chooser, textvariable=self.device_var, state="readonly", width=48)
        self.device_list.pack(side="left")
        ttk.Button(self.chooser, text=TEXT["use"], command=self._use_device).pack(side="left", padx=(8, 0))
        self.devices = []

        self.buttons = ttk.Frame(self, padding=(12, 6, 12, 12))
        self.buttons.pack(side="bottom", fill="x")
        self.map_all = ttk.Button(self.buttons, text=TEXT["map_sticks" if sticks_only else "map_all"],
                                  command=self.start)
        self.skip = ttk.Button(self.buttons, text=TEXT["skip"], command=lambda: self._send("skip"))
        self.cancel = ttk.Button(self.buttons, text=TEXT["cancel"], command=lambda: self._send("cancel"))
        self.reset = ttk.Button(self.buttons, text=TEXT["reset"], command=self._reset)
        self.done = ttk.Button(self.buttons, text=TEXT["done"], command=self._done)
        for button in (self.map_all, self.skip, self.cancel, self.reset):
            button.pack(side="left", padx=(0, 8))
        self.done.pack(side="right")

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, padx=12)
        style = ttk.Style(self)  # the launcher's light text needs a dark list behind it
        style.configure("Pad.Treeview", background=panel, fieldbackground=panel, foreground=fg, rowheight=22,
                        borderwidth=0)
        style.map("Pad.Treeview", background=[("selected", "#3a342e")], foreground=[("selected", accent)])
        style.configure("Pad.Treeview.Heading", background=bg, foreground=accent, relief="flat")
        style.map("Pad.Treeview.Heading", background=[("active", bg)])
        self.rows = ttk.Treeview(table, columns=("pad", "game", "set"), show="headings", height=12,
                                 selectmode="browse", style="Pad.Treeview")
        for column, title, width in (("pad", "Controller", 230), ("game", "In game", 150), ("set", "Set", 120)):
            self.rows.heading(column, text=title)
            self.rows.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.rows.yview)
        self.rows.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.rows.pack(side="left", fill="both", expand=True)
        for name, pad, game in self._entries():
            self.rows.insert("", "end", iid=name, values=(pad, game, TEXT["set"]))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._show_state("idle")
        self._say(TEXT["idle_saved"] if mapping.exists() else TEXT["idle"])

    def _entries(self):
        return [entry for entry in ENTRIES if not self.sticks_only or entry[0] in STICKS]

    def _names(self):
        return [name for name, _pad, _game in self._entries()]

    # ---- the mapper ----
    def start(self):
        """Map all: runs the mapper; the list shows each entry as it is recorded."""
        if self.mapper:
            return
        for name, pad, game in self._entries():
            self.rows.item(name, values=(pad, game, TEXT["set"]))
        try:
            self.mapper = MapperProcess(self.exe, self.mapping, ["--sticks"] if self.sticks_only else [], self.env)
        except OSError as exc:
            self._say(TEXT["not_started"].format(reason=exc.strerror or exc))
            return
        self.finished = self.error_shown = False
        self._show_state("mapping")
        self._say(TEXT["starting"])
        self.pump_job = self.after(50, self._pump)

    def _pump(self):
        self.pump_job = None
        mapper = self.mapper
        if not mapper:
            return
        try:
            while self.mapper is mapper:
                self.handle(mapper.events.get_nowait())
        except queue.Empty:
            pass
        if self.mapper is mapper:
            self.pump_job = self.after(50, self._pump)

    def _send(self, command):
        if self.mapper:
            self.mapper.send(command)

    def handle(self, event):
        """One event from the mapper."""
        kind, target = event.get("ev"), event.get("target")
        if kind == "devices":
            self.devices = event.get("list") or []
            if len(self.devices) > 1:
                self.device_list["values"] = [d.get("name", "Gamepad") for d in self.devices]
                self.device_list.current(0)
                self._say(TEXT["choose"])
                self.chooser.pack(fill="x", pady=(8, 0))
        elif kind == "controller":
            self.chooser.pack_forget()
            self.detail.config(text=event.get("name", ""))
        elif kind == "prompt" and target in self._names():
            self.error_shown = False
            self._ask(target)
        elif kind in ("captured", "flipped") and target in self._names():
            self._set(target, event.get("binding", ""))
        elif kind == "timeout" and target in self._names():
            self._set(target, TEXT["no_input"])
        elif kind == "skipped" and target in self._names():
            self._set(target, TEXT["skipped"])
        elif kind == "used":
            self.detail.config(text=TEXT["used"])
        elif kind == "warn" and event.get("reason") in ("stick_rotated", "stick_two_axes"):
            self.detail.config(text=TEXT[event["reason"]])
        elif kind == "review":
            self._show_state("review")
            self._say(TEXT["review"])
        elif kind == "saved":
            self.finished = True
            self._say(TEXT["saved"])
            if self.on_saved:
                self.on_saved()
        elif kind == "cancelled":
            self.finished = True
            self._say(TEXT["cancelled"])
        elif kind == "error":
            # Also sent for a refused command (the mapper goes on), so it does not end the run here.
            self.error_shown = True
            self._say(event.get("message", ""))
        elif kind == "exit":
            # 0 saved, 2 cancelled, 1 failed with an error message; anything else is unexplained.
            code = event.get("code")
            explained = (code in (0, 2) and self.finished) or (code == 1 and self.error_shown)
            mapper, self.mapper = self.mapper, None
            if mapper:
                mapper.close()  # already ended: this only releases its input pipe
            if not explained:
                self._say(TEXT["ended"])
            self._show_state("idle")

    def _ask(self, target):
        pad, game = next((p, g) for n, p, g in ENTRIES if n == target)
        names = self._names()
        self.prompt.config(text=TEXT["stick" if target in STICKS else "button"].format(pad=pad, game=game))
        self.detail.config(text=TEXT["progress"].format(n=names.index(target) + 1, total=len(names)))
        self.rows.item(target, values=(pad, game, TEXT["waiting"]))
        self.rows.see(target)
        self.rows.selection_set(target)

    def _set(self, target, value):
        pad, game = next((p, g) for n, p, g in ENTRIES if n == target)
        self.rows.item(target, values=(pad, game, value))

    def _say(self, text):
        self.prompt.config(text=text)
        if not self.mapper:
            self.detail.config(text="")

    def _show_state(self, state):
        """idle: Map all, Reset, Done. mapping: Skip, Cancel. review: Done (saves), Cancel."""
        self.state = state
        enabled = {"idle": {self.map_all, self.reset, self.done}, "mapping": {self.skip, self.cancel},
                   "review": {self.done, self.cancel}}[state]
        for button in (self.map_all, self.skip, self.cancel, self.reset, self.done):
            button.state(["!disabled"] if button in enabled else ["disabled"])

    def _use_device(self):
        index = self.device_list.current()
        if 0 <= index < len(self.devices):
            self._send(f"select {self.devices[index].get('i', index)}")
            self.chooser.pack_forget()

    def _done(self):
        if self.state == "review":
            self._send("save")
        else:
            self.close()

    def _reset(self):
        if self.on_reset:
            self.on_reset()
        self._say(TEXT["idle_saved"] if self.mapping.exists() else TEXT["idle"])

    # ---- closing ----
    def close(self):
        """Closing never saves: a running mapper is cancelled."""
        self._stop()
        self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self._stop()  # also when the launcher closes with this window open

    def _stop(self):
        if self.pump_job:
            self.after_cancel(self.pump_job)
            self.pump_job = None
        mapper, self.mapper = self.mapper, None
        if mapper:
            mapper.close()
