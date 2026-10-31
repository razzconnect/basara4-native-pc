"""Basara 4 PC launcher - by Razz.

Runs Sengoku Basara 4 Sumeragi (PS4, CUSA01159) on the native runtime in bin\\ (basara4-core.exe), a fork of
SpartanPort. Free software under the GNU GPL v2 or later; the credits are in NOTICE.md.

Everything lives next to this program:
  bin\\   the runtime (basara4-core.exe) and its DLLs
  game\\  the game dump (the folder with eboot.bin); another folder can be chosen
  out\\   the prepared game image, made automatically from eboot.bin
  user\\  saves, shader cache, settings, the log
"""
import hashlib
import importlib.util
import json
import os
import re
import runpy
import ssl
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from basara4_dlc import scan_extracted_dlc

FROZEN = getattr(sys, "frozen", False)
ROOT = (Path(sys.executable) if FROZEN else Path(__file__)).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
try:
    os.chdir(str(ROOT))
except OSError:
    pass
BIN, OUT, USER, SCRIPTS = ROOT / "bin", ROOT / "out", ROOT / "user", ROOT / "scripts"
CONFIG = USER / "launcher.json"
INI = USER / "basara4.ini"
OLD_INI = USER / "spartanport.ini"
WINDOW_ICON = USER / "window_icon.bmp"
CORE = BIN / "basara4-core.exe"
VERSION = "1.0.5"
BRAND = "Basara 4 PC"
AUTHOR = "Razz"
GITHUB_REPO = "razzconnect/basara4-native-pc"


FATAL_PATTERNS = [
    r"(STOP: .*)",
    r"(Fault: .*)",
    r"(ASSERT_MSG.*)",
    r"(vk::Result=.*)",
    r"(VK_ERROR_\w+)",
    r"(GPU \[Render\.Vulkan\] <Error> .*)",
    r"(GPU \[Render\.Vulkan\] <Warning> .*)",
    r"(GPU \[Lib\.\w+\] <Error> .*)",
    r"(Runtime: open\(.*?\) -> not found)",
    r"(guest raised debug exception .*)",
    r"(cannot create \d+ MiB of guest memory .*)",
    r"(error 1455.*)",
    r"(ERROR_COMMITMENT_LIMIT.*)",
    r"(no rewrite table matches the image.*)",
    r"(missing MOVBE, BMI1, LZCNT.*)",
]


def extract_crash_summary(log_path, max_context_lines=10):
    """Parses log from bottom-up and extracts fatal signatures and tail context."""
    if not log_path.is_file():
        return {"detected_errors": ["File log tidak ditemukan (game belum sempat menulis log)."], "tail_context": ""}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"detected_errors": [f"Gagal membaca log: {e}"], "tail_context": ""}
    if not lines:
        return {"detected_errors": ["Log kosong (game berhenti sebelum inisialisasi)."], "tail_context": ""}

    tail_lines = lines[-max_context_lines:]
    detected = []
    for line in reversed(lines):
        for pat in FATAL_PATTERNS:
            m = re.search(pat, line)
            if m:
                matched_str = m.group(1).strip()
                if matched_str not in detected:
                    detected.append(matched_str)
        if len(detected) >= 4:
            break
    return {
        "detected_errors": detected if detected else ["Tidak ada fatal signature terdaftar (game keluar mendadak)."],
        "tail_context": "\n".join(tail_lines),
        "total_lines": len(lines)
    }


