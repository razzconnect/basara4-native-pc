// SPDX-License-Identifier: GPL-2.0-or-later
// The present summary in last_run.log with SPARTAN_RENDER_TRACE off: every session that ends
// through RenderTrace::WriteSessionSummary (window closed / Stop game, exit(), device lost, presenter
// teardown) writes exactly one "Present summary:" line to stdout, with the frames presented, frames
// skipped, acquire timeouts and errors, the newest failed acquire result and the present mode.
// No game or Vulkan device required.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

#include "support/capture_stdout.h"
#include "video_core/renderer_vulkan/vk_render_trace.h"

namespace {
void UnsetEnv(const char* name) {
#ifdef _WIN32
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}

size_t CountOccurrences(const std::string& text, const std::string& needle) {
    size_t count = 0;
    for (size_t pos = text.find(needle); pos != std::string::npos;
         pos = text.find(needle, pos + 1)) {
        ++count;
    }
    return count;
}
} // namespace

int main() {
#ifdef _WIN32
    _set_error_mode(_OUT_TO_STDERR);
#endif
    namespace RT = Vulkan::RenderTrace;
    UnsetEnv("SPARTAN_RENDER_TRACE");
    // Same order as spartanportgpu_init: the exit hook is registered before the state exists; it
    // runs again at exit and must not write a second line.
    std::atexit(&RT::WriteSessionSummary);
    assert(!RT::Enabled());

    // A session that ends before any swapchain exists (no frame, no present mode).
    assert(RT::FormatPresentSummary() ==
           "Present summary: presented=0 present_skips=0 acquire_timeouts=0 acquire_errors=0 "
           "swapchain_recreates=0 last_acquire_failure=none present_mode=none\n");

    // The swapchain is created with VSync off; the user's case: a few acquires time out and their
    // frames are skipped, then frames present again; one Alt-Tab style out-of-date error.
    RT::SetPresentMode("Immediate");
    for (int i = 0; i < 4; ++i) {
        RT::NoteAcquireFailure(RT::AcquireFailure::Timeout, "Timeout");
        RT::Count(RT::Counter::PresentSkips);
    }
    RT::Count(RT::Counter::FramesPresented, 5);
    RT::NoteAcquireFailure(RT::AcquireFailure::Error, "ErrorOutOfDateKHR");
    RT::Count(RT::Counter::SwapchainRecreates);

    // The present thread counts while other threads read: counts stay exact.
    std::vector<std::thread> threads;
    for (int t = 0; t < 4; ++t) {
        threads.emplace_back([] {
            for (int i = 0; i < 10000; ++i) {
                RT::Count(RT::Counter::FramesPresented);
                if (i % 100 == 0) {
                    RT::NoteAcquireFailure(RT::AcquireFailure::Timeout, "Timeout");
                }
                if (i % 1000 == 0) {
                    (void)RT::FormatPresentSummary();
                }
            }
        });
    }
    for (auto& thread : threads) {
        thread.join();
    }

    // The newest failure and the newest present mode (VSync switched on in game) are reported.
    RT::NoteAcquireFailure(RT::AcquireFailure::Timeout, "NotReady");
    RT::SetPresentMode("Fifo");

    const std::string expected =
        "Present summary: presented=40005 present_skips=4 acquire_timeouts=405 acquire_errors=1 "
        "swapchain_recreates=1 last_acquire_failure=NotReady present_mode=Fifo\n";
    assert(RT::FormatPresentSummary() == expected);

    // The session end writes it to stdout (last_run.log) once, although the trace is off and
    // several exit paths call WriteSessionSummary.
    const auto out = std::filesystem::temp_directory_path() / "basara4_present_summary_test.txt";
    const std::string written = CaptureStdout(out, [] {
        RT::WriteSessionSummary();
        RT::WriteSessionSummary();
    });
    assert(written == expected);
    assert(CountOccurrences(written, "Present summary:") == 1);

    // Frames after the summary (presenter teardown after a window close) add no second line.
    RT::Count(RT::Counter::FramesPresented);
    const std::string again = CaptureStdout(out, [] { RT::WriteSessionSummary(); });
    assert(again.empty());

    std::error_code ec;
    std::filesystem::remove(out, ec);
    std::puts("present-summary-test ok");
    return 0;
}
