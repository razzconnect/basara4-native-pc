// SPDX-License-Identifier: GPL-2.0-or-later
// Test helper: runs a function with the process's stdout (the stream the game's last_run.log is
// written from) redirected to a file, restores stdout and returns what was written.
#pragma once

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#ifdef _WIN32
#include <io.h>
#define SPARTAN_TEST_DUP _dup
#define SPARTAN_TEST_DUP2 _dup2
#define SPARTAN_TEST_CLOSE _close
#define SPARTAN_TEST_FILENO _fileno
#else
#include <unistd.h>
#define SPARTAN_TEST_DUP dup
#define SPARTAN_TEST_DUP2 dup2
#define SPARTAN_TEST_CLOSE close
#define SPARTAN_TEST_FILENO fileno
#endif

template <typename Fn>
std::string CaptureStdout(const std::filesystem::path& file, Fn&& fn) {
    std::fflush(stdout);
    const int saved = SPARTAN_TEST_DUP(SPARTAN_TEST_FILENO(stdout));
    std::FILE* sink = std::fopen(file.string().c_str(), "wb");
    if (saved < 0 || !sink) {
        std::fprintf(stderr, "CaptureStdout: cannot redirect stdout to %s\n",
                     file.string().c_str());
        std::abort();
    }
    SPARTAN_TEST_DUP2(SPARTAN_TEST_FILENO(sink), SPARTAN_TEST_FILENO(stdout));
    fn();
    std::fflush(stdout);
    SPARTAN_TEST_DUP2(saved, SPARTAN_TEST_FILENO(stdout));
    SPARTAN_TEST_CLOSE(saved);
    std::fclose(sink);
    std::ifstream in(file, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}
