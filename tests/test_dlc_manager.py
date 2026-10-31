from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / "launcher"))
from basara4_dlc import scan_extracted_dlc


class ExtractedDlcTests(unittest.TestCase):
    def test_lists_valid_and_invalid_extracted_addons(self):
        label = "DLCXBGMXXXXXX000"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / label
            invalid = root / "wrong-folder"
            for folder in (valid, invalid):
                (folder / "sce_sys").mkdir(parents=True)
                (folder / "sce_sys" / "param.sfo").write_bytes(b"fixture")

            def sfo_values(data):
                self.assertEqual(data, b"fixture")
                if data:
                    return {
                        "TITLE_ID": "CUSA01159",
                        "CATEGORY": "ac",
                        "CONTENT_ID": f"JP0102-CUSA01159_00-{label}",
                        "TITLE": "Test DLC",
                    }

            with patch("basara4_dlc._parse_sfo", side_effect=sfo_values):
                records = scan_extracted_dlc(root)

            self.assertEqual(len(records), 2)
            self.assertEqual(records[0][0].name, label)
            self.assertEqual(records[0][1]["title"], "Test DLC")
            self.assertEqual(records[0][2], "")
            self.assertEqual(records[1][0].name, "wrong-folder")
            self.assertIsNone(records[1][1])
            self.assertIn("folder name must match", records[1][2])

    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            records = scan_extracted_dlc(missing)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1], None)
        self.assertEqual(records[0][2], "DLC folder does not exist")


if __name__ == "__main__":
    unittest.main()
