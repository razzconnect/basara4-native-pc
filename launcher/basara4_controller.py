"""Controller window: maps a controller without a console.

The mapper (bin\\basara4-pad-mapper.exe <mapping file> --ui) detects the inputs and writes
user\\gamepad_mapping.txt; the file format and the game side do not change. It sends one JSON event per
line on stdout and reads one command per line on stdin (select N, skip, cancel, redo <entry>,
flip <entry>, check left|right|off, save). This window shows what it asks and what it recorded and sends
those commands. No console window opens: the mapper runs with CREATE_NO_WINDOW and is closed with the
window.

Sticks get two extra views. While a stick is asked for, a guide shows the direction with a moving white
dot and a blinking arrow, and a green dot shows the player's own stick. After the stick steps, a check
shows what the game will receive (the mapper reads the stick through the recorded mapping, as the game
does): the player compares it with the thumb and can flip an axis or map the stick again. The program
cannot know which way the thumb went; the check only shows what the game will get.
"""
import json
import math
import queue
import subprocess
import threading
import time
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
# The four stick steps: entry, stick, direction to push, unit vector of that direction on screen.
STICK_STEPS = [("leftx", "Left stick", "RIGHT", (1, 0)), ("lefty", "Left stick", "DOWN (toward you)", (0, 1)),
               ("rightx", "Right stick", "RIGHT", (1, 0)), ("righty", "Right stick", "DOWN (toward you)", (0, 1))]
# The stick check: side sent to the mapper, stick, its two entries.
CHECK_SIDES = [("left", "left stick", "leftx", "lefty"), ("right", "right stick", "rightx", "righty")]

# Every text of the window in one place (English, like the launcher's error messages).
TEXT = {
    "title": "Controller",
    "idle": "Press Map all, then press each button on your controller when it is asked for.",
    "idle_saved": "A custom mapping is in use. Press Map all to make a new one.",
    "starting": "Looking for your controller...",
    "choose": "More than one controller is connected. Choose the one to map:",
    "button": "Press {pad} on your controller  ->  {game} in the game",
    "stick": "{stick} ({n} of 4): push it straight {dir} and hold, then let go",
    "stick_hint": "One straight push. Do not spin the stick in circles.",
    "stick_waiting": "Waiting for you to push {dir}...",
    "stick_moving": "Keep pushing {dir} until it stops, hold, then let go.",
    "progress": "{n} of {total}  |  15 seconds without input skips this one",
    "used": "That input is already used by another entry. Let go and press the right one.",
    "stick_rotated": "That was a circle or a push back. Push straight {dir} and hold, then let go.",
    "stick_two_axes": "The stick moved in two directions. Push straight {dir} and hold, then let go.",
    "check": "Check your {stick}: push it up-right. The dot must go up-right too.",
    "check_hint": "The dot and the label show what the game will receive. Compare them with your thumb. "
                  "If they go another way, tick Flip below.",
    "game_sees": "Game sees: {where}",
    "center": "CENTER",
    "flip_x": "Flip left / right", "flip_y": "Flip up / down",
    "looks_right": "Looks right", "redo_stick": "Redo this stick",
    "check_done": "Sticks checked. Press Done to save.",
    "review": "All entries done. Check the list, then press Done to save.",
    "saved": "Saved. The game uses this mapping from its next start.",
    "cancelled": "Cancelled. Nothing was saved.",
    "ended": "The mapper stopped without saving.",
    "not_started": "The mapper could not start: {reason}",
    "busy": "Finish or cancel the mapping in this window first.",
    "set": "not set", "skipped": "skipped", "no_input": "skipped (no input)", "waiting": "press now",
    "map_all": "Map all", "map_sticks": "Map sticks", "skip": "Skip", "cancel": "Cancel", "reset": "Reset",
    "done": "Done", "use": "Use this controller",
}

# The stick panel: canvas size, circle radius, animation timing (ms), and what counts as a direction.
GUIDE_SIZE, GUIDE_RADIUS = 180, 70
FRAME_MS, SLIDE_MS, BLINK_MS = 33, 2200, 1100
STICK_FULL, SEES_DEADZONE, PUSH_SEEN = 32768, 8000, 4000


