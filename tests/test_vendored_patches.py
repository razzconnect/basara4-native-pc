"""Vendored third-party code: the libraries under gpu/third_party/ and
third_party/ are committed in this repository with this port's changes in them, and every change is
recorded as a patch that build.sh checks (VENDORED_PATCHES). This test reads that list from build.sh,
so build.sh and the test cannot drift apart."""
import re
import subprocess
import unittest

from paths import ROOT


def vendored_patches():
    """[(patch folder, library folder)] from build.sh's VENDORED_PATCHES."""
    text = (ROOT / 'build.sh').read_text(encoding='utf-8')
    block = re.search(r'^VENDORED_PATCHES=\(\s*(.*?)\s*\)', text, re.M | re.S)
    assert block, 'VENDORED_PATCHES not found in build.sh'
    return [tuple(entry.split(':', 1)) for entry in block.group(1).split()]


def reverse_check(library, patch):
    """git apply --reverse --check from the repository root, as build.sh's check_vendored does."""
    return subprocess.run(['git', 'apply', '--reverse', '--check', '-v', f'--directory={library}', str(patch)],
                          cwd=ROOT, capture_output=True, text=True)


class VendoredPatchesTest(unittest.TestCase):
    def test_every_recorded_patch_is_in_the_vendored_tree(self):
        pairs = vendored_patches()
        self.assertIn(('gpu/patches/fsr-vulkan', 'gpu/third_party/fsr-vulkan'), pairs)
        self.assertIn(('third_party/patches/LibAtrac9', 'third_party/LibAtrac9'), pairs)
        for patches, library in pairs:
            files = sorted((ROOT / patches).glob('*.patch'))
            self.assertTrue(files, patches)
            self.assertTrue((ROOT / library).is_dir(), library)
            for patch in files:
                result = reverse_check(library, patch)
                self.assertEqual(result.returncode, 0, f'{patch.name}: {result.stderr}')
                # A real check: every file of the patch is looked at inside the library, none skipped.
                self.assertNotIn('Skipped patch', result.stderr, patch.name)
                self.assertIn(f'Checking patch {library}/', result.stderr, patch.name)

    def test_every_patch_folder_is_checked_by_build_sh(self):
        listed = {patches for patches, _library in vendored_patches()}
        found = {str(path.parent.relative_to(ROOT)).replace('\\', '/')
                 for base in ('gpu/patches', 'third_party/patches') for path in (ROOT / base).rglob('*.patch')}
        self.assertEqual(found, listed)

    def test_patched_libraries_check_out_with_lf(self):
        # git apply compares bytes: a library checked out with CRLF (Git for Windows with core.eol
        # native, or LibAtrac9's own text=auto) would fail the check although the patch is there.
        rules = (ROOT / '.gitattributes').read_text(encoding='utf-8').splitlines()
        for _patches, library in vendored_patches():
            self.assertIn(f'{library}/** text=auto eol=lf', rules, library)

    def test_no_submodules(self):
        # The libraries are plain folders in the tree; a .gitmodules or a gitfile in one of them makes
        # git treat the folder as a separate (missing) repository and build.sh stop.
        self.assertFalse((ROOT / '.gitmodules').exists())
        for _patches, library in vendored_patches():
            self.assertFalse((ROOT / library / '.git').exists(), library)


if __name__ == '__main__':
    unittest.main()
