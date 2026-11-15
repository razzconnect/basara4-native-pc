/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Basara 4 PC: linked into every *-test target (gpu/CMakeLists.txt). A failing assert, abort() or
 * crash in a test must end the process with a message on stderr and a failing exit code that CTest
 * reports, never a Windows dialog that waits on the desktop (build.sh --test runs unattended). */
#ifdef _WIN32
#include <stdlib.h>
#include <windows.h>

__attribute__((constructor)) static void spartanport_test_no_dialog(void) {
    /* Crashes (access violation, ...) and missing-drive prompts: no error box, exit code only. */
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    /* assert(): message to stderr instead of a message box. */
    _set_error_mode(_OUT_TO_STDERR);
    /* abort() (called by a failed assert): no "abort() has been called" box, no Windows Error
     * Reporting; the exit code stays 3. */
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
}
#else
/* Elsewhere a failed assert already prints and aborts without a dialog. */
typedef int spartanport_test_no_dialog_unused;
#endif
