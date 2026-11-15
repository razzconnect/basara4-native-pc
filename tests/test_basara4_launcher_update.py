"""Package update in launcher/basara4_launcher.py (replaces the upstream test_launcher_update.py).

The GitHub API and the download are mocked: no network. The updater script is written, not run.
"""
import io
import json
import tempfile
import types
import unittest
from unittest import mock

from launcher_fixtures import bl, launcher_folders


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def release(tag, assets):
    return json.dumps({"tag_name": tag, "body": "notes", "assets": assets}).encode()


class VersionTests(unittest.TestCase):
    def test_parse_version_compares_numerically(self):
        self.assertEqual(bl.parse_version("v1.0.10"), (1, 0, 10))
        self.assertGreater(bl.parse_version("v1.0.10"), bl.parse_version("1.0.9"))
        self.assertEqual(bl.parse_version("Basara4-PC-v1.1.0"), (4, 1, 1, 0))
        self.assertEqual(bl.parse_version("latest"), (0,))
        self.assertEqual(bl.parse_version(None), (0,))


class LatestReleaseTests(unittest.TestCase):
    def check(self, payload):
        with mock.patch.object(bl.urllib.request, "urlopen", return_value=FakeResponse(payload)) as urlopen:
            info = bl.check_latest_release()
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, f"https://api.github.com/repos/{bl.GITHUB_REPO}/releases/latest")
        self.assertEqual(request.get_header("User-agent"), "Basara4-PC-Launcher")
        return info

    def newer_tag(self):
        major, minor, patch = (bl.parse_version(bl.VERSION) + (0, 0, 0))[:3]
        return f"v{major}.{minor}.{patch + 1}"

    def test_newer_release_returns_the_windows_package(self):
        tag = self.newer_tag()
        info = self.check(release(tag, [
            {"name": "source.tar.gz", "browser_download_url": "https://x/src", "size": 1},
            {"name": "basara4-windows-v9.zip", "browser_download_url": "https://x/win.zip", "size": 123},
        ]))
        self.assertEqual(info, {"tag": tag, "url": "https://x/win.zip", "name": "basara4-windows-v9.zip",
                                "size": 123, "body": "notes"})

    def test_newer_release_without_a_named_package_falls_back_to_a_zip(self):
        tag = self.newer_tag()
        info = self.check(release(tag, [
            {"name": "notes.txt", "browser_download_url": "https://x/notes", "size": 1},
            {"name": "other.zip", "browser_download_url": "https://x/other.zip", "size": 7},
        ]))
        self.assertEqual((info["tag"], info["url"], info["size"]), (tag, "https://x/other.zip", 7))

    def test_same_or_older_release_or_no_zip_is_no_update(self):
        zip_asset = [{"name": "basara4-windows.zip", "browser_download_url": "https://x/w.zip", "size": 1}]
        self.assertIsNone(self.check(release(f"v{bl.VERSION}", zip_asset)))
        self.assertIsNone(self.check(release("v1.0.0", zip_asset)))
        self.assertIsNone(self.check(release(self.newer_tag(), [{"name": "readme.md"}])))

    def test_network_or_format_errors_are_no_update(self):
        with mock.patch.object(bl.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(bl.check_latest_release())
        self.assertIsNone(self.check(b"not json"))


class DownloadAndApplyTests(unittest.TestCase):
    def test_download_writes_the_package_then_applies_it(self):
        payload = b"PK\x03\x04 synthetic package"
        widgets = mock.MagicMock()
        window = mock.MagicMock()
        window.after.side_effect = lambda _ms, callback: callback()
        launcher = types.SimpleNamespace(_apply_update_and_restart=mock.MagicMock())
        info = {"tag": "v9.9.9", "url": "https://x/win.zip", "name": "win.zip", "size": len(payload)}

        def run_now(target, daemon):
            return types.SimpleNamespace(start=target)

        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl.tk, "Toplevel", return_value=window), \
                mock.patch.object(bl.tk, "Label", widgets.Label), \
                mock.patch.object(bl.ttk, "Progressbar", widgets.Progressbar), \
                mock.patch.object(bl.threading, "Thread", side_effect=run_now), \
                mock.patch.object(bl.urllib.request, "urlopen",
                                  return_value=FakeResponse(payload, {"Content-Length": str(len(payload))})):
            bl.Launcher._download_and_apply_update(launcher, info)
            self.assertEqual((paths.user / "updates" / "update.zip").read_bytes(), payload)
        launcher._apply_update_and_restart.assert_called_once_with(window)

    def test_failed_download_is_reported_and_nothing_is_applied(self):
        window = mock.MagicMock()
        window.after.side_effect = lambda _ms, callback: callback()
        launcher = types.SimpleNamespace(_apply_update_and_restart=mock.MagicMock())
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp), \
                mock.patch.object(bl.tk, "Toplevel", return_value=window), \
                mock.patch.object(bl.tk, "Label"), mock.patch.object(bl.ttk, "Progressbar"), \
                mock.patch.object(bl.threading, "Thread",
                                  side_effect=lambda target, daemon: types.SimpleNamespace(start=target)), \
                mock.patch.object(bl.urllib.request, "urlopen", side_effect=OSError("offline")), \
                mock.patch.object(bl.messagebox, "showerror") as showerror:
            bl.Launcher._download_and_apply_update(launcher, {"tag": "v9", "url": "https://x/a.zip", "size": 1})
        launcher._apply_update_and_restart.assert_not_called()
        window.destroy.assert_called_once()
        self.assertIn("offline", showerror.call_args[0][1])

    def test_updater_replaces_program_files_only_and_restarts(self):
        window = mock.MagicMock()
        launcher = types.SimpleNamespace(destroy=mock.MagicMock())
        with tempfile.TemporaryDirectory() as tmp, launcher_folders(tmp) as paths, \
                mock.patch.object(bl.subprocess, "Popen") as popen:
            (paths.user / "updates").mkdir(parents=True)  # the download made it
            bl.Launcher._apply_update_and_restart(launcher, window)
            script = (paths.user / "updater.cmd").read_text(encoding="ascii")
        window.destroy.assert_called_once()
        launcher.destroy.assert_called_once()
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], ["cmd.exe", "/c", str(paths.user / "updater.cmd")])
        copied = [line for line in script.splitlines() if "xcopy" in line or "copy /y" in line]
        for folder in ("_internal", "bin", "scripts", "launcher"):
            self.assertTrue(any(f'"!SRC!\\{folder}"' in line for line in copied), folder)
        # The game dump and the player's saves and settings are never copied over or deleted.
        for kept in ("game", "user"):
            self.assertFalse(any(f"\\{kept}\"" in line or f"\\{kept}\\" in line for line in copied), kept)
        self.assertNotIn("rmdir /s /q \"!DEST!", script)
        self.assertIn('start "" "!DEST!\\Sengoku Basara 4.exe"', script)


if __name__ == "__main__":
    unittest.main()