def check_latest_release(repo=GITHUB_REPO):
    """Checks the latest GitHub release. Returns dict of asset info or None."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "Basara4-PC-Launcher"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if parse_version(tag) > parse_version(VERSION):
            for a in data.get("assets", []):
                name = a.get("name", "")
                if name.endswith(".zip") and ("basara4" in name.lower() or "windows" in name.lower()):
                    return {
                        "tag": tag,
                        "url": a.get("browser_download_url"),
                        "name": name,
                        "size": a.get("size", 0),
                        "body": data.get("body", ""),
                    }
            for a in data.get("assets", []):
                if a.get("name", "").endswith(".zip"):
                    return {
                        "tag": tag,
                        "url": a.get("browser_download_url"),
                        "name": name,
                        "size": a.get("size", 0),
                        "body": data.get("body", ""),
                    }
    except Exception:
        pass
    return None
LOG = USER / "last_run.log"
PREPARE_LOG = USER / "prepare.log"
MAPPING = USER / "gamepad_mapping.txt"
AUDIO_LOG = USER / "audio_debug.log"
AUDIO_WAV = USER / "audio_wav"  # SPARTAN_AUDIO_WAV_DIR: float WAV capture of the mix and of long voices
STAMP = OUT / "prepared.json"
DEFAULT_DLC_DIR = ROOT.parent / "DLC"
if not DEFAULT_DLC_DIR.is_dir():
    DEFAULT_DLC_DIR = USER / "dlc"
WAV_WARNING = ("WAV capture saves the final mix and every long voice as uncompressed audio in user\\audio_wav: about 70 MB per "
               "minute, up to 3 GB for one session.\n\nUse it only to find an audio problem, and delete the files afterwards.\n\n"
               "It applies to the next game start only and switches itself off after that. It also turns on the audio debug "
               "log. Turn it on?")
PIPELINE = [("prepare.py", []), ("link_libc.py", []), ("link_modules.py", []),
            ("content_profile.py", ["--sku", "full"]),
            ("isa_rewrite.py", [])]
NO_WINDOW = 0x08000000


def wav_captures():
    """The audio captures an earlier session left in user\\audio_wav: (files, total bytes)."""
    files = sorted(AUDIO_WAV.glob("*.wav")) if AUDIO_WAV.is_dir() else []
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return files, total


def format_size(n):
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


def check_commit_limit_mb():
    """Returns available virtual memory (page file commit limit) in MB, or None if unavailable."""
    try:
        import ctypes
        from ctypes import wintypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullAvailPageFile // (1024 * 1024)
    except Exception:
        pass
    return None


def run_script_mode():
    """`launcher.exe --run-script NAME args...`: runs one of the preparation scripts in this process."""
    script = SCRIPTS / sys.argv[2]
    sys.path.insert(0, str(SCRIPTS))
    sys.argv = [str(script)] + sys.argv[3:]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_:
        code = exit_.code
        sys.exit(code if isinstance(code, int) else (0 if code is None else 1))


if len(sys.argv) > 2 and sys.argv[1] == "--run-script":
    run_script_mode()
    sys.exit(0)

import tkinter as tk  # noqa: E402  (after the script mode, which needs no window)
from tkinter import filedialog, messagebox, ttk  # noqa: E402

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageTk
except ImportError:  # still works, just without artwork
    Image = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basara4_patches  # noqa: E402

DEFAULTS = {"game": "", "dlc_dir": str(DEFAULT_DLC_DIR),
            "display_mode": "borderless", "vsync": True, "fps_limit": 60, "rcas": False,
            "anisotropy": 0, "resolution": "native", "upscale": "off", "audio_log": False, "audio_wav": False,
            "gpu_index": -1, "keymap": {}, "mouse_camera": True, "mouse_sens": 100}

DEFAULT_PC_KEYMAP = {
    "square": "Mouse Left",       # Normal Attack (Serangan Biasa)
    "circle": "Mouse Right",      # Heavy / Special Attack / Basara Skill
    "triangle": "Q",              # Secondary Skill (Khas Hack & Slash)
    "cross": "Space",             # Jump / Lompat
    "l1": "Left Shift",           # Guard / Tangkis / Evade
    "r1": "E",                    # Special Skill 2 (Interact / Skill Kanan)
    "r2": "F",                    # Basara Attack (Ultimate Finisher)
    "l2": "R",                    # Sengoku Drive / Poise Boost (Rage/Burst)
    "touchpad": "Tab",            # Taunt / Provoke
    "options": "Escape",          # Pause / Menu
    "up": "Up",                   # D-Pad Up
    "down": "Down",               # D-Pad Down
    "left": "Left",               # D-Pad Left
    "right": "Right",             # D-Pad Right
    "l3": "Z",                    # L3
    "r3": "C",                    # R3 (Lock-On)
}

ACTION_DESCRIPTIONS = [
    ("square", "Normal Attack (Serangan Biasa)", "Kotak / Square"),
    ("triangle", "Special Skill 1 (Skill Utama)", "Segitiga / Triangle"),
    ("cross", "Jump (Lompat)", "Silang / Cross"),
    ("circle", "Basara Attack (Ultimate)", "Lingkaran / Circle"),
    ("l1", "Guard / Tangkis / Dash", "L1"),
    ("r1", "Special Skill 2 (Skill Tambahan)", "R1"),
    ("r2", "Special Skill 3 (Skill Karakter)", "R2"),
    ("l2", "Sengoku Drive / Boost", "L2"),
    ("touchpad", "Taunt / Provoke", "Touchpad / Tab"),
    ("options", "Pause / In-Game Menu", "Options / Enter"),
    ("up", "D-Pad Up (Navigasi / Perintah)", "D-Pad Atas"),
    ("down", "D-Pad Down (Navigasi / Perintah)", "D-Pad Bawah"),
    ("left", "D-Pad Left (Navigasi / Perintah)", "D-Pad Kiri"),
    ("right", "D-Pad Right (Navigasi / Perintah)", "D-Pad Kanan"),
    ("l3", "L3 Action", "L3"),
    ("r3", "R3 Action / Lock-on", "R3"),
]


def enumerate_gpus():
    """Enumerate system Vulkan GPUs using vulkan-1.dll. Returns list of (index, display_name, is_discrete)."""
    gpus = [(-1, "Auto (Prioritaskan GPU Diskrit)", True)]
    try:
        import ctypes
        vulkan = ctypes.cdll.LoadLibrary("vulkan-1.dll")
        class VkApplicationInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p),
                ("pApplicationName", ctypes.c_char_p), ("applicationVersion", ctypes.c_uint32),
                ("pEngineName", ctypes.c_char_p), ("engineVersion", ctypes.c_uint32),
                ("apiVersion", ctypes.c_uint32),
            ]
        class VkInstanceCreateInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p),
                ("flags", ctypes.c_uint32), ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
                ("enabledLayerCount", ctypes.c_uint32), ("ppEnabledLayerNames", ctypes.c_void_p),
                ("enabledExtensionCount", ctypes.c_uint32), ("ppEnabledExtensionNames", ctypes.c_void_p),
            ]
        class VkPhysicalDeviceProperties(ctypes.Structure):
            _fields_ = [
                ("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32),
                ("vendorID", ctypes.c_uint32), ("deviceID", ctypes.c_uint32),
                ("deviceType", ctypes.c_uint32), ("deviceName", ctypes.c_char * 256),
                ("pipelineCacheUUID", ctypes.c_ubyte * 16), ("limits", ctypes.c_byte * 504),
                ("sparseProperties", ctypes.c_byte * 20),
            ]
        app_info = VkApplicationInfo(sType=1, pApplicationName=b"Basara4Launcher", apiVersion=(1 << 22) | (2 << 12))
        ci = VkInstanceCreateInfo(sType=2, pApplicationInfo=ctypes.pointer(app_info))
        vulkan.vkCreateInstance.argtypes = [ctypes.POINTER(VkInstanceCreateInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        vulkan.vkCreateInstance.restype = ctypes.c_int32
        vulkan.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vulkan.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p)]
        vulkan.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
        vulkan.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(VkPhysicalDeviceProperties)]

        inst = ctypes.c_void_p()
        if vulkan.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(inst)) == 0:
            count = ctypes.c_uint32(0)
            vulkan.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None)
            if count.value > 0:
                devs = (ctypes.c_void_p * count.value)()
                vulkan.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), devs)
                for i in range(count.value):
                    p = VkPhysicalDeviceProperties()
                    vulkan.vkGetPhysicalDeviceProperties(devs[i], ctypes.byref(p))
                    name = p.deviceName.decode("utf-8", errors="replace").strip("\x00")
                    is_disc = (p.deviceType == 2)
                    kind_tag = "dGPU" if is_disc else ("iGPU" if p.deviceType == 1 else "GPU")
                    gpus.append((i, f"GPU {i}: {name} ({kind_tag})", is_disc))
            vulkan.vkDestroyInstance(inst, None)
    except Exception:
        pass
    return gpus


SYSTEM_GPUS = enumerate_gpus()
DISPLAY_MODES = [("Windowed", "windowed"), ("Borderless fullscreen", "borderless"),
                 ("Exclusive fullscreen", "fullscreen")]
FPS_LIMITS = [("60 (game default)", 60), ("90", 90), ("120", 120), ("144", 144), ("165", 165),
              ("240", 240), ("Unlimited", 0)]
RESOLUTIONS = [("1920 x 1080  (native)", "native"), ("1280 x 720", "720p"), ("1600 x 900", "900p"),
               ("2560 x 1440", "1440p"), ("3200 x 1800", "1800p"), ("3840 x 2160  (4K)", "4K")]
UPSCALERS = [("Off  (plain scaling)", "off"), ("FSR 1  (sharper)", "fsr1")]
ANISO = [("Off", 0), ("2x", 2), ("4x", 4), ("8x", 8), ("16x", 16)]
BG, PANEL, FG, MUTED, GOLD, RED = "#12100e", "#1c1916", "#eadfcb", "#8d8374", "#c9a24a", "#9c2b24"
RAZZ = "#e0526a"  # the accent of the by-Razz mark


def find_game():
    """The first folder under game\\ that holds an eboot.bin."""
    base = ROOT / "game"
    if (base / "eboot.bin").is_file():
        return str(base)
    for child in sorted(base.iterdir()) if base.is_dir() else []:
        if (child / "eboot.bin").is_file():
            return str(child)
    return ""


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        pass
    if not cfg["game"] or not (Path(cfg["game"]) / "eboot.bin").is_file():
        cfg["game"] = find_game()
    return cfg


def save_config(cfg):
    USER.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def read_ini(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, value = line.strip().split("=", 1)
                values[key] = value
    except OSError:
        pass
    return values


def write_ini(path, updates):
    """Update only the given keys; every other line the runtime wrote stays as it was."""
    path = Path(path)
    lines, seen = [], set()
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key = line.split("=", 1)[0] if "=" in line else None
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                lines.append(line)
    except OSError:
        pass
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def image_ready(game):
    """Whether out\\ holds an image made from this game's current eboot.bin."""
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8-sig"))
        return (stamp.get("eboot_sha256") == file_sha256(Path(game) / "eboot.bin")
                and all((OUT / name).is_file() for name in ("boot-linked.bin", "content.bin", "eboot.elf")))
    except (OSError, ValueError):
        return False


def prepare_image(game, progress):
    """Runs the preparation scripts on the game's eboot.bin. Returns an error text or None."""
    OUT.mkdir(parents=True, exist_ok=True)
    USER.mkdir(parents=True, exist_ok=True)
    with open(PREPARE_LOG, "w", encoding="utf-8", errors="replace") as log:
        for index, (name, extra) in enumerate(PIPELINE, 1):
            progress(f"Preparing the game image ({index}/{len(PIPELINE)}): {name[:-3]}…")
            script = [sys.executable, "--run-script", name] if FROZEN else [sys.executable, str(SCRIPTS / name)]
            result = subprocess.run(script + [str(game), "--out", str(OUT)] + extra, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            if result.returncode:
                return f"{name} failed (exit {result.returncode}). See user\\prepare.log"
    STAMP.write_text(json.dumps({"eboot_sha256": file_sha256(Path(game) / "eboot.bin"), "game": str(game)}),
                     encoding="utf-8")
    return None


def prepare_content_profile(game, dlc_dir):
    """Refresh AppContent metadata for every launch, even when the game image is cached."""
    script = [sys.executable, "--run-script", "content_profile.py"] if FROZEN else [
        sys.executable, str(SCRIPTS / "content_profile.py")]
    command = script + [str(game), "--out", str(OUT), "--sku", "full", "--dlc-dir", str(dlc_dir)]
    USER.mkdir(parents=True, exist_ok=True)
    with open(PREPARE_LOG, "a", encoding="utf-8", errors="replace") as log:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)
    if result.returncode:
        return f"content_profile.py failed (exit {result.returncode}). See user\\prepare.log"
    return None


