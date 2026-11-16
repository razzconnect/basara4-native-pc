"""The game's environment from Launcher._env (replaces the upstream test_launcher_upscale.py).

Upscaler and internal render resolution, the GPU choice, memory, input, frame limit and the render
trace (F11 only with the render debug trace, decision of 2026-11-14).
"""
import tempfile
import unittest
from unittest import mock

from launcher_fixtures import bl, launcher_folders, launcher_like

# Render sizes are whole 20x20 motion blur tiles: 480p renders at 860x480 (854 blurred the right edge).
SUB_1080P = {"360p": "640x360", "480p": "860x480", "540p": "960x540", "720p": "1280x720", "900p": "1600x900"}


def env_of(cfg=None, commit_mb=None, gpus=None, environ=None, **attributes):
    """_env with this process's SPARTAN_* variables replaced by `environ` (restored afterwards)."""
    with mock.patch.object(bl, "check_commit_limit_mb", return_value=commit_mb), \
            mock.patch.object(bl, "SYSTEM_GPUS", gpus or [(-1, "Auto", True)]), \
            mock.patch.dict(bl.os.environ):
        for name in [n for n in bl.os.environ if n.startswith("SPARTAN_")]:
            del bl.os.environ[name]
        bl.os.environ.update(environ or {})
        return bl.Launcher._env(launcher_like(cfg, **attributes))


