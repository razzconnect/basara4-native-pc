"""Every way the game process ends without running atexit handlers (_exit, _Exit, quick_exit,
TerminateProcess on itself) writes the session end first: the present summary in last_run.log and,
while tracing, session.summary. The session end is runtime_report() (whose loader hook calls
spartanportgpu_session_end), spartanportgpu_session_end() or RenderTrace::WriteSessionSummary().
Ends through exit() are covered by the std::atexit hook that spartanportgpu_init registers.

The listed exceptions end the process where no session summary belongs: a crash (the crash
handler writes its own report and a minidump) or a stop before the game runs."""
import re
import unittest

from paths import ROOT

SOURCES = sorted(ROOT.glob('src/*.c')) + sorted((ROOT / 'gpu/shim').rglob('*.cpp'))
TERMINATION = re.compile(r'\b(?:std::)?(?:_exit|_Exit|quick_exit)\s*\(|'
                         r'\bTerminateProcess\s*\(\s*GetCurrentProcess\s*\(\s*\)')
SESSION_END = re.compile(r'\b(?:runtime_report|spartanportgpu_session_end|WriteSessionSummary)\s*\(')
FUNCTION_START = re.compile(r'^[A-Za-z_].*\)\s*\{\s*$')
NOT_A_NAME = {'__attribute__', 'noreturn', 'if', 'for', 'while', 'switch', 'sizeof'}

# (file, function) -> why that end writes no session summary.
EXCEPTIONS = {
    ('src/probe.c', 'fatal_exception'): 'crash (Windows): logs the fault and writes a minidump',
    ('src/probe.c', 'fault'): 'crash (Linux): signal handler, only async-signal-safe writes',
    ('src/probe.c', 'watchdog'): 'hang (Linux): signal handler, only async-signal-safe writes',
    ('src/probe.c', 'main'): 'stops before the game runs (--check-imports, CPU lacks needed ISA)',
    ('gpu/shim/common/assert.cpp', 'assert_fail_impl'): 'crash: a failed assertion',
}
STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def function_name(signature):
    names = [n for n in re.findall(r'(\w+)\s*\(', signature) if n not in NOT_A_NAME]
    return names[-1] if names else '?'


def terminations():
    """[(relative file, function, line number, function text up to and including the call)]"""
    found = []
    for path in SOURCES:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        start, name = 0, '?'
        for number, line in enumerate(lines, 1):
            if FUNCTION_START.match(line) and not line.startswith(('}', '#', '/', '*')):
                start, name = number - 1, function_name(line)
            code = STRING.sub('""', line).split('//')[0]
            if TERMINATION.search(code):
                rel = path.relative_to(ROOT).as_posix()
                found.append((rel, name, number, '\n'.join(lines[start:number])))
    return found


class SessionEndPathsTest(unittest.TestCase):
    def test_every_process_end_writes_the_session_end_first(self):
        found = terminations()
        self.assertTrue(found, 'no process end found: the scan is broken')
        missing = [f'{rel}:{number} in {name}()' for rel, name, number, body in found
                   if (rel, name) not in EXCEPTIONS and not SESSION_END.search(body)]
        self.assertEqual(missing, [], 'process ends without the session end (present summary)')

    def test_known_ends_are_seen(self):
        names = {(rel, name) for rel, name, _number, _body in found_once()}
        # The ends a player reaches: window closed / Stop game, Exit to Desktop, restart after a
        # resolution change, the guest's own _exit.
        for end in [('gpu/shim/spartanportgpu.cpp', 'spartanportgpu_init'),
                    ('src/probe.c', 'menu_quit'), ('src/probe.c', 'runtime_restart'),
                    ('src/runtime_kernel.c', 'hard_exit')]:
            self.assertIn(end, names)

    def test_exceptions_still_exist(self):
        names = {(rel, name) for rel, name, _number, _body in found_once()}
        self.assertEqual(sorted(set(EXCEPTIONS) - names), [], 'stale exception: remove it')


_found = None


def found_once():
    global _found
    if _found is None:
        _found = terminations()
    return _found


if __name__ == '__main__':
    unittest.main()
