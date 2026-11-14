// SPDX-License-Identifier: GPL-2.0-or-later
/* spartanport_write_minidump (src/crash_dump_win.c): a child process faults, its unhandled
 * exception filter writes the dump like probe.c's fatal handler, and the parent reads the dump
 * back with dbghelp. Checks that the dump is not empty, is a minidump, and carries the fault
 * (exception stream: code, thread). The RTX 4070 crash of 2026-11-14 left a 0-byte dump.
 * Windows only; no game or GPU required. */
#include <windows.h>
#include <dbghelp.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "crash_dump_win.h"

#define CHILD_EXIT 3

static DWORD crash_thread;

static LONG WINAPI child_filter(EXCEPTION_POINTERS *info) {
    spartanport_write_minidump(info);
    TerminateProcess(GetCurrentProcess(), CHILD_EXIT);
    return EXCEPTION_CONTINUE_SEARCH;
}

static int child(void) {
    SetUnhandledExceptionFilter(child_filter);
    crash_thread = GetCurrentThreadId();
    printf("child thread %lu\n", crash_thread);
    fflush(stdout);
    volatile int *null_pointer = NULL;
    return *null_pointer;
}

static void find_dump(const char *dir, char *path, size_t size) {
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof(pattern), "%s\\crash\\core-*.dmp", dir);
    WIN32_FIND_DATAA found;
    HANDLE search = FindFirstFileA(pattern, &found);
    assert(search != INVALID_HANDLE_VALUE && "no dump file was created");
    snprintf(path, size, "%s\\crash\\%s", dir, found.cFileName);
    assert(!FindNextFileA(search, &found) && "exactly one dump expected");
    FindClose(search);
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "child")) {
        return child();
    }

    char temp[MAX_PATH], dir[MAX_PATH];
    GetTempPathA(sizeof(temp), temp);
    snprintf(dir, sizeof(dir), "%sbasara4_crash_dump_test_%lu", temp, GetCurrentProcessId());
    assert(CreateDirectoryA(dir, NULL));
    SetEnvironmentVariableA("SPARTAN_GPU_USER_DIR", dir);

    char exe[MAX_PATH], command[MAX_PATH * 2];
    GetModuleFileNameA(NULL, exe, sizeof(exe));
    snprintf(command, sizeof(command), "\"%s\" child", exe);
    STARTUPINFOA startup = {.cb = sizeof(startup)};
    PROCESS_INFORMATION process;
    assert(CreateProcessA(NULL, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process));
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 0;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    assert(exit_code == CHILD_EXIT);

    char path[MAX_PATH];
    find_dump(dir, path, sizeof(path));
    HANDLE file = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    assert(file != INVALID_HANDLE_VALUE);
    LARGE_INTEGER size;
    assert(GetFileSizeEx(file, &size));
    printf("dump %s: %lld bytes\n", path, (long long)size.QuadPart);
    assert(size.QuadPart > 0 && "empty dump (the 2026-11-14 symptom)");

    HANDLE mapping = CreateFileMappingA(file, NULL, PAGE_READONLY, 0, 0, NULL);
    assert(mapping);
    void *view = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
    assert(view);
    assert(!memcmp(view, "MDMP", 4));

    MINIDUMP_DIRECTORY *directory = NULL;
    MINIDUMP_EXCEPTION_STREAM *exception = NULL;
    ULONG stream_size = 0;
    assert(MiniDumpReadDumpStream(view, ExceptionStream, &directory, (void **)&exception,
                                  &stream_size) &&
           "the dump has no exception stream");
    printf("exception 0x%08x in thread %u\n", (unsigned)exception->ExceptionRecord.ExceptionCode,
           (unsigned)exception->ThreadId);
    assert(exception->ExceptionRecord.ExceptionCode == EXCEPTION_ACCESS_VIOLATION);
    assert(exception->ThreadId != 0);

    MINIDUMP_THREAD_LIST *threads = NULL;
    assert(MiniDumpReadDumpStream(view, ThreadListStream, &directory, (void **)&threads,
                                  &stream_size));
    int faulting_thread_listed = 0;
    for (ULONG i = 0; i < threads->NumberOfThreads; ++i) {
        faulting_thread_listed |= threads->Threads[i].ThreadId == exception->ThreadId;
    }
    assert(faulting_thread_listed);

    UnmapViewOfFile(view);
    CloseHandle(mapping);
    CloseHandle(file);
    DeleteFileA(path);
    char crash_dir[MAX_PATH];
    snprintf(crash_dir, sizeof(crash_dir), "%s\\crash", dir);
    RemoveDirectoryA(crash_dir);
    RemoveDirectoryA(dir);
    puts("crash-dump-test ok");
    return 0;
}