def game_sees(x, y):
    """The direction the game receives from SDL values: y is negative when pushed up."""
    vertical = "UP" if y < -SEES_DEADZONE else "DOWN" if y > SEES_DEADZONE else ""
    horizontal = "LEFT" if x < -SEES_DEADZONE else "RIGHT" if x > SEES_DEADZONE else ""
    return "-".join(part for part in (vertical, horizontal) if part) or TEXT["center"]


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


class StickPanel(tk.Frame):
    """The circle of one stick: the guide (moving white dot, blinking arrow, the player's green dot) or
    the check (green dot = what the game receives, with its direction written under the circle)."""

    def __init__(self, parent, bg, fg, accent):
        super().__init__(parent, bg=bg)
        self.fg, self.accent, self.dim = fg, accent, "#5a5046"
        self.canvas = tk.Canvas(self, width=GUIDE_SIZE, height=GUIDE_SIZE, bg=bg, highlightthickness=0)
        self.canvas.pack(side="left", padx=(0, 14))
        c, r = GUIDE_SIZE / 2, GUIDE_RADIUS
        self.canvas.create_oval(c - r, c - r, c + r, c + r, outline=self.dim, width=2)
        self.canvas.create_line(c - r, c, c + r, c, fill=self.dim, dash=(2, 4))
        self.canvas.create_line(c, c - r, c, c + r, fill=self.dim, dash=(2, 4))
        for text, x, y in (("UP", c, c - r - 9), ("DOWN", c, c + r + 9), ("LEFT", c - r + 2, c - 9),
                           ("RIGHT", c + r - 2, c - 9)):
            self.canvas.create_text(x, y, text=text, fill=self.dim, font=("Segoe UI", 7))
        self.arrow = self.canvas.create_line(c, c, c, c, fill=accent, width=4, arrow="last", state="hidden")
        self.guide_dot = self.canvas.create_oval(0, 0, 0, 0, fill="#ffffff", outline="", state="hidden")
        self.live_dot = self.canvas.create_oval(0, 0, 0, 0, fill="#5fbf6a", outline="", state="hidden")
        self.side = tk.Frame(self, bg=bg)
        self.side.pack(side="left", fill="both", expand=True)
        self.direction = (1, 0)
        self.anim_job = None
        self.anim_start = 0.0

    @staticmethod
    def _place(canvas, item, x, y, radius):
        canvas.coords(item, x - radius, y - radius, x + radius, y + radius)

    @staticmethod
    def _in_circle(u, v):
        """(u, v) as a fraction of the radius, kept inside the circle (a full diagonal is 1.4 long)."""
        length = math.hypot(u, v)
        return (u / length, v / length) if length > 1 else (u, v)

    # ---- guide (a stick step) ----
    def guide(self, direction):
        """Shows the direction to push and starts the animation."""
        self.direction = direction
        c, r = GUIDE_SIZE / 2, GUIDE_RADIUS
        dx, dy = direction
        self.canvas.coords(self.arrow, c + dx * 22, c + dy * 22, c + dx * (r - 6), c + dy * (r - 6))
        self.canvas.itemconfigure(self.arrow, state="normal")
        self.canvas.itemconfigure(self.guide_dot, state="normal")
        self.canvas.itemconfigure(self.live_dot, state="normal")
        self.show_live(0, 0)
        self.anim_start = time.monotonic()
        if self.anim_job is None:
            self._animate()

    def _animate(self):
        """One frame: the white dot slides out and back (SLIDE_MS), the arrow blinks (BLINK_MS)."""
        self.anim_job = None
        if not self.canvas.winfo_exists():
            return  # the window is gone
        elapsed = (time.monotonic() - self.anim_start) * 1000
        out = (1 - math.cos(2 * math.pi * (elapsed % SLIDE_MS) / SLIDE_MS)) / 2
        c, dx, dy = GUIDE_SIZE / 2, *self.direction
        self._place(self.canvas, self.guide_dot, c + dx * out * (GUIDE_RADIUS - 8), c + dy * out * (GUIDE_RADIUS - 8), 8)
        bright = (elapsed % BLINK_MS) < BLINK_MS / 2
        self.canvas.itemconfigure(self.arrow, fill=self.accent if bright else self.dim)
        self.anim_job = self.after(FRAME_MS, self._animate)

    def stop(self):
        """Stops the animation: nothing runs while the panel is hidden or the window idles."""
        if self.anim_job is not None:
            self.after_cancel(self.anim_job)
            self.anim_job = None
        if self.canvas.winfo_exists():  # also called while the window is being destroyed
            self.canvas.itemconfigure(self.arrow, state="hidden")
            self.canvas.itemconfigure(self.guide_dot, state="hidden")

    def show_live(self, along, across):
        """The player's own stick in a step: how far along the asked direction, and how far it also
        moved another way (both 0..32768, shown on the circle)."""
        c, r = GUIDE_SIZE / 2, GUIDE_RADIUS
        dx, dy = self.direction
        a, s = self._in_circle(min(along, STICK_FULL) / STICK_FULL, min(across, STICK_FULL) / STICK_FULL)
        self._place(self.canvas, self.live_dot, c + (dx * a - dy * s) * r, c + (dy * a + dx * s) * r, 6)

    # ---- check ----
    def check(self):
        self.stop()
        self.canvas.itemconfigure(self.live_dot, state="normal")
        self.show_game(0, 0)

    def show_game(self, x, y):
        """What the game receives (SDL values, -32768..32767; y negative = up)."""
        c, r = GUIDE_SIZE / 2, GUIDE_RADIUS
        u, v = self._in_circle(max(-1.0, min(1.0, x / STICK_FULL)), max(-1.0, min(1.0, y / STICK_FULL)))
        self._place(self.canvas, self.live_dot, c + u * r, c + v * r, 7)

    def live_dot_center(self):
        x0, y0, x1, y1 = self.canvas.coords(self.live_dot)
        return (x0 + x1) / 2, (y0 + y1) / 2


