"""Repository paths for the Python tests (run: python3 -m unittest discover -s tests)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))


def built(name, windows=None):
    """A program `bash build.sh --test` builds: out/<name> on Linux; on Windows the CMake test target
    out/gpu/<name>.exe, or `windows` (relative to the repository) for a program build.sh copies to out/."""
    if sys.platform == 'win32':
        return ROOT / (windows or f'out/gpu/{name}.exe')
    return ROOT / 'out' / name
