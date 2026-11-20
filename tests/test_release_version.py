"""The version of a release build: the launcher says 1.0.9 (no beta), the update check treats the tag "v1.0.9" as the
same version, an older install is offered it and a newer one is not, and the version the bug-report guide uses as an
example is the launcher's. parse_version itself is not changed here: it reads EVERY number of a tag, so the GitHub
tag of the release has to be exactly "v1.0.9"."""
import re
import tempfile
import unittest
from unittest import mock

from launcher_fixtures import bl
from paths import ROOT
from test_basara4_launcher_window import launcher

RELEASE = "1.0.9"  # change this together with the tag of the release


def release_json(tag):
    import json
    return json.dumps({"tag_name": tag, "body": "notes", "assets": [
        {"name": "basara4-windows-test.zip", "browser_download_url": "https://x/win.zip", "size": 123}]}).encode()


class VersionTests(unittest.TestCase):
    def test_the_launcher_is_the_release_not_a_beta(self):
        self.assertEqual(bl.VERSION, RELEASE)
        self.assertNotRegex(bl.VERSION, r"(?i)beta|rc|alpha|dev")
        self.assertEqual(bl.parse_version(bl.VERSION), (1, 0, 9))

    def test_the_window_and_the_report_show_the_release_number(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.assertEqual(app.version_label.cget("text"), f"v{RELEASE}")

    def test_the_guide_example_is_the_launchers_version(self):
        guide = (ROOT / "docs" / "BUG-SCAN-GUIDE.md").read_text(encoding="utf-8")
        self.assertIn(f"`v{RELEASE}`", guide)

    def check(self, tag):
        class Response:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        with mock.patch.object(bl.urllib.request, "urlopen", return_value=Response(release_json(tag))):
            return bl.check_latest_release()

    def test_the_tag_v1_0_9_is_the_same_version_as_the_launcher(self):
        self.assertEqual(bl.parse_version("v1.0.9"), bl.parse_version(bl.VERSION))
        self.assertIsNone(self.check("v1.0.9"), "an install of 1.0.9 must not be offered 1.0.9 again")

    def test_a_newer_tag_is_offered_and_an_older_one_is_not(self):
        self.assertEqual(self.check("v1.0.10")["tag"], "v1.0.10")
        self.assertEqual(self.check("v1.1.0")["tag"], "v1.1.0")
        self.assertIsNone(self.check("v1.0.8"))

    def test_a_tester_of_the_beta_is_not_offered_the_release(self):
        # The beta and the release have the same numbers, so the update check sees no difference: they update by hand.
        self.assertEqual(bl.parse_version("1.0.9-beta"), bl.parse_version("v1.0.9"))

    def test_an_install_of_an_older_version_is_offered_the_release_tag(self):
        with mock.patch.object(bl, "VERSION", "1.0.8"):
            self.assertEqual(self.check("v1.0.9")["tag"], "v1.0.9")
        with mock.patch.object(bl, "VERSION", "1.0.9-beta"):
            self.assertIsNone(self.check("v1.0.9"))


if __name__ == "__main__":
    unittest.main()