class Launcher(tk.Tk):
    def __init__(self, play_now=False):
        super().__init__()
        self.cfg = load_config()
        self.proc = None
        self.busy = False
        self.pending = None
        self.play_now = play_now
        self.title(f"Sengoku Basara 4 Sumeragi  \u00b7  {BRAND} by {AUTHOR}")
        self.configure(bg=BG)
        self.geometry("1060x650")
        self.minsize(1020, 620)
        icon = Path(__file__).resolve().parent / "basara4.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass
        self._style()
        self._art()
        self._build()
        self._load_ini_into_ui()
        self._refresh_image_label()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(500, self._poll)
        self.update_info = None
        self.update_btn = None
        if play_now:
            self.iconify()
            self.after(300, self.on_play)
        else:
            self.after(700, self._check_wav_leftovers)
            self.after(1200, lambda: threading.Thread(target=self._check_update_thread, daemon=True).start())

    # ---- look ----
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Head.TLabel", background=PANEL, foreground=GOLD, font=("Segoe UI Semibold", 11))
        s.configure("TRadiobutton", background=PANEL, foreground=FG)
        s.configure("TCheckbutton", background=PANEL, foreground=FG)
        s.map("TRadiobutton", background=[("active", PANEL)], foreground=[("active", GOLD)])
        s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", GOLD)])
        s.configure("TButton", background="#2a2622", foreground=FG, padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("active", "#3a342e"), ("disabled", "#1c1916")],
              foreground=[("disabled", MUTED)])
        s.configure("Play.TButton", background=RED, foreground="#fff", font=("Segoe UI Semibold", 15),
                    padding=(10, 12))
        s.map("Play.TButton", background=[("active", "#b8362d"), ("disabled", "#4a2522")])
        s.configure("TCombobox", arrowcolor=GOLD, selectbackground=PANEL, selectforeground=FG)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)

    def _art(self):
        self.cover = self.bg_img = None
        if Image is None or not self.cfg["game"]:
            return
        sys_dir = Path(self.cfg["game"]) / "sce_sys"
        try:
            self.cover = ImageTk.PhotoImage(Image.open(sys_dir / "icon0.png").convert("RGB").resize((220, 220)))
        except OSError:
            pass
        try:
            raw_bg = Image.open(sys_dir / "pic1.png").convert("RGB")
            # Scale & crop to fill 1060x650 cleanly without borders
            w, h = 1060, 650
            scale = max(w / raw_bg.width, h / raw_bg.height)
            nw, nh = int(raw_bg.width * scale), int(raw_bg.height * scale)
            bg = raw_bg.resize((nw, nh), Image.LANCZOS)
            x, y = (nw - w) // 2, (nh - h) // 2
            bg = bg.crop((x, y, x + w, y + h))
            bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(6))).enhance(0.25)
            self.bg_img = ImageTk.PhotoImage(bg)
        except OSError:
            pass

    # ---- layout ----
    def _build(self):
        if self.bg_img:
            tk.Label(self, image=self.bg_img, bd=0, bg=BG).place(x=0, y=0, relwidth=1, relheight=1)
        root = ttk.Frame(self, style="TFrame")
        root.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root, style="Panel.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="ns", padx=(16, 14), pady=16)
        if self.cover:
            tk.Label(left, image=self.cover, bd=0, bg=PANEL).pack()
        ttk.Label(left, text="戦国BASARA4 皇", style="Head.TLabel").pack(pady=(12, 0))
        ttk.Label(left, text="Sengoku Basara 4 Sumeragi", style="Panel.TLabel").pack()
        ttk.Label(left, text="CUSA01159", style="Muted.TLabel").pack()
        self.status = ttk.Label(left, text="Ready", style="Muted.TLabel", wraplength=220)
        self.status.pack(pady=(14, 4))
        self.play = ttk.Button(left, text="PLAY", style="Play.TButton", command=self.on_play)
        self.play.pack(fill="x", pady=(2, 6))
        self.user_stopped = False
        self.stop = ttk.Button(left, text="Stop game", command=self.on_stop, state="disabled")
        self.stop.pack(fill="x")
        mark = ttk.Frame(left, style="Panel.TFrame")
        mark.pack(side="bottom", pady=(18, 0))
        tk.Label(mark, text=BRAND.upper(), bg=PANEL, fg=GOLD, font=("Georgia", 11, "bold")).pack()
        tk.Label(mark, text=f"by {AUTHOR}", bg=PANEL, fg=RAZZ, font=("Georgia", 12, "italic")).pack()
        self.version_label = tk.Label(mark, text=f"v{VERSION}", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.version_label.pack()
        self.mark_frame = mark

        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="Credits", command=self.show_credits).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(btn_row, text="Check Update", command=self.check_update_manual).pack(side="right", fill="x", expand=True, padx=(2, 0))

        right = ttk.Frame(root, style="Panel.TFrame", padding=18)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=16)
        right.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(right, text="Game", style="Head.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.game_var = tk.StringVar(value=self.cfg["game"])
        self.game_var.trace_add("write", lambda *_: self._refresh_image_label())
        ttk.Label(right, text="Game folder", style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(right, textvariable=self.game_var).grid(row=r, column=1, sticky="ew", padx=8)
        ttk.Button(right, text="Browse…", command=self._browse).grid(row=r, column=2)
        r += 1
        self.image_label = ttk.Label(right, text="", style="Muted.TLabel")
        self.image_label.grid(row=r, column=1, sticky="w", padx=8)
        r += 1

        ttk.Separator(right).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1
        ttk.Label(right, text="Display", style="Head.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.mode_var = tk.StringVar(value=self.cfg["display_mode"])
        modes = ttk.Frame(right, style="Panel.TFrame")
        modes.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        for label, key in DISPLAY_MODES:
            ttk.Radiobutton(modes, text=label, value=key, variable=self.mode_var).pack(side="left", padx=(0, 18))
        r += 1

        opts = ttk.Frame(right, style="Panel.TFrame")
        opts.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)

        # Baris 0: Graphics Card (Col 0: Label, Col 1..3: Combobox melebar rapi)
        ttk.Label(opts, text="Graphics card", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.gpu_var = tk.StringVar()
        gpu_display_names = [name for _, name, _ in SYSTEM_GPUS]
        ttk.Combobox(opts, textvariable=self.gpu_var, state="readonly", width=46,
                     values=gpu_display_names).grid(row=0, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(0, 8))

        # Baris 1: Frame limit & Anisotropic
        ttk.Label(opts, text="Frame limit", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.fps_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.fps_var, state="readonly", width=18,
                     values=[n for n, _ in FPS_LIMITS]).grid(row=1, column=1, sticky="w", padx=(8, 20), pady=4)
        ttk.Label(opts, text="Anisotropic", style="Panel.TLabel").grid(row=1, column=2, sticky="w", pady=4)
        self.aniso_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.aniso_var, state="readonly", width=12,
                     values=[n for n, _ in ANISO]).grid(row=1, column=3, sticky="w", padx=(8, 0), pady=4)

        # Baris 2: Window size & Upscaling
        ttk.Label(opts, text="Window size", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.res_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.res_var, state="readonly", width=18,
                     values=[n for n, _ in RESOLUTIONS]).grid(row=2, column=1, sticky="w", padx=(8, 20), pady=4)
        ttk.Label(opts, text="Upscaling", style="Panel.TLabel").grid(row=2, column=2, sticky="w", pady=4)
        self.up_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.up_var, state="readonly", width=12,
                     values=[n for n, _ in UPSCALERS]).grid(row=2, column=3, sticky="w", padx=(8, 0), pady=4)

        # Baris 3: Checkboxes (VSync, RCAS, Audio log, WAV)
        self.vsync_var = tk.BooleanVar(value=self.cfg["vsync"])
        self.rcas_var = tk.BooleanVar(value=self.cfg["rcas"])
        self.audio_var = tk.BooleanVar(value=self.cfg.get("audio_log", False))
        self.wav_var = tk.BooleanVar(value=False)

        chk_frame = ttk.Frame(opts, style="Panel.TFrame")
        chk_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 4))
        ttk.Checkbutton(chk_frame, text="VSync", variable=self.vsync_var).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(chk_frame, text="Sharpening (RCAS)", variable=self.rcas_var).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(chk_frame, text="Audio debug log", variable=self.audio_var, command=self._audio_toggled).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(chk_frame, text="+ WAV capture (up to 3 GB)", variable=self.wav_var, command=self._wav_toggled).pack(side="left")

        # Baris 4: Keterangan / Tips
        ttk.Label(opts, text="The game draws at 1080p. Window size sets windowed mode. FSR 1 sharpens if screen > 1080p.\n"
                             "Above 60 turns off internal frame limiter. VSync caps rate at monitor refresh.",
                  style="Muted.TLabel", wraplength=600).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        r += 1
        ttk.Label(right, text="In-game menu: Insert, or R3 + L2 on the pad (it changes these too).",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 0))
        r += 1

        ttk.Separator(right).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1
        ttk.Label(right, text="Controller & files", style="Head.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1
        # Row 1 of tools
        tools1 = ttk.Frame(right, style="Panel.TFrame")
        tools1.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 4))
        ttk.Button(tools1, text="⌨️ Keyboard & Mouse Controls", command=self.open_keymap_ui).pack(side="left", padx=(0, 8))
        ttk.Button(tools1, text="Map controller", command=lambda: self.map_pad([])).pack(side="left", padx=(0, 8))
        ttk.Button(tools1, text="Map sticks only", command=lambda: self.map_pad(["--sticks"])).pack(side="left", padx=(0, 8))
        ttk.Button(tools1, text="Reset mapping", command=self.reset_map).pack(side="left")
        r += 1

        # Row 2 of tools
        tools2 = ttk.Frame(right, style="Panel.TFrame")
        tools2.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Button(tools2, text="Open log", command=self.open_log).pack(side="left", padx=(0, 8))
        ttk.Button(tools2, text="Open audio log", command=self.open_audio_log).pack(side="left", padx=(0, 8))
        ttk.Button(tools2, text="Open saves", command=self.open_saves).pack(side="left", padx=(0, 8))
        ttk.Button(tools2, text="Manage extracted DLC…", command=self.open_dlc_manager).pack(side="left")
        r += 1
        r += 1
        self.pad_label = ttk.Label(right, text="", style="Muted.TLabel")
        self.pad_label.grid(row=r, column=0, columnspan=3, sticky="w")
        self._pad_text()

    # ---- helpers ----
    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.game_var.get() or str(ROOT), mustexist=True)
        if path:
            self.game_var.set(os.path.normpath(path))

    def open_dlc_manager(self):
        window = tk.Toplevel(self)
        window.title("Extracted Basara 4 DLC")
        window.geometry("920x480")
        window.minsize(720, 360)
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        frame = ttk.Frame(window, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="DLC folder", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        path_var = tk.StringVar(value=self.cfg.get("dlc_dir", str(DEFAULT_DLC_DIR)))
        path_entry = ttk.Entry(frame, textvariable=path_var, state="readonly")
        path_entry.grid(row=1, column=0, sticky="ew", pady=(8, 12))
        table_frame = ttk.Frame(frame)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("folder", "title", "content_id", "status")
        table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in (
                ("folder", "ENTITLEMENT", 180), ("title", "DLC", 220),
                ("content_id", "CONTENT ID", 300), ("status", "STATUS", 180)):
            table.heading(column, text=heading)
            table.column(column, width=width, minwidth=100, stretch=column in ("title", "content_id"))
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        summary = ttk.Label(frame, text="", style="Muted.TLabel")
        summary.grid(row=3, column=0, sticky="w", pady=(10, 0))

        def choose_folder():
            initial_dir = Path(path_var.get())
            if not initial_dir.is_dir():
                initial_dir = ROOT if ROOT.is_dir() else Path.home()
            try:
                selected = filedialog.askdirectory(
                    parent=self, title="Select the parent folder of extracted Basara 4 DLC",
                    initialdir=str(initial_dir), mustexist=True)
            except (OSError, tk.TclError) as error:
                messagebox.showerror("Cannot open folder picker", str(error), parent=self)
                return
            if not selected:
                return
            try:
                selected_path = Path(selected).resolve(strict=True)
                if (selected_path / "sce_sys" / "param.sfo").is_file():
                    selected_path = selected_path.parent
                if not selected_path.is_dir():
                    raise NotADirectoryError(selected_path)
                previous = self.cfg.get("dlc_dir")
                self.cfg["dlc_dir"] = str(selected_path)
                try:
                    save_config(self.cfg)
                except OSError:
                    if previous is None:
                        self.cfg.pop("dlc_dir", None)
                    else:
                        self.cfg["dlc_dir"] = previous
                    raise
            except (OSError, RuntimeError) as error:
                messagebox.showerror("Cannot select DLC folder", str(error), parent=window)
                return
            path_var.set(self.cfg["dlc_dir"])
            refresh()

        def refresh():
            table.delete(*table.get_children())
            try:
                records = scan_extracted_dlc(path_var.get())
            except OSError as error:
                messagebox.showerror("Cannot scan DLC folder", str(error), parent=window)
                return
            ready = sum(info is not None for _path, info, _error in records)
            invalid = len(records) - ready
            for path, info, error in records:
                if info:
                    table.insert("", "end", values=(
                        path.name, info["title"], info["content_id"], "READY"))
                else:
                    table.insert("", "end", values=(path.name, "", "", f"CHECK: {error}"))
            summary.configure(text=f"{ready} DLC ready for the next game launch"
                               + (f"  |  {invalid} need attention" if invalid else "")
                               if records else "No extracted DLC found in this folder.")

        def open_folder():
            folder = Path(path_var.get())
            try:
                folder.mkdir(parents=True, exist_ok=True)
                os.startfile(str(folder))
            except OSError as error:
                messagebox.showerror("Cannot open DLC folder", str(error), parent=window)

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Choose folder…", command=choose_folder).pack(side="left")
        ttk.Button(buttons, text="Open folder", command=open_folder).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Refresh", command=refresh).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        refresh()

    def _refresh_image_label(self):
        game = self.game_var.get().strip()
        if not game or not (Path(game) / "eboot.bin").is_file():
            text = "No eboot.bin in this folder."
        elif image_ready(game):
            text = "Game image ready."
        else:
            text = "The game image is made on the first start (about a minute); again if eboot.bin changes."
        self.image_label.config(text=text)

    def _ini(self):
        if OLD_INI.exists() and not INI.exists():
            os.replace(OLD_INI, INI)  # settings made before the runtime was renamed
        return INI

    def _load_ini_into_ui(self):
        ini = read_ini(self._ini())
        mode = ini.get("display_mode", self.cfg["display_mode"])
        if mode in {k for _, k in DISPLAY_MODES}:
            self.mode_var.set(mode)
        if "vsync" in ini:
            self.vsync_var.set(ini["vsync"] == "1")
        if "rcas" in ini:
            self.rcas_var.set(ini["rcas"] == "1")
        res = ini.get("render_resolution", self.cfg["resolution"])
        self.res_var.set(next((n for n, v in RESOLUTIONS if v == res), RESOLUTIONS[0][0]))
        fsr = ini.get("fsr1")
        up = ("fsr1" if fsr == "1" else "off") if fsr is not None else self.cfg["upscale"]
        self.up_var.set(next((n for n, v in UPSCALERS if v == up), UPSCALERS[0][0]))
        fps = int(self.cfg["fps_limit"])
        self.fps_var.set(next((n for n, v in FPS_LIMITS if v == fps), FPS_LIMITS[0][0]))
        an = int(ini["anisotropy"]) if ini.get("anisotropy", "").isdigit() else self.cfg["anisotropy"]
        self.aniso_var.set(next((n for n, v in ANISO if v == an), "Off"))
        saved_gpu = self.cfg.get("gpu_index", -1)
        self.gpu_var.set(next((name for idx, name, _ in SYSTEM_GPUS if idx == saved_gpu), SYSTEM_GPUS[0][1]))

    def _audio_toggled(self):
        if not self.audio_var.get():
            self.wav_var.set(False)  # the capture belongs to the log

    def _wav_toggled(self):
        if not self.wav_var.get():
            return
        if messagebox.askyesno("WAV capture", WAV_WARNING, icon="warning", default="no", parent=self):
            self.audio_var.set(True)
        else:
            self.wav_var.set(False)

    def _check_wav_leftovers(self):
        files, total = wav_captures()
        if not files or total < 50 * 2**20:
            return
        if messagebox.askyesno("Audio captures", f"user\\audio_wav holds {format_size(total)} of audio captures "
                               f"({len(files)} files) from an earlier session.\n\nDelete them to free the space?",
                               default="no", parent=self):
            for f in files:
                try:
                    f.unlink()
                except OSError:
                    pass

    def _pad_text(self):
        self.pad_label.config(text="Custom gamepad mapping in use." if MAPPING.exists()
                              else "No custom mapping: XInput and known pads work as they are.")

    def _collect(self):
        selected_gpu_name = self.gpu_var.get()
        selected_gpu_idx = next((idx for idx, name, _ in SYSTEM_GPUS if name == selected_gpu_name), -1)
        self.cfg.update(
            game=self.game_var.get().strip(), display_mode=self.mode_var.get(), vsync=self.vsync_var.get(),
            rcas=self.rcas_var.get(), fps_limit=dict(FPS_LIMITS)[self.fps_var.get()],
            resolution=dict(RESOLUTIONS)[self.res_var.get()], upscale=dict(UPSCALERS)[self.up_var.get()],
            anisotropy=dict(ANISO)[self.aniso_var.get()], audio_log=self.audio_var.get(),
            audio_wav=self.audio_var.get() and self.wav_var.get(),
            gpu_index=selected_gpu_idx)
        save_config(self.cfg)
        write_ini(self._ini(), {
            "display_mode": self.cfg["display_mode"], "vsync": int(self.cfg["vsync"]),
            "fps_limit": self.cfg["fps_limit"], "rcas": int(self.cfg["rcas"]),
            "anisotropy": self.cfg["anisotropy"], "render_resolution": self.cfg["resolution"],
            "fsr1": int(self.cfg["upscale"] == "fsr1"),
            "gpu_id": self.cfg.get("gpu_index", -1)})

    def _env(self):
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        env["SPARTAN_GPU_USER_DIR"] = str(USER)
        env["SPARTAN_CONFIG"] = str(self._ini())
        env["SPARTAN_WINDOW_TITLE"] = f"Sengoku Basara 4 Sumeragi - {BRAND} by {AUTHOR}"
        # Disable problematic AMD switchable graphics layer that causes instant close / black screen on Ryzen & Radeon
        env["DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1"] = "1"
        gpu_idx = self.cfg.get("gpu_index", -1)
        if gpu_idx != -1:
            env["SPARTAN_GPU_ID"] = str(gpu_idx)
        else:
            # Auto: If discrete GPU exists, prioritize it automatically
            disc_idx = next((idx for idx, _, is_disc in SYSTEM_GPUS if is_disc and idx >= 0), None)
            if disc_idx is not None:
                env["SPARTAN_GPU_ID"] = str(disc_idx)
            else:
                env.pop("SPARTAN_GPU_ID", None)
        avail_mb = check_commit_limit_mb()
        if avail_mb is not None and avail_mb < 3200:
            env["SPARTAN_DMEM_MB"] = "2048"
        elif avail_mb is not None and avail_mb < 4608:
            env["SPARTAN_DMEM_MB"] = "2560"
        if WINDOW_ICON.exists():
            env["SPARTAN_WINDOW_ICON"] = str(WINDOW_ICON)
        if MAPPING.exists():
            env["SDL_GAMECONTROLLERCONFIG_FILE"] = str(MAPPING)
        if self.cfg.get("audio_log"):  # one timestamped line per audio event, for bug reports
            env["SPARTAN_AUDIO_DEBUG"] = str(AUDIO_LOG)
        if self.cfg.get("audio_log") and self.cfg.get("audio_wav"):  # needs about 70 MB per minute
            env["SPARTAN_AUDIO_WAV_DIR"] = str(AUDIO_WAV)
        if self.cfg["fps_limit"] != 60:  # uncapped: 480 Hz vblank, the present thread paces to the limit
            env["SPARTAN_VBLANK_HZ"] = "0"
            env["SPARTAN_FPS_LIMIT"] = str(self.cfg["fps_limit"])

        # Keyboard & Mouse Control Mapping
        user_keymap = dict(DEFAULT_PC_KEYMAP)
        user_keymap.update(self.cfg.get("keymap", {}))
        # Format spec: button=name,button=name,...
        spec_parts = [f"{btn}={src}" for btn, src in user_keymap.items() if src]
        if spec_parts:
            env["SPARTAN_KEY_MAP"] = ",".join(spec_parts)

        if self.cfg.get("mouse_camera", True):
            env["SPARTAN_MOUSE_LOCK"] = "1"
            env["SPARTAN_MOUSE_AIM_STYLE"] = "0"  # 0: relative / free camera rotation
            sens = int(self.cfg.get("mouse_sens", 100))
            env["SPARTAN_MOUSE_AIM_SENSITIVITY"] = str(sens)

        return env

    # ---- starting the game ----
    def on_play(self):
        if self.busy or self.proc:
            return
        self.user_stopped = False
        self._collect()
        game = Path(self.cfg["game"]) if self.cfg["game"] else None
        exe = CORE
        if not game or not (game / "eboot.bin").is_file():
            self._deiconify()
            messagebox.showerror("Cannot start", "Choose the game folder (the one with eboot.bin).")
            return
        if not exe.is_file():
            self._deiconify()
            messagebox.showerror("Cannot start", f"Runtime not found: {exe}")
            return
        avail_mb = check_commit_limit_mb()
        if avail_mb is not None and avail_mb < 2560:
            if not messagebox.askyesno(
                "Virtual Memory Sangat Rendah",
                f"Sisa alokasi memori virtual Windows Anda hanya ~{avail_mb} MB.\n"
                "Game membutuhkan setidaknya ~3584 MB (3.5 GB) virtual memory.\n\n"
                "Jika game crash dengan Error 1455, pastikan Virtual Memory (Paging File) aktif di Windows.\n\n"
                "Tetap jalankan game?"):
                return
        self.busy = True
        self.play.config(state="disabled")
        self.pending = None
        dlc_dir = self.cfg.get("dlc_dir") or str(DEFAULT_DLC_DIR)
        threading.Thread(target=self._prepare_then_launch, args=(game, exe, dlc_dir), daemon=True).start()
        self.after(200, self._wait_prepare)

    def _progress(self, text):
        self.pending_text = text

    def _prepare_then_launch(self, game, exe, dlc_dir):
        self.pending_text = "Starting…"
        error = None
        if not image_ready(game):
            error = prepare_image(game, self._progress)
        if not error:
            self._progress("Preparing the DLC profile…")
            error = prepare_content_profile(game, dlc_dir)
        patch_args, notice = [], ""
        if not error and self.cfg["fps_limit"] != 60:
            try:
                writes, _ = basara4_patches.build(OUT / "eboot.elf", OUT / "patches.bin", True)
            except OSError:
                writes = []
            if writes:
                patch_args = ["--patches", str(OUT / "patches.bin")]
            else:
                self.cfg["fps_limit"] = 60
                notice = "Frame limiter not found in this eboot.bin: staying at 60.  "
        self.pending = (error, game, exe, patch_args, notice, dlc_dir)

    def _wait_prepare(self):
        if self.pending is None:
            self.status.config(text=getattr(self, "pending_text", "Starting…"))
            self.after(200, self._wait_prepare)
            return
        error, game, exe, patch_args, notice, dlc_dir = self.pending
        self.busy = False
        if error:
            self.play.config(state="normal")
            self._deiconify()
            self.status.config(text=error)
            messagebox.showerror("Cannot prepare the game", error)
            return
        if self.cfg["fps_limit"] == 60:
            self.fps_var.set(FPS_LIMITS[0][0])
        USER.mkdir(parents=True, exist_ok=True)
        self._make_window_icon(game)
        if self.cfg.get("audio_log") and AUDIO_LOG.exists():  # keep the previous run's log
            try:
                os.replace(AUDIO_LOG, AUDIO_LOG.with_name("audio_debug.previous.log"))
            except OSError:
                pass
        if self.cfg.get("audio_log") and self.cfg.get("audio_wav"):  # only this run's captures stay
            AUDIO_WAV.mkdir(parents=True, exist_ok=True)
            for old in AUDIO_WAV.glob("*.wav"):
                try:
                    old.unlink()
                except OSError:
                    pass
        extra_args = []
        isa_table = OUT / "isa-rewrite.bin"
        if isa_table.is_file():
            extra_args = ["--isa-rewrite", str(isa_table)]

        log = open(LOG, "wb")
        self.proc = subprocess.Popen(
            [str(exe), str(OUT / "boot-linked.bin"), "--content-profile", str(OUT / "content.bin"),
             "--app0", str(game), "--user", str(USER), "--dlc-dir", str(dlc_dir),
             "--no-game-hooks"] + extra_args + patch_args,
            env=self._env(), cwd=str(USER), stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        if self.cfg.get("audio_wav"):  # a one-session option: the next start has it off again
            self.cfg["audio_wav"] = False
            save_config(self.cfg)
            self.wav_var.set(False)
        self.stop.config(state="normal")
        self.status.config(text=notice + ("Running, writing user\\audio_debug.log…" if self.cfg.get("audio_log")
                                          else "Running…  (Insert opens the in-game menu)"))
        self._refresh_image_label()

    def _make_window_icon(self, game):
        if Image is None or WINDOW_ICON.exists():
            return
        try:
            Image.open(Path(game) / "sce_sys" / "icon0.png").convert("RGBA").resize((128, 128)).save(
                WINDOW_ICON, format="BMP")
        except OSError:
            pass

    def show_credits(self):
        notice = ROOT / "NOTICE.md"
        try:
            text = notice.read_text(encoding="utf-8")
        except OSError:
            text = "Credits are in NOTICE.md."
        win = tk.Toplevel(self)
        win.title(f"{BRAND} - Credits & Community")
        win.configure(bg=BG)
        win.geometry("740x520")
        win.minsize(640, 420)
        win.transient(self)

        top_bar = ttk.Frame(win, style="Panel.TFrame", padding=10)
        top_bar.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(top_bar, text="Community & Official Channels:", style="Head.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(top_bar, text="💬 Discord",
                   command=lambda: webbrowser.open("https://discord.gg/UBZc57bBMa")).pack(side="left", padx=4)
        ttk.Button(top_bar, text="🎬 TikTok (@razzconnect)",
                   command=lambda: webbrowser.open("https://www.tiktok.com/@razzconnect")).pack(side="left", padx=4)
        ttk.Button(top_bar, text="☕ Support Dev",
                   command=lambda: webbrowser.open("https://sociabuzz.com/razzgame/tribe")).pack(side="left", padx=4)

        box_frame = ttk.Frame(win, style="Panel.TFrame")
        box_frame.pack(fill="both", expand=True, padx=10, pady=10)
        box = tk.Text(box_frame, bg=PANEL, fg=FG, wrap="word", relief="flat", padx=14, pady=12, font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(box_frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scrollbar.set)
        box.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        box.insert("1.0", text)
        box.config(state="disabled")

    # ---- auto update ----
    def _check_update_thread(self):
        info = check_latest_release()
        if info:
            self.update_info = info
            self.after(0, self._show_update_available)

    def _show_update_available(self):
        if not self.update_info or getattr(self, "_update_btn_shown", False):
            return
        self._update_btn_shown = True
        tag = self.update_info["tag"]
        self.version_label.config(text=f"v{VERSION}  (Update: {tag})", fg=GOLD)
        self.update_btn = tk.Button(
            self.mark_frame,
            text=f"Update ke {tag}",
            bg=GOLD,
            fg=BG,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            activebackground="#dbb458",
            activeforeground=BG,
            cursor="hand2",
            command=self.prompt_update,
        )
        self.update_btn.pack(pady=(4, 0))

    def check_update_manual(self):
        self.status.config(text="Memeriksa pembaruan...")

        def run():
            info = check_latest_release()
            if info:
                self.update_info = info
                self.after(0, lambda: (
                    self.status.config(text="Ready"),
                    self._show_update_available(),
                    self.prompt_update()
                ))
            else:
                self.after(0, lambda: (
                    self.status.config(text="Ready"),
                    messagebox.showinfo("Update", f"Anda sudah menggunakan versi terbaru (v{VERSION}).")
                ))

        threading.Thread(target=run, daemon=True).start()

    def prompt_update(self):
        if not self.update_info:
            return
        tag = self.update_info["tag"]
        size_mb = self.update_info["size"] / (1024 * 1024)
        if messagebox.askyesno(
            "Update Tersedia",
            f"Versi baru {tag} tersedia di GitHub!\nUkuran unduhan: ~{size_mb:.1f} MB.\n\n"
            "Launcher akan mengunduh dan menerapkan update secara otomatis.\n"
            "Data game (folder game\\) dan save data Anda (folder user\\) TIDAK akan terhapus.\n\n"
            "Unduh dan pasang update sekarang?"
        ):
            self._download_and_apply_update(self.update_info)

    def _download_and_apply_update(self, info):
        win = tk.Toplevel(self)
        win.title("Basara 4 PC - Updater")
        win.configure(bg=BG)
        win.geometry("440x180")
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text=f"Mengunduh Pembaruan {info['tag']}...", bg=BG, fg=GOLD, font=("Segoe UI", 11, "bold")).pack(pady=(18, 8))
        status_lbl = tk.Label(win, text="Menghubungkan ke GitHub...", bg=BG, fg=FG, font=("Segoe UI", 9))
        status_lbl.pack(pady=(0, 8))

        prog = ttk.Progressbar(win, orient="horizontal", length=360, mode="determinate")
        prog.pack(pady=6)

        def worker():
            updates_dir = USER / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            zip_path = updates_dir / "update.zip"

            try:
                req = urllib.request.Request(info["url"], headers={"User-Agent": "Basara4-PC-Launcher"})
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                    total = int(resp.headers.get("Content-Length", info["size"] or 1))
                    downloaded = 0
                    chunk_size = 64 * 1024
                    with open(zip_path, "wb") as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            percent = int((downloaded / total) * 100) if total else 0
                            cur_mb = downloaded / (1024 * 1024)
                            tot_mb = total / (1024 * 1024)
                            win.after(0, lambda p=percent, c=cur_mb, t=tot_mb: (
                                prog.configure(value=p),
                                status_lbl.configure(text=f"Mengunduh: {c:.1f} MB / {t:.1f} MB ({p}%)")
                            ))

                win.after(0, lambda: self._apply_update_and_restart(win))
            except Exception as e:
                win.after(0, lambda err=e: (
                    win.destroy(),
                    messagebox.showerror("Gagal Mengunduh", f"Pembaruan gagal diunduh:\n{err}")
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_and_restart(self, win):
        win.destroy()
        updater_cmd = USER / "updater.cmd"
        cmd_content = (
            "@echo off\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "title Basara 4 PC - Updater\r\n"
            "cd /d \"%~dp0\"\r\n"
            "echo Menunggu launcher menutup...\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            "set \"ZIP=%~dp0updates\\update.zip\"\r\n"
            "set \"EXTRACT=%~dp0updates\\extract\"\r\n"
            "set \"DEST=%~dp0..\"\r\n"
            "if not exist \"!ZIP!\" exit /b 1\r\n"
            "echo Mengekstrak pembaruan...\r\n"
            "if exist \"!EXTRACT!\" rmdir /s /q \"!EXTRACT!\"\r\n"
            "mkdir \"!EXTRACT!\"\r\n"
            "tar -xf \"!ZIP!\" -C \"!EXTRACT!\" 2>nul\r\n"
            "if errorlevel 1 (\r\n"
            "    powershell.exe -NoProfile -Command \"Expand-Archive -Path '!ZIP!' -DestinationPath '!EXTRACT!' -Force\"\r\n"
            ")\r\n"
            "set \"SRC=!EXTRACT!\"\r\n"
            "if not exist \"!EXTRACT!\\Sengoku Basara 4.exe\" (\r\n"
            "    for /d %%D in (\"!EXTRACT!\\*\") do (\r\n"
            "        if exist \"%%~fD\\Sengoku Basara 4.exe\" set \"SRC=%%~fD\"\r\n"
            "    )\r\n"
            ")\r\n"
            "echo Menerapkan file baru...\r\n"
            "if exist \"!SRC!\\_internal\" xcopy /e /y /i \"!SRC!\\_internal\" \"!DEST!\\_internal\" >nul\r\n"
            "if exist \"!SRC!\\bin\" xcopy /e /y /i \"!SRC!\\bin\" \"!DEST!\\bin\" >nul\r\n"
            "if exist \"!SRC!\\scripts\" xcopy /e /y /i \"!SRC!\\scripts\" \"!DEST!\\scripts\" >nul\r\n"
            "if exist \"!SRC!\\launcher\" xcopy /e /y /i \"!SRC!\\launcher\" \"!DEST!\\launcher\" >nul\r\n"
            "if exist \"!SRC!\\Sengoku Basara 4.exe\" copy /y \"!SRC!\\Sengoku Basara 4.exe\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\Play Sengoku Basara 4.cmd\" copy /y \"!SRC!\\Play Sengoku Basara 4.cmd\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\NOTICE.md\" copy /y \"!SRC!\\NOTICE.md\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\LICENSE\" copy /y \"!SRC!\\LICENSE\" \"!DEST!\\\" >nul\r\n"
            "rmdir /s /q \"!EXTRACT!\" 2>nul\r\n"
            "del /f /q \"!ZIP!\" 2>nul\r\n"
            "echo Memulai versi baru...\r\n"
            "start \"\" \"!DEST!\\Sengoku Basara 4.exe\"\r\n"
            "del /f /q \"%~f0\" 2>nul\r\n"
            "exit /b 0\r\n"
        )
        updater_cmd.write_text(cmd_content, encoding="ascii")
        subprocess.Popen(["cmd.exe", "/c", str(updater_cmd)], creationflags=NO_WINDOW)
        self.destroy()

    def _deiconify(self):
        self.deiconify()

    def on_stop(self):
        if self.proc and self.proc.poll() is None:
            self.user_stopped = True
            self.proc.kill()

    def _show_crash_dialog(self, code):
        data = extract_crash_summary(LOG)
        win = tk.Toplevel(self)
        win.title("Basara 4 PC — Crash Diagnostic")
        win.configure(bg=BG)
        win.geometry("740x560")
        win.minsize(680, 480)
        win.grab_set()

        # Bottom Button Bar dipack DULUAN dengan side="bottom" agar TIDAK TERDORONG KELUAR LAYAR
        btn_bar = ttk.Frame(win, style="TFrame", padding=12)
        btn_bar.pack(side="bottom", fill="x")

        # Top Header Frame
        header_frame = ttk.Frame(win, style="Panel.TFrame", padding=14)
        header_frame.pack(side="top", fill="x", padx=12, pady=(12, 6))

        exit_hex = f"{code & 0xFFFFFFFF:#x}" if isinstance(code, int) else "Unknown"
        tk.Label(
            header_frame,
            text=f"⚠️ Game Berhenti Mendadak (Exit Code {exit_hex})",
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")

        tk.Label(
            header_frame,
            text="Terdeteksi indikasi error dari log permainan di bawah ini:",
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(4, 6))

        for err in data["detected_errors"]:
            tk.Label(
                header_frame,
                text=f"• {err}",
                bg=PANEL,
                fg=GOLD,
                font=("Consolas", 9, "bold"),
                wraplength=680,
                justify="left"
            ).pack(anchor="w", padx=6)

        # Center Body Frame
        body_frame = ttk.Frame(win, style="TFrame")
        body_frame.pack(side="top", fill="both", expand=True, padx=12, pady=6)

        ttk.Label(body_frame, text="Cuplikan 10 Baris Log Terakhir (Raw Context):", style="Panel.TLabel").pack(anchor="w", pady=(0, 4))
        txt = tk.Text(body_frame, bg="#0d0b0a", fg="#d6cfc7", relief="flat", wrap="none", font=("Consolas", 9), padx=8, pady=8)
        txt.insert("1.0", data["tail_context"])
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

        def copy_details():
            try:
                import platform
                import ctypes
                from ctypes import wintypes
                k = ctypes.windll.kernel32
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64),
                    ]
                mem = MEMORYSTATUSEX()
                mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                k.GlobalMemoryStatusEx(ctypes.byref(mem))
                ram_total = f"{mem.ullTotalPhys // (1024*1024)} MB"
                ram_avail = f"{mem.ullAvailPhys // (1024*1024)} MB"
                commit_avail = f"{mem.ullAvailPageFile // (1024*1024)} MB"
            except Exception:
                ram_total = ram_avail = commit_avail = "N/A"

            gpu_list = "\n".join(f"  - {name}" for _, name, _ in SYSTEM_GPUS) if SYSTEM_GPUS else "  - None detected"

            audio_tail = ""
            if AUDIO_LOG.is_file():
                try:
                    a_lines = AUDIO_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                    if a_lines:
                        audio_tail = "\n".join(a_lines[-6:])
                except Exception:
                    pass

            clip_text = (
                f"=== Sengoku Basara 4 PC — Crash Diagnostic Report ===\n"
                f"Version       : v{VERSION}\n"
                f"Exit Code     : {exit_hex}\n"
                f"OS            : {platform.system()} {platform.release()} (Build {platform.version()})\n"
                f"Architecture  : {platform.machine()} ({platform.processor()})\n"
                f"RAM           : Total {ram_total} | Free {ram_avail} | Available Commit {commit_avail}\n"
                f"Vulkan GPUs   :\n{gpu_list}\n"
                f"Detected Fatal Signatures:\n" + "\n".join(f"  * {e}" for e in data["detected_errors"]) + "\n\n"
                f"--- [Last 10 Log Lines (last_run.log)] ---\n"
                f"{data['tail_context']}\n"
            )
            if audio_tail:
                clip_text += f"\n--- [Recent Audio Log (audio_debug.log)] ---\n{audio_tail}\n"

            clip_text += "\n=== End of Report ==="
            self.clipboard_clear()
            self.clipboard_append(clip_text)
            messagebox.showinfo(
                "Tersalin ke Clipboard",
                "Laporan crash lengkap (spek hardware, GPU, log error, dan audio log) berhasil disalin!\n"
                "Anda tinggal Paste (Ctrl+V) langsung ke Discord untuk bantuan teknis.",
                parent=win
            )

        def open_discord():
            import webbrowser
            webbrowser.open("https://discord.gg/UBZc57bBMa")

        ttk.Button(btn_bar, text="📋 Copy Error ke Clipboard", command=copy_details).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="📁 Buka File Log", command=self.open_log).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="🌐 Lapor ke Discord", command=open_discord).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="Tutup", command=win.destroy).pack(side="right", padx=4)

    # ---- keyboard & mouse mapping dialog ----
    def open_keymap_ui(self):
        win = tk.Toplevel(self)
        win.title("Keyboard & Mouse Controls — Custom Keymap")
        win.configure(bg=BG)
        win.geometry("740x620")
        win.minsize(680, 520)
        win.grab_set()

        cur_map = dict(DEFAULT_PC_KEYMAP)
        cur_map.update(self.cfg.get("keymap", {}))
        mouse_cam_var = tk.BooleanVar(value=self.cfg.get("mouse_camera", True))
        mouse_sens_var = tk.IntVar(value=self.cfg.get("mouse_sens", 100))

        # Top Header
        top_frame = ttk.Frame(win, style="Panel.TFrame", padding=12)
        top_frame.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(top_frame, text="⚙️ Pengaturan Kontrol Keyboard & Mouse", bg=PANEL, fg=GOLD, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(top_frame, text="Klik tombol untuk mengubah tombol pemetaan. Tekan tombol Keyboard atau Mouse untuk mengikat.", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        # Mouse Settings Bar
        mouse_bar = ttk.Frame(top_frame, style="Panel.TFrame")
        mouse_bar.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(mouse_bar, text="Aktifkan Kamera Mouse (Mouse Look)", variable=mouse_cam_var).pack(side="left")

        sens_frame = ttk.Frame(mouse_bar, style="Panel.TFrame")
        sens_frame.pack(side="right")
        ttk.Label(sens_frame, text="Sensitivitas: ", style="Panel.TLabel").pack(side="left")
        sens_lbl = ttk.Label(sens_frame, text=f"{mouse_sens_var.get()}%", style="Panel.TLabel", width=5)
        sens_lbl.pack(side="right")
        def on_sens_scroll(val):
            sens_lbl.config(text=f"{int(float(val))}%")
        scale = ttk.Scale(sens_frame, from_=20, to=300, variable=mouse_sens_var, command=on_sens_scroll, length=120)
        scale.pack(side="left", padx=6)

        # Body - Scrollable Mapping Table
        body_container = ttk.Frame(win, style="TFrame")
        body_container.pack(fill="both", expand=True, padx=14, pady=6)

        canvas = tk.Canvas(body_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body_container, orient="vertical", command=canvas.yview)
        scroll_content = ttk.Frame(canvas, style="TFrame")

        scroll_content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_content, anchor="nw")
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_widgets = {}
        active_listening = [None]  # holds current button listening

        def format_binding_display(key_str):
            if not key_str:
                return "None"
            return key_str.upper() if len(key_str) <= 3 else key_str

        def finish_listen(action_name, new_key):
            if active_listening[0] == action_name:
                active_listening[0] = None
                cur_map[action_name] = new_key
                btn = btn_widgets.get(action_name)
                if btn:
                    btn.config(text=format_binding_display(new_key), bg="#2c2824", fg=GOLD)
                win.unbind("<Key>")
                win.unbind("<Button-1>")
                win.unbind("<Button-2>")
                win.unbind("<Button-3>")

        def start_listen(action_name):
            # If another was listening, reset it
            if active_listening[0]:
                old_action = active_listening[0]
                old_btn = btn_widgets.get(old_action)
                if old_btn:
                    old_btn.config(text=format_binding_display(cur_map.get(old_action, "")), bg="#2c2824", fg=GOLD)
            active_listening[0] = action_name
            btn = btn_widgets.get(action_name)
            if btn:
                btn.config(text="[ TEKAN TOMBOL... ]", bg=RED, fg="#fff")

            def on_key_event(event):
                # Translate tkinter keysym to SDL scancode name
                keysym = event.keysym
                # Special mappings
                key_map_table = {
                    "Return": "Return", "Escape": "Escape", "space": "Space",
                    "BackSpace": "Backspace", "Tab": "Tab", "Shift_L": "Left Shift",
                    "Shift_R": "Right Shift", "Control_L": "Left Ctrl", "Control_R": "Right Ctrl",
                    "Alt_L": "Left Alt", "Alt_R": "Right Alt", "Caps_Lock": "CapsLock",
                    "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
                    "comma": "Comma", "equal": "Equals", "period": "Period",
                    "slash": "Slash", "semicolon": "Semicolon", "minus": "Minus"
                }
                mapped_name = key_map_table.get(keysym, keysym.capitalize() if len(keysym) > 1 else keysym.upper())
                finish_listen(action_name, mapped_name)
                return "break"

            def on_mouse_event(btn_num):
                name_map = {1: "Mouse Left", 2: "Mouse Middle", 3: "Mouse Right"}
                finish_listen(action_name, name_map.get(btn_num, f"Mouse {btn_num}"))
                return "break"

            win.bind("<Key>", on_key_event)
            # Give focus to window to capture keys
            win.focus_set()
            # Allow clicking mouse buttons to bind
            win.bind("<Button-1>", lambda e: on_mouse_event(1))
            win.bind("<Button-2>", lambda e: on_mouse_event(2))
            win.bind("<Button-3>", lambda e: on_mouse_event(3))

        # Render Table Items (Styled like game interaction list)
        for row_idx, (action_key, action_label, ps_button_label) in enumerate(ACTION_DESCRIPTIONS):
            item_frame = tk.Frame(scroll_content, bg="#181512", padx=12, pady=7, highlightbackground="#25211d", highlightthickness=1)
            item_frame.pack(fill="x", pady=2, padx=4)

            # Left Label: Action name
            lbl_text = f"{action_label}"
            tk.Label(item_frame, text=lbl_text, bg="#181512", fg=FG, font=("Segoe UI Semibold", 9), anchor="w").pack(side="left", padx=(4, 0))

            # Sub-label for PS button
            tk.Label(item_frame, text=f"({ps_button_label})", bg="#181512", fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(side="left", padx=8)

            # Right: Key Box Button
            val = cur_map.get(action_key, "")
            key_btn = tk.Button(
                item_frame,
                text=format_binding_display(val),
                bg="#2c2824",
                fg=GOLD,
                activebackground=RED,
                activeforeground="#fff",
                font=("Consolas", 10, "bold"),
                width=16,
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                cursor="hand2",
                command=lambda a=action_key: start_listen(a)
            )
            key_btn.pack(side="right")
            btn_widgets[action_key] = key_btn

        # Movement Fixed Notice
        notice_frame = tk.Frame(scroll_content, bg=PANEL, padx=12, pady=6)
        notice_frame.pack(fill="x", pady=(8, 4), padx=4)
        tk.Label(
            notice_frame,
            text="ℹ️ Pergerakan Karakter otomatis terikat pada W, A, S, D (Standar PC). Kamera mouse mengontrol analog kanan.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "italic")
        ).pack(anchor="w")

        # Bottom Action Bar
        bottom_bar = ttk.Frame(win, style="TFrame", padding=12)
        bottom_bar.pack(fill="x")

        def reset_defaults():
            cur_map.clear()
            cur_map.update(DEFAULT_PC_KEYMAP)
            mouse_cam_var.set(True)
            mouse_sens_var.set(100)
            sens_lbl.config(text="100%")
            for k, b in btn_widgets.items():
                b.config(text=format_binding_display(cur_map.get(k, "")), bg="#2c2824", fg=GOLD)
            messagebox.showinfo("Reset Preset", "Pengaturan kontrol berhasil dikembalikan ke Preset Default PC!", parent=win)

        def save_and_close():
            self.cfg["keymap"] = cur_map
            self.cfg["mouse_camera"] = mouse_cam_var.get()
            self.cfg["mouse_sens"] = int(mouse_sens_var.get())
            save_config(self.cfg)
            win.destroy()
            messagebox.showinfo("Tersimpan", "Pengaturan kontrol Keyboard & Mouse berhasil disimpan!", parent=self)

        ttk.Button(bottom_bar, text="🔄 Reset ke Default PC", command=reset_defaults).pack(side="left", padx=4)
        ttk.Button(bottom_bar, text="Batal", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(bottom_bar, text="💾 Simpan Pengaturan", style="Play.TButton", command=save_and_close).pack(side="right", padx=4)

    def _check_exit_error(self, code):
        if code in (0, None) or not LOG.is_file():
            return None
        self._show_crash_dialog(code)
        return f"Game crash (exit code {code & 0xFFFFFFFF:#x}). Silakan cek jendela diagnostik."

    def _poll(self):
        if self.proc and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.play.config(state="normal")
            self.stop.config(state="disabled")
            if code not in (0, None) and not self.user_stopped:
                self._deiconify()
                self._show_crash_dialog(code)
            if self.play_now:
                if code in (0, None) or self.user_stopped:
                    self.destroy()
                return
            err_msg = f"Game exited with code {code & 0xFFFFFFFF:#x}. See the log." if (code not in (0, None) and not self.user_stopped) else None
            self.status.config(text=err_msg if err_msg else "Game closed.")
            self._load_ini_into_ui()
        self.after(500, self._poll)

    # ---- controller and files ----
    def map_pad(self, extra):
        exe = BIN / "basara4-pad-mapper.exe"
        if not exe.is_file():
            messagebox.showerror("Mapper missing", str(exe))
            return
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        USER.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([str(exe), str(MAPPING)] + extra, env=env, creationflags=0x00000010)  # new console
        self.status.config(text="Follow the prompts in the mapper window, then press PLAY.")
        self.after(4000, self._pad_text)

    def reset_map(self):
        if MAPPING.exists() and messagebox.askyesno("Reset mapping", "Delete the saved controller mapping?"):
            os.replace(MAPPING, str(MAPPING) + ".bak")
            self._pad_text()

    def open_log(self):
        if LOG.exists():
            os.startfile(LOG)
        else:
            messagebox.showinfo("Log", "No log yet. Start the game first.")

    def open_audio_log(self):
        if AUDIO_LOG.exists():
            os.startfile(AUDIO_LOG)
        else:
            messagebox.showinfo("Audio log", "No audio log yet. Tick \"Audio debug log\", then start the game.")

    def open_saves(self):
        path = USER / "savedata"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Game running", "Stop the game and close the launcher?"):
                return
            self.proc.kill()
        try:
            self._collect()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    Launcher(play_now="--play" in sys.argv).mainloop()
