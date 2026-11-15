from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))
from basara4_dlc import scan_extracted_dlc


LABEL = "DLCXBGMXXXXXX000"
VALID = {
    "TITLE_ID": "CUSA01159",
    "CATEGORY": "ac",
    "CONTENT_ID": f"JP0102-CUSA01159_00-{LABEL}",
    "TITLE": "Test DLC",
}


def scan_with(test, folders):
    """scan_extracted_dlc over synthetic add-on folders: {folder name: param.sfo values}."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in folders:
            (root / name / "sce_sys").mkdir(parents=True)
            (root / name / "sce_sys" / "param.sfo").write_bytes(name.encode())

        def sfo_values(data):
            test.assertIn(data.decode(), folders)
            return folders[data.decode()]

        with patch("basara4_dlc._parse_sfo", side_effect=sfo_values):
            return [(path.name, metadata, error) for path, metadata, error in scan_extracted_dlc(root)]


class ExtractedDlcTests(unittest.TestCase):
    def test_folder_name_may_differ_from_the_entitlement_label(self):
        # a76b1b9 (v1.0.6 "Flexible DLC Scanner"): the metadata identifies the add-on, whatever the
        # folder is called.
        records = scan_with(self, {LABEL: VALID, "wrong-folder": VALID})
        self.assertEqual([name for name, _metadata, _error in records], [LABEL, "wrong-folder"])
        for name, metadata, error in records:
            self.assertEqual(error, "", name)
            self.assertEqual(metadata, {"title": "Test DLC", "content_id": VALID["CONTENT_ID"],
                                        "title_id": "CUSA01159", "label": LABEL}, name)

    def test_folders_for_other_titles_or_content_are_rejected(self):
        bad_label = "DLC-BGM" + "X" * 9  # 16 characters, one that is not a letter, digit or _
        records = dict((name, (metadata, error)) for name, metadata, error in scan_with(self, {
            "other-title": dict(VALID, TITLE_ID="CUSA00001"),
            "not-addon": dict(VALID, CATEGORY="gd"),
            "short-id": dict(VALID, CONTENT_ID="JP0102-CUSA01159_00"),
            "bad-label": dict(VALID, CONTENT_ID=f"JP0102-CUSA01159_00-{bad_label}"),
            "long-label": dict(VALID, CONTENT_ID=f"JP0102-CUSA01159_00-{LABEL}X"),
        }))
        self.assertEqual(records["other-title"], (None, "folder is for CUSA00001, not CUSA01159"))
        self.assertEqual(records["not-addon"], (None, "folder metadata does not identify a Basara 4 add-on"))
        self.assertEqual(records["short-id"], (None, "folder metadata does not identify a Basara 4 add-on"))
        self.assertEqual(records["bad-label"], (None, "invalid DLC entitlement label in CONTENT_ID"))
        self.assertEqual(records["long-label"], (None, "invalid DLC entitlement label in CONTENT_ID"))

    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            records = scan_extracted_dlc(missing)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1], None)
        self.assertEqual(records[0][2], "DLC folder does not exist")


if __name__ == "__main__":
    unittest.main()
