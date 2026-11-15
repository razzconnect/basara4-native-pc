// SPDX-License-Identifier: GPL-2.0-or-later
// RenderTrace telemetry, deduped anomaly ring, auto-capture queue, and SPARTAN_RENDER_TRACE log.
// No game or Vulkan device required.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#include "support/capture_stdout.h"
#include "video_core/renderer_vulkan/vk_render_trace.h"

namespace {
void SetEnv(const char* name, const char* value) {
#ifdef _WIN32
    _putenv_s(name, value ? value : "");
#else
    if (value) {
        setenv(name, value, 1);
    } else {
        unsetenv(name);
    }
#endif
}

std::string ReadAll(const std::filesystem::path& p) {
    std::ifstream f(p, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}

size_t CountOccurrences(const std::string& text, const std::string& needle) {
    size_t count = 0;
    size_t pos = 0;
    while ((pos = text.find(needle, pos)) != std::string::npos) {
        ++count;
        pos += needle.size();
    }
    return count;
}
} // namespace

int main() {
#ifdef _WIN32
    _set_error_mode(_OUT_TO_STDERR);
#endif
    namespace RT = Vulkan::RenderTrace;

    const auto tmp_dir = std::filesystem::temp_directory_path() / "basara4_render_trace_test";
    std::error_code ec;
    std::filesystem::remove_all(tmp_dir, ec);
    std::filesystem::create_directories(tmp_dir, ec);
    const auto log_path = tmp_dir / "render_trace.log";
    const auto log_u8 = log_path.u8string();
    const std::string log_str(reinterpret_cast<const char*>(log_u8.data()), log_u8.size());
    SetEnv("SPARTAN_RENDER_TRACE", log_str.c_str());

    // Same order as spartanportgpu_init: the exit hook is registered before the state exists, and
    // runs again (as a no-op) after the explicit summary below.
    std::atexit(&RT::WriteSessionSummary);
    assert(RT::Enabled());
    assert(RT::LogPath() == log_path);

    // Metadata updates should write once without duplication across lazy file open + updates.
    RT::SetGpuInfo("NVIDIA | RTX 4060 (dGPU) | Vulkan 1.3.280 | 560.94.0.0 | no_msaa=0", "");
    RT::SetGpuInfo("", "mixed_depth=1 budget_mb=7900");
    RT::AddUnsupportedFormat("surface dfmt=1 nfmt=1 vk=eR8Unorm missing=BlitDst");
    RT::SetConfigSummary("mode=1 vsync=1 fps=60 res=0 render_res=1080p");
    RT::SetSceneSize(1280, 720);
    RT::SetDisplayInfo(1280, 720, 1920, 1080, true, true, false, 0.0f, 0.0f, 1.0f, 1.0f);

    // Relaxed counters.
    RT::Count(RT::Counter::FramesPresented, 180);
    RT::Count(RT::Counter::GraphicsPipelineFails, 2);
    assert(RT::GetCount(RT::Counter::FramesPresented) == 180);
    assert(RT::GetCount(RT::Counter::GraphicsPipelineFails) == 2);

    // Per-tag detail budget: 600 distinct details of one tag record kTracedDetailsPerTag of them, and
    // the detail text is not built once the budget is spent.
    int tsharp_formats = 0;
    for (int i = 0; i < 600; ++i) {
        RT::RecordAnomaly(RT::Anomaly::TexInvalidTSharp, [&] {
            ++tsharp_formats;
            return "addr=0x" + std::to_string(0x10000 + i * 0x100) + " w=64";
        });
    }
    assert(tsharp_formats == int(RT::kTracedDetailsPerTag));
    // LogOnce has its own per-tag key set: a flood of anomalies cannot stop it.
    for (int i = 0; i < 70; ++i) {
        RT::LogOnce("pool_type_" + std::to_string(i), "tex.pool_type_mismatch",
                    "key=" + std::to_string(i));
    }
    RT::LogOnce("pool_type_0", "tex.pool_type_mismatch", "duplicate key ignored");
    RT::LogOnce("first", "test.log_once_other_tag", "other tag still logged");

    // A new tag after the flood is still recorded and still queues the anomaly auto capture.
    RT::RecordAnomaly(RT::Anomaly::GraphicsPipelineCreateFail, "vs=11 ps=22", true);
    RT::RecordAnomaly(RT::Anomaly::GraphicsPipelineCreateFail, "vs=11 ps=22", true); // duplicate
    std::string reason;
    assert(RT::ConsumeAutoCapture(reason));
    assert(reason == "auto_pipeline.gfx_create_fail");
    assert(!RT::ConsumeAutoCapture(reason));

    for (int i = 0; i < 40; ++i) {
        RT::RecordAnomaly(RT::Anomaly::TexFormatRetry, "id=" + std::to_string(i), false);
    }

    // Startup capture by time since the first flip, not by flip count: 480 Hz flips (unlocked
    // frame rate) for 2.9 s do not trigger it, the first flip at 3 s does, once.
    {
        auto& state = RT::detail::GetState();
        constexpr u64 kFirstFlipMs = 950;
        u64 flips = 0;
        for (u64 t = kFirstFlipMs; t < kFirstFlipMs + RT::kStartupCaptureDelayMs; t += 2, ++flips) {
            RT::detail::OnFlipAt(state, t);
        }
        assert(flips > 1000); // far past the old trigger (flip 180)
        assert(!RT::ConsumeAutoCapture(reason));
        RT::detail::OnFlipAt(state, kFirstFlipMs + RT::kStartupCaptureDelayMs);
        assert(RT::ConsumeAutoCapture(reason));
        assert(reason == "auto_startup");
        RT::detail::OnFlipAt(state, kFirstFlipMs + 2 * RT::kStartupCaptureDelayMs);
        assert(!RT::ConsumeAutoCapture(reason));
    }

    for (u32 i = 0; i < RT::kMovieCaptureFrame; ++i) {
        RT::OnMovieFrame();
    }
    assert(RT::ConsumeAutoCapture(reason));
    assert(reason == "auto_movie");

    RT::OnFrameCaptureSaved("frame_1.txt", "frame_1.png", "auto_startup");

    const std::string header = RT::FormatHeader();
    assert(header.find("gpu: NVIDIA | RTX 4060") != std::string::npos);
    assert(header.find("caps: mixed_depth=1 budget_mb=7900") != std::string::npos);
    assert(header.find("settings: mode=1 vsync=1") != std::string::npos);
    assert(header.find("scene: raster=1280x720 half=640x360") != std::string::npos);
    assert(header.find("display: guest=1280x720 frame=1920x1080") != std::string::npos);
    assert(header.find("presented=180") != std::string::npos);
    assert(header.find("gfx_pipe_fail=2") != std::string::npos);
    assert(header.find("unsupported_formats:") != std::string::npos);
    assert(header.find("recent_anomalies:") != std::string::npos);
    // 64 T# + 1 pipeline fail + 40 retries -> only the newest kRecentAnomalyLines (32) are kept
    // (id=39 present, id=0 evicted).
    static_assert(RT::kRecentAnomalyLines == 32);
    assert(header.find("id=39") != std::string::npos);
    assert(header.find("id=0\n") == std::string::npos);

    // Tracing on: last_run.log (stdout) still gets the one present summary line.
    const std::string stdout_text = CaptureStdout(tmp_dir / "stdout.txt", [] {
        RT::WriteSessionSummary();
        RT::WriteSessionSummary(); // idempotent
    });
    assert(CountOccurrences(stdout_text, "Present summary:") == 1);
    assert(stdout_text.find("Present summary: presented=180 ") == 0);

    const std::string written = ReadAll(log_path);
    assert(CountOccurrences(written, "gpu.info ") == 1);
    assert(CountOccurrences(written, "gpu.caps ") == 1);
    assert(CountOccurrences(written, "config ") == 1);
    assert(CountOccurrences(written, "gpu.unsupported_format ") == 1);
    assert(CountOccurrences(written, "session.summary ") == 1);
    assert(written.find("capture.saved reason=auto_startup trace=frame_1.txt screenshot=frame_1.png") !=
           std::string::npos);
    assert(written.find("presented=180") != std::string::npos);
    assert(written.find(" acquire_timeouts=0 acquire_errors=0 ") != std::string::npos);
    assert(CountOccurrences(written, "tex.invalid_tsharp ") == RT::kTracedDetailsPerTag);
    assert(written.find("pipeline.gfx_create_fail vs=11 ps=22") != std::string::npos);
    assert(CountOccurrences(written, "pipeline.gfx_create_fail ") == 1);
    assert(CountOccurrences(written, "tex.pool_type_mismatch ") == RT::kLogOnceKeysPerTag);
    assert(written.find("duplicate key ignored") == std::string::npos);
    assert(written.find("test.log_once_other_tag other tag still logged") != std::string::npos);
    assert(written.find("details_budget_reached=tex.invalid_tsharp") != std::string::npos);

    std::filesystem::remove_all(tmp_dir, ec);
    std::puts("render-trace-test ok");
    return 0;
}
