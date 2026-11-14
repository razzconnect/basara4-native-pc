// SPDX-License-Identifier: GPL-2.0-or-later
// RenderTrace requested but the log cannot be created: the reason is printed and kept for the capture
// header instead of the trace silently doing nothing, and recording keeps working without a file.
// No game or Vulkan device required.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>

#include "video_core/renderer_vulkan/vk_render_trace.h"

namespace {
void SetEnv(const char* name, const char* value) {
#ifdef _WIN32
    _putenv_s(name, value);
#else
    setenv(name, value, 1);
#endif
}
} // namespace

int main() {
#ifdef _WIN32
    _set_error_mode(_OUT_TO_STDERR);
#endif
    namespace RT = Vulkan::RenderTrace;

    const auto tmp_dir = std::filesystem::temp_directory_path() / "basara4_render_trace_open_fail";
    std::error_code ec;
    std::filesystem::remove_all(tmp_dir, ec);
    std::filesystem::create_directories(tmp_dir, ec);
    // A regular file where the log folder should be: neither the folder nor the log can be created.
    const auto blocker = tmp_dir / "blocker";
    std::ofstream(blocker) << "not a folder";
    const auto log_path = blocker / "sub" / "render_trace.log";
    const auto log_u8 = log_path.u8string();
    const std::string log_str(reinterpret_cast<const char*>(log_u8.data()), log_u8.size());
    SetEnv("SPARTAN_RENDER_TRACE", log_str.c_str());

    // Same order as spartanportgpu_init: the exit hook is registered before the state exists.
    std::atexit(&RT::WriteSessionSummary);
    assert(RT::Enabled());

    RT::Log("test.first_line", "opens the log");
    RT::RecordAnomaly(RT::Anomaly::GraphicsPipelineCreateFail, "vs=1 ps=2", true);

    const std::string header = RT::FormatHeader();
    assert(header.find("trace_log_error: cannot create the log folder") != std::string::npos);
    assert(header.find("pipeline.gfx_create_fail vs=1 ps=2") != std::string::npos);
    assert(!std::filesystem::exists(log_path));

    std::filesystem::remove_all(tmp_dir, ec);
    std::puts("render-trace-open-fail-test ok");
    return 0;
}
