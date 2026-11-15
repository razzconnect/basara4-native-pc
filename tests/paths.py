"""Repository paths for the Python tests.

Run them with `bash build.sh --test` (it builds the programs some tests run, then runs every test),
or directly once those programs are built:
  Linux:   python3 -m unittest discover -s tests
  Windows: py -3 -m unittest discover -s tests   (the programs need MSYS2 CLANG64's DLLs: built_env)
"""
import os
import re
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


def built_env(program=None):
    """The environment to run a program from built() in. On Windows it links MSYS2 CLANG64's DLLs
    (SDL3, FFmpeg, libc++), which build.sh puts on PATH through msys2-env.sh: their folder goes first
    on PATH here too, so a direct `py -3 -m unittest` run finds them. A packaged program with its own
    SDL3.dll next to it is left as it is. MSYS2_ROOT selects another MSYS2 (as in msys2-env.sh)."""
    env = dict(os.environ)
    if sys.platform != 'win32' or (program is not None and (Path(program).parent / 'SDL3.dll').is_file()):
        return env
    root = env.get('MSYS2_ROOT', r'C:\msys64')
    if re.match(r'^/[A-Za-z]/', root):  # an MSYS path such as /c/msys64
        root = f'{root[1]}:{root[2:]}'
    clang64 = Path(root) / 'clang64' / 'bin'
    if not (clang64 / 'SDL3.dll').is_file():
        raise RuntimeError(f'{clang64} has no SDL3.dll: the programs from bash build.sh --test need MSYS2 '
                           'CLANG64 (packaging/windows/README.md); set MSYS2_ROOT to the MSYS2 that built them')
    env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
    return env
