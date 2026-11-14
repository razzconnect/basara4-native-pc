// SPDX-License-Identifier: GPL-2.0-or-later
// RenderTrace with SPARTAN_RENDER_TRACE off: detail text is built at most kUntracedEventsPerTag times
// per tag (only for the recent-anomalies list of an overlay capture), Log/LogOnce never build their
// text, and one busy tag does not stop another tag from being recorded.
// No game or Vulkan device required.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "video_core/renderer_vulkan/vk_render_trace.h"

namespace {
void UnsetEnv(const char* name) {
#ifdef _WIN32
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}
} // namespace

int main() {
#ifdef _WIN32
    _set_error_mode(_OUT_TO_STDERR);
#endif
    namespace RT = Vulkan::RenderTrace;
    UnsetEnv("SPARTAN_RENDER_TRACE");
    // Same order as spartanportgpu_init: the exit hook is registered before the state exists.
    std::atexit(&RT::WriteSessionSummary);
    assert(!RT::Enabled());

    // A tag that fires on every draw with a new detail: formatted only kUntracedEventsPerTag times.
    int tsharp_formats = 0;
    for (int i = 0; i < 100000; ++i) {
        RT::RecordAnomaly(RT::Anomaly::TexInvalidTSharp, [&] {
            ++tsharp_formats;
            return "addr=" + std::to_string(i);
        });
    }
    assert(tsharp_formats == int(RT::kUntracedEventsPerTag));

    // The same detail every time: repeats also spend the budget, so the cost stays bounded.
    int depth_formats = 0;
    for (int i = 0; i < 1000; ++i) {
        RT::RecordAnomaly(
            RT::Anomaly::TexFindDepthSubstitute,
            [&] {
                ++depth_formats;
                return std::string("addr=0x1000 want=R16G16B16A16Sfloat");
            },
            true);
    }
    assert(depth_formats == int(RT::kUntracedEventsPerTag));

    // Another tag after both floods is still recorded.
    RT::RecordAnomaly(RT::Anomaly::TexOverlapFail, "addr=0x2000 levels 3 vs 1");

    // Log and LogOnce do not build their text while off.
    int log_formats = 0;
    RT::Log("test.log", [&] {
        ++log_formats;
        return std::string("never");
    });
    RT::LogOnce(
        [&] {
            ++log_formats;
            return std::string("key");
        },
        "test.log_once",
        [&] {
            ++log_formats;
            return std::string("never");
        });
    assert(log_formats == 0);

    // No auto capture is queued while off.
    std::string reason;
    assert(!RT::ConsumeAutoCapture(reason));

    // The overlay capture header still lists recent anomalies of each tag.
    const std::string header = RT::FormatHeader();
    assert(header.find("recent_anomalies:") != std::string::npos);
    assert(header.find("tex.invalid_tsharp addr=0\n") != std::string::npos);
    assert(header.find("tex.find_depth_sub addr=0x1000") != std::string::npos);
    assert(header.find("tex.overlap_fail addr=0x2000") != std::string::npos);
    assert(header.find("trace_log_error") == std::string::npos);

    std::puts("render-trace-off-test ok");
    return 0;
}