class ControllerWindow(tk.Toplevel):
    """Map all / Skip / Cancel / Reset / Done, the list of entries, what to press now, the stick guide and
    the stick check."""

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
        self.asking = None  # the entry asked for now
        self.bindings = {}  # entry -> binding recorded in this run
        self.checks = None  # sticks still to check in this run (None: the check has not started)
        self.checks_total = 0  # sticks the check started with
        self.redo_left = 0  # reviews still to come from "Redo this stick" before the check comes back
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

        # the stick panel, shown during stick steps and the check
        self.stick_area = tk.Frame(self, bg=panel, padx=14, pady=10)
        self.stick = StickPanel(self.stick_area, panel, fg, accent)
        self.stick.pack(fill="x")
        side = self.stick.side
        self.steps = [tk.Label(side, text="", bg=panel, fg=fg, font=("Segoe UI", 9), anchor="w")
                      for _ in STICK_STEPS]
        self.you = tk.Label(side, text="", bg=panel, fg="#5fbf6a", font=("Segoe UI", 9), anchor="w",
                            wraplength=300, justify="left")
        self.sees = tk.Label(side, text="", bg=panel, fg="#5fbf6a", font=("Segoe UI", 11, "bold"), anchor="w")
        self.flip_x_var, self.flip_y_var = tk.BooleanVar(), tk.BooleanVar()
        self.flip_x = ttk.Checkbutton(side, text=TEXT["flip_x"], variable=self.flip_x_var,
                                      command=lambda: self._flip(2))
        self.flip_y = ttk.Checkbutton(side, text=TEXT["flip_y"], variable=self.flip_y_var,
                                      command=lambda: self._flip(3))
        self.check_buttons = ttk.Frame(side)
        self.looks_right = ttk.Button(self.check_buttons, text=TEXT["looks_right"], command=self._looks_right)
        self.redo_stick = ttk.Button(self.check_buttons, text=TEXT["redo_stick"], command=self._redo_stick)
        self.looks_right.pack(side="left", padx=(0, 8))
        self.redo_stick.pack(side="left")

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
        self._fill_rows()
        self.table = table

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._show_state("idle")
        self._say(TEXT["idle_saved"] if mapping.exists() else TEXT["idle"])

    def _entries(self):
        return [entry for entry in ENTRIES if not self.sticks_only or entry[0] in STICKS]

    def _names(self):
        return [name for name, _pad, _game in self._entries()]

    def _fill_rows(self):
        self.rows.delete(*self.rows.get_children())
        for name, pad, game in self._entries():
            self.rows.insert("", "end", iid=name, values=(pad, game, TEXT["set"]))

    def set_sticks_only(self, sticks_only):
        """Map controller / Map sticks only while this window is open: switches what Map all asks for,
        unless a mapping is running here (that one is finished or cancelled first)."""
        if self.mapper:
            self._say(TEXT["busy"])
            return False
        if sticks_only != self.sticks_only:
            self.sticks_only = sticks_only
            self.map_all.config(text=TEXT["map_sticks" if sticks_only else "map_all"])
            self._fill_rows()
        return True

    # ---- the mapper ----
    def start(self):
        """Map all: runs the mapper; the list shows each entry as it is recorded."""
        if self.mapper:
            return
        self._fill_rows()
        try:
            self.mapper = MapperProcess(self.exe, self.mapping, ["--sticks"] if self.sticks_only else [], self.env)
        except OSError as exc:
            self._say(TEXT["not_started"].format(reason=exc.strerror or exc))
            return
        self.finished = self.error_shown = False
        self.bindings, self.checks, self.asking, self.checks_total, self.redo_left = {}, None, None, 0, 0
        self._show_state("mapping")
        self._say(TEXT["starting"])
        self.pump_job = self.after(FRAME_MS, self._pump)

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
            self.pump_job = self.after(FRAME_MS, self._pump)

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
            self.bindings[target] = event.get("binding", "")
            self._set(target, event.get("binding", ""))
        elif kind == "timeout" and target in self._names():
            self.bindings.pop(target, None)
            self._set(target, TEXT["no_input"])
        elif kind == "skipped" and target in self._names():
            self.bindings.pop(target, None)
            self._set(target, TEXT["skipped"])
        elif kind == "used":
            self.detail.config(text=TEXT["used"])
        elif kind == "warn" and event.get("reason") in ("stick_rotated", "stick_two_axes"):
            step = self._step(target)
            self.detail.config(text=TEXT[event["reason"]].format(dir=step[2] if step else "in the direction shown"))
        elif kind == "live":
            self._live(target, event)
        elif kind == "review":
            self._review()
        elif kind == "saved":
            self.finished = True
            self._hide_sticks()
            self._say(TEXT["saved"])
            if self.on_saved:
                self.on_saved()
        elif kind == "cancelled":
            self.finished = True
            self._hide_sticks()
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
            self._hide_sticks()
            if not explained:
                self._say(TEXT["ended"])
            self._show_state("idle")

    @staticmethod
    def _step(target):
        return next((step for step in STICK_STEPS if step[0] == target), None)

    def _ask(self, target):
        pad, game = next((p, g) for n, p, g in ENTRIES if n == target)
        names = self._names()
        self.asking = target
        self._show_state("mapping")
        step = self._step(target)
        if step:
            _name, stick, direction, vector = step
            self.prompt.config(text=TEXT["stick"].format(stick=stick, n=STICK_STEPS.index(step) + 1, dir=direction))
            self.detail.config(text=TEXT["stick_hint"])
            self._show_guide(target, vector)
        else:
            self._hide_sticks()
            self.prompt.config(text=TEXT["button"].format(pad=pad, game=game))
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
        """idle: Map all, Reset, Done. mapping: Skip, Cancel. review: Done (saves), Cancel.
        check: Cancel (the check's own buttons lead on to review)."""
        self.state = state
        enabled = {"idle": {self.map_all, self.reset, self.done}, "mapping": {self.skip, self.cancel},
                   "review": {self.done, self.cancel}, "check": {self.cancel}}[state]
        for button in (self.map_all, self.skip, self.cancel, self.reset, self.done):
            button.state(["!disabled"] if button in enabled else ["disabled"])

    # ---- the stick panel ----
    def _show_stick_area(self):
        if not self.stick_area.winfo_manager():
            self.stick_area.pack(fill="x", padx=12, pady=(0, 6), before=self.table)

    def _hide_sticks(self):
        self.stick.stop()
        self.stick_area.pack_forget()

    def _show_guide(self, target, vector):
        for widget in (self.sees, self.flip_x, self.flip_y, self.check_buttons):
            widget.pack_forget()
        current = [step[0] for step in STICK_STEPS].index(target)
        for index, (label, (_name, stick, direction, _v)) in enumerate(zip(self.steps, STICK_STEPS)):
            marker = "► " if index == current else "   "
            when = "now" if index == current else "done" if index < current else "next"
            label.config(text=f"{marker}{index + 1}. {stick} → push {direction}  ({when})",
                         fg=self.stick.accent if index == current else self.stick.fg)
            label.pack(fill="x")
        self.you.config(text=TEXT["stick_waiting"].format(dir=self._step(target)[2]))
        self.you.pack(fill="x", pady=(8, 0))
        self._show_stick_area()
        self.stick.guide(vector)

    def _live(self, target, event):
        if self.state == "mapping" and target == self.asking and self._step(target):
            push, side = int(event.get("push", 0)), int(event.get("side", 0))
            self.stick.show_live(push, side)
            direction = self._step(target)[2]
            self.you.config(text=TEXT["stick_moving" if push >= PUSH_SEEN else "stick_waiting"].format(dir=direction))
        elif self.state == "check" and self.checks and target == self.checks[0][0]:
            x, y = int(event.get("x", 0)), int(event.get("y", 0))
            self.stick.show_game(x, y)
            self.sees.config(text=TEXT["game_sees"].format(where=game_sees(x, y)))

    # ---- the check ----
    def _review(self):
        """The mapper waits for the window. After the stick steps the check starts; a flip during the
        check also ends in a review (the check goes on); after "Redo this stick" the check comes back
        once both axes were asked again."""
        self.asking = None
        if self.state == "check":
            return  # a flip: the mapper reads the stick with the new mapping, the check goes on
        if self.redo_left:
            self.redo_left -= 1
            if self.redo_left:
                return  # the other axis of the stick is asked next
        if self.checks is None:  # the stick steps are done: check the sticks that were set
            self.checks = [side for side in CHECK_SIDES
                           if self.bindings.get(side[2], "").startswith("a")
                           or self.bindings.get(side[3], "").startswith("a")]
            self.checks_total = len(self.checks)
        if self.checks:
            self._start_check()
        else:
            self._hide_sticks()
            self._show_state("review")
            self._say(TEXT["check_done"] if self.checks_total else TEXT["review"])

    def _start_check(self):
        side, stick, x_entry, y_entry = self.checks[0]
        self._show_state("check")
        self.prompt.config(text=TEXT["check"].format(stick=stick))
        self.detail.config(text=TEXT["check_hint"])
        for label in self.steps:
            label.pack_forget()
        self.you.pack_forget()
        self.flip_x_var.set(False)
        self.flip_y_var.set(False)
        self.flip_x.state(["!disabled"] if self.bindings.get(x_entry, "").startswith("a") else ["disabled"])
        self.flip_y.state(["!disabled"] if self.bindings.get(y_entry, "").startswith("a") else ["disabled"])
        self.sees.config(text=TEXT["game_sees"].format(where=TEXT["center"]))
        for widget in (self.sees, self.flip_x, self.flip_y):
            widget.pack(fill="x", pady=(0, 4))
        self.check_buttons.pack(fill="x", pady=(8, 0))
        self._show_stick_area()
        self.stick.check()
        self._send(f"check {side}")

    def _flip(self, column):
        """column 2: the stick's x entry, 3: its y entry. Each tick or untick turns that axis around."""
        if self.checks:
            self._send(f"flip {self.checks[0][column]}")

    def _looks_right(self):
        if not self.checks:
            return
        self.checks.pop(0)
        if self.checks:
            self._start_check()
        else:
            self._send("check off")
            self._hide_sticks()
            self._show_state("review")
            self._say(TEXT["check_done"])

    def _redo_stick(self):
        """Maps both axes of this stick again; the check comes back after them."""
        if not self.checks:
            return
        _side, _stick, x_entry, y_entry = self.checks[0]
        self.redo_left = 2
        self._send("check off")  # the check pauses while the stick is asked for
        for entry in (x_entry, y_entry):
            self._send(f"redo {entry}")

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
        self.stick.stop()
        if self.pump_job:
            self.after_cancel(self.pump_job)
            self.pump_job = None
        mapper, self.mapper = self.mapper, None
        if mapper:
            mapper.close()