class UpscaleTests(unittest.TestCase):
    def test_internal_render_resolution_only_below_1080p(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            for name, _label in [(v, n) for n, v in bl.RESOLUTIONS]:
                env = env_of({"resolution": name}, environ={"SPARTAN_RENDER_RES": "stale"})
                if name in SUB_1080P:
                    self.assertEqual(env["SPARTAN_RENDER_RES"], SUB_1080P[name], name)
                else:
                    self.assertNotIn("SPARTAN_RENDER_RES", env, name)

    def test_resolution_labels_show_the_render_size(self):
        # The launcher shows the size the game renders at, the one the eboot patch writes.
        sizes = {**SUB_1080P, "native": "1920x1080", "1440p": "2560x1440", "1800p": "3200x1800",
                 "4K": "3840x2160"}
        self.assertEqual(sorted(v for _n, v in bl.RESOLUTIONS), sorted(sizes))
        for label, name in bl.RESOLUTIONS:
            self.assertTrue(label.startswith(sizes[name].replace("x", " x ") + "  ("), (label, name))
        self.assertIn(("860 x 480  (480p - Super Kentang)", "480p"), bl.RESOLUTIONS)

    def test_the_game_runs_without_the_temporal_upscalers(self):
        # FSR 1 and RCAS reach the runtime through basara4.ini (fsr1=, rcas=); the environment
        # turns the FSR 3/4 and object-motion paths off.
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            for upscale in ("off", "fsr1"):
                env = env_of({"upscale": upscale}, environ={"SPARTAN_UPSCALER": "fsr3"})
                self.assertEqual((env["SPARTAN_UPSCALER"], env["SPARTAN_OBJECT_MOTION"]), ("off", "0"))
                self.assertNotIn("SPARTAN_FSR1", env)
        self.assertEqual(sorted(dict(bl.UPSCALERS).values()), ["fsr1", "off"])  # the launcher's choices


class RenderTraceTests(unittest.TestCase):
    def test_f11_capture_only_with_the_render_trace(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            stale = {"SPARTAN_RENDER_TRACE": "x", "SPARTAN_FRAME_CAPTURE_KEY": "1"}
            env = env_of({"render_trace": False}, environ=stale)
            self.assertNotIn("SPARTAN_RENDER_TRACE", env)
            self.assertNotIn("SPARTAN_FRAME_CAPTURE_KEY", env)
            env = env_of({"render_trace": True})
            self.assertEqual(env["SPARTAN_RENDER_TRACE"], str(paths.render_trace_log))
            self.assertEqual(env["SPARTAN_FRAME_CAPTURE_KEY"], "1")
            session = paths.render_traces / "20261115-101500"
            env = env_of({"render_trace": True}, trace_session=session)
            self.assertEqual(env["SPARTAN_RENDER_TRACE"], str(session / "render_trace.log"))


class DeviceAndRuntimeTests(unittest.TestCase):
    def test_runtime_paths_and_window(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            env = env_of(environ={"PATH": "C:\\system"})
            self.assertTrue(env["PATH"].startswith(str(paths.bin) + bl.os.pathsep))
            self.assertEqual(env["SPARTAN_GPU_USER_DIR"], str(paths.user))
            self.assertEqual(env["SPARTAN_CONFIG"], str(paths.ini))
            self.assertEqual(env["DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1"], "1")
            self.assertNotIn("SPARTAN_WINDOW_ICON", env)
            self.assertNotIn("SDL_GAMECONTROLLERCONFIG_FILE", env)
            paths.user.mkdir()
            paths.window_icon.write_bytes(b"BM")
            paths.mapping.write_text("mapping", encoding="utf-8")
            env = env_of()
            self.assertEqual(env["SPARTAN_WINDOW_ICON"], str(paths.window_icon))
            self.assertEqual(env["SDL_GAMECONTROLLERCONFIG_FILE"], str(paths.mapping))

    def test_gpu_choice(self):
        gpus = [(-1, "Auto", True), (0, "GPU 0: iGPU", False), (1, "GPU 1: dGPU", True)]
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            self.assertEqual(env_of({"gpu_index": 0}, gpus=gpus)["SPARTAN_GPU_ID"], "0")
            self.assertEqual(env_of({"gpu_index": -1}, gpus=gpus)["SPARTAN_GPU_ID"], "1")  # discrete first
            only_integrated = [(-1, "Auto", True), (0, "GPU 0: iGPU", False)]
            env = env_of({"gpu_index": -1}, gpus=only_integrated, environ={"SPARTAN_GPU_ID": "7"})
            self.assertNotIn("SPARTAN_GPU_ID", env)

    def test_guest_memory_follows_resolution_and_commit_limit(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            for res in ("1440p", "1800p", "4K"):
                self.assertEqual(env_of({"resolution": res}, commit_mb=1000)["SPARTAN_DMEM_MB"], "4800", res)
            self.assertEqual(env_of({"resolution": "native"}, commit_mb=3199)["SPARTAN_DMEM_MB"], "2048")
            self.assertEqual(env_of({"resolution": "720p"}, commit_mb=4607)["SPARTAN_DMEM_MB"], "2560")
            self.assertNotIn("SPARTAN_DMEM_MB", env_of({"resolution": "native"}, commit_mb=4608,
                                                        environ={"SPARTAN_DMEM_MB": "1"}))
            self.assertNotIn("SPARTAN_DMEM_MB", env_of({"resolution": "native"}, commit_mb=None))

    def test_frame_limit(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            env = env_of({"fps_limit": 60}, environ={"SPARTAN_VBLANK_HZ": "60"})
            self.assertNotIn("SPARTAN_FPS_LIMIT", env)
            for limit in (90, 144, 0):
                env = env_of({"fps_limit": limit})
                self.assertEqual((env["SPARTAN_VBLANK_HZ"], env["SPARTAN_FPS_LIMIT"]), ("0", str(limit)))

    def test_audio_debug_and_wav_capture(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths:
            env = env_of({"audio_log": False, "audio_wav": True})
            self.assertNotIn("SPARTAN_AUDIO_DEBUG", env)
            self.assertNotIn("SPARTAN_AUDIO_WAV_DIR", env)
            env = env_of({"audio_log": True, "audio_wav": True})
            self.assertEqual(env["SPARTAN_AUDIO_DEBUG"], str(paths.audio_log))
            self.assertEqual(env["SPARTAN_AUDIO_WAV_DIR"], str(paths.audio_wav))

    def test_keyboard_mouse_and_input_mode(self):
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp):
            env = env_of({"keymap": {"cross": "J", "square": ""}, "mouse_camera": True, "mouse_sens": 150,
                          "input_mode": "kbm"})
            pairs = dict(part.split("=", 1) for part in env["SPARTAN_KEY_MAP"].split(","))
            expected = dict(bl.DEFAULT_PC_KEYMAP, cross="J")
            del expected["square"]  # an empty binding is left out
            self.assertEqual(pairs, expected)
            self.assertEqual((env["SPARTAN_MOUSE_LOCK"], env["SPARTAN_MOUSE_AIM_STYLE"],
                              env["SPARTAN_MOUSE_AIM_SENSITIVITY"], env["SPARTAN_INPUT_MODE"]),
                             ("1", "0", "150", "kbm"))
            env = env_of({"mouse_camera": False})
            self.assertNotIn("SPARTAN_MOUSE_LOCK", env)  # mouse look off: no lock, no aim settings
            self.assertNotIn("SPARTAN_MOUSE_AIM_SENSITIVITY", env)
            self.assertEqual(env["SPARTAN_INPUT_MODE"], "controller")


if __name__ == "__main__":
    unittest.main()
