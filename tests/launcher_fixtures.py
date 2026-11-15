"""Shared fixtures for the launcher tests (launcher/basara4_launcher.py).

No game files: every eboot, image and log here is synthetic, written into a temporary folder.
"""
import contextlib
import sys
import types
from pathlib import Path
from unittest import mock

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))
import basara4_launcher as bl  # noqa: E402


@contextlib.contextmanager
def launcher_folders(folder):
    """Point the launcher's bin\\, out\\ and user\\ files into `folder` (a temporary directory)."""
    folder = Path(folder)
    user, out, bin_dir = folder / "user", folder / "out", folder / "bin"
    names = {
        "BIN": bin_dir, "OUT": out, "USER": user, "CONFIG": user / "launcher.json",
        "INI": user / "basara4.ini", "OLD_INI": user / "spartanport.ini",
        "WINDOW_ICON": user / "window_icon.bmp", "CORE": bin_dir / "basara4-core.exe",
        "LOG": user / "last_run.log", "PREPARE_LOG": user / "prepare.log",
        "MAPPING": user / "gamepad_mapping.txt", "AUDIO_LOG": user / "audio_debug.log",
        "AUDIO_WAV": user / "audio_wav", "RENDER_TRACE_LOG": user / "render_trace.log",
        "RENDER_TRACES": user / "render_traces", "CAPTURES_DIR": user / "captures",
        "STAMP": out / "prepared.json",
    }
    with contextlib.ExitStack() as stack:
        for name, value in names.items():
            stack.enter_context(mock.patch.object(bl, name, value))
        yield types.SimpleNamespace(root=folder, **{k.lower(): v for k, v in names.items()})


def launcher_like(cfg=None, **attributes):
    """An object the Launcher's methods run on without a window: its cfg (DEFAULTS + cfg) and
    _ini(). Call methods unbound, e.g. bl.Launcher._env(launcher_like(...))."""
    config = dict(bl.DEFAULTS)
    config.update(cfg or {})
    fake = types.SimpleNamespace(**dict({"cfg": config, "trace_session": None}, **attributes))
    fake._ini = lambda: bl.Launcher._ini(fake)
    return fake


def game_folder(folder, eboot=b"synthetic eboot"):
    """A game folder with a synthetic eboot.bin (not a game file)."""
    game = Path(folder) / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "eboot.bin").write_bytes(eboot)
    return game
