// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC: the minidump written when the process dies (Windows).
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/* Writes user/crash/core-YYYYMMDD-HHMMSS.dmp once (later calls return); SPARTAN_GPU_USER_DIR is
 * the launcher's user folder. exception_pointers is the EXCEPTION_POINTERS* of the fault, or
 * NULL (assertions, stack protector, SPARTAN_TEST_MINIDUMP). */
void spartanport_write_minidump(void *exception_pointers);

#ifdef __cplusplus
}
#endif
