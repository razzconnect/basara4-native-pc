// SPDX-License-Identifier: GPL-2.0-or-later
/* Basara 4 PC: a minidump (threads, stacks, modules) next to the logs when the process dies, so a
 * crash report is not just the text of the fault handler. Moved out of probe.c so it can be tested
 * on its own (tests/test_crash_dump.c). */
#include <windows.h>
#include <dbghelp.h>
#include <stdio.h>
#include <stdlib.h>

#include "crash_dump_win.h"

typedef BOOL (WINAPI *minidump_write_fn)(HANDLE, DWORD, HANDLE, MINIDUMP_TYPE,
                                         PMINIDUMP_EXCEPTION_INFORMATION,
                                         PMINIDUMP_USER_STREAM_INFORMATION,
                                         PMINIDUMP_CALLBACK_INFORMATION);

void spartanport_write_minidump(void *exception_pointers) {
    static volatile LONG once;
    if (InterlockedExchange(&once,1)) return;
    HMODULE dbghelp=LoadLibraryA("dbghelp.dll");
    minidump_write_fn write=dbghelp ? (minidump_write_fn)(void *)GetProcAddress(dbghelp,"MiniDumpWriteDump") : NULL;
    if (!write) {
        fprintf(stderr,"Crash dump failed: %s (%lu)\n",
                dbghelp ? "MiniDumpWriteDump unavailable" : "cannot load dbghelp.dll",
                GetLastError());
        if (dbghelp) FreeLibrary(dbghelp);
        return;
    }
    const char *user=getenv("SPARTAN_GPU_USER_DIR");
    char dir[MAX_PATH],path[MAX_PATH];
    snprintf(dir,sizeof(dir),"%s\\crash",user && *user ? user : "user");
    if (!CreateDirectoryA(dir,NULL) && GetLastError()!=ERROR_ALREADY_EXISTS) {
        fprintf(stderr,"Crash dump failed: cannot create %s (%lu)\n",dir,GetLastError());
        FreeLibrary(dbghelp);
        return;
    }
    SYSTEMTIME now;
    GetLocalTime(&now);
    snprintf(path,sizeof(path),"%s\\core-%04d%02d%02d-%02d%02d%02d.dmp",dir,now.wYear,now.wMonth,now.wDay,now.wHour,now.wMinute,now.wSecond);
    HANDLE file=CreateFileA(path,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if (file==INVALID_HANDLE_VALUE) {
        fprintf(stderr,"Crash dump failed: cannot create %s (%lu)\n",path,GetLastError());
        FreeLibrary(dbghelp);
        return;
    }
    /* dbghelp.h declares this structure with 4-byte packing (ExceptionPointers at offset 4 on
     * x64). The unpacked copy that was here put it at offset 8, so dbghelp read a garbage pointer
     * and failed with ERROR_NOACCESS after creating the file: every crash dump with exception
     * information was 0 bytes. */
    MINIDUMP_EXCEPTION_INFORMATION ex={
        .ThreadId=GetCurrentThreadId(),
        .ExceptionPointers=(EXCEPTION_POINTERS *)exception_pointers,
        .ClientPointers=FALSE,
    };
    /* thread info, process/thread data, indirectly referenced memory, unloaded modules */
    const MINIDUMP_TYPE type=(MINIDUMP_TYPE)(MiniDumpWithThreadInfo|MiniDumpWithProcessThreadData|
                                             MiniDumpWithIndirectlyReferencedMemory|MiniDumpWithUnloadedModules);
    BOOL ok=write(GetCurrentProcess(),GetCurrentProcessId(),file,type,exception_pointers ? &ex : NULL,NULL,NULL);
    const DWORD error=ok ? 0 : GetLastError();
    CloseHandle(file);
    if (ok) {
        fprintf(stderr,"Crash dump written: %s\n",path);
    } else {
        fprintf(stderr,"Crash dump failed: %s (0x%08lx)\n",path,error);
    }
    fflush(stderr);
    FreeLibrary(dbghelp);
}
