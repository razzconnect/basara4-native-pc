// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC (Razz): lightweight rendering telemetry and on-demand trace support.
// Zero hot-loop cost when SPARTAN_RENDER_TRACE is off (relaxed atomics incremented only on errors,
// fallbacks, or rare lifecycle changes). When SPARTAN_RENDER_TRACE=<file|1> is active, writes a
// timestamped render_trace.log (capped at 16 MiB) and requests up to 4 auto frame captures.
#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <filesystem>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>

#include "common/types.h"

namespace Vulkan::RenderTrace {

enum class Counter : std::size_t {
    FramesPresented = 0,
    PresentSkips,
    SwapchainRecreates,
    ShaderCaptureFails,
    ShaderCompileFails,
    GraphicsPipelineFails,
    ComputePipelineFails,
    PipelinePendingDrops,
    BindFalseDrops,
    InvalidTSharps,
    DepthColorSubstitutions,
    TextureFormatRetries,
    TextureStillNotFit,
    PoolTypeMismatches,
    OverlapResolveFails,
    VmaBudgetFallbacks,
    AvPlayerErrors,
    Count
};

inline constexpr std::array<const char*, std::size_t(Counter::Count)> CounterNames = {{
    "presented",
    "present_skips",
    "swapchain_recreates",
    "shader_cap_fail",
    "shader_comp_fail",
    "gfx_pipe_fail",
    "cs_pipe_fail",
    "pipe_pending_drop",
    "bind_false",
    "invalid_tsharp",
    "depth_sub",
    "fmt_retry",
    "still_not_fit",
    "pool_mismatch",
    "overlap_fail",
    "vma_fallback",
    "avplayer_err",
}};

namespace detail {

struct State {
    std::array<std::atomic<u64>, std::size_t(Counter::Count)> counters{};
    std::atomic<bool> auto_capture_pending{false};
    std::atomic<u32> auto_captures_taken{0};
    std::atomic<u32> movie_frames{0};
    std::atomic<bool> startup_auto_done{false};
    std::atomic<bool> movie_auto_done{false};
    std::atomic<bool> anomaly_auto_done{false};
    std::atomic<bool> summary_written{false};

    std::mutex mutex;
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    std::string gpu_summary;
    std::string gpu_caps;
    std::string config_summary;
    std::string scene_summary;
    std::string display_summary;
    std::string auto_capture_reason;
    std::vector<std::string> unsupported_formats;
    std::deque<std::string> recent_anomalies;
    std::unordered_set<std::string> seen_keys;
    FILE* log_file = nullptr;
    bool log_init = false;
    u64 log_bytes = 0;
};

inline State& GetState() {
    static State state;
    return state;
}

inline std::string WallTime() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t sec = std::chrono::system_clock::to_time_t(now);
    std::tm local{};
#ifdef _WIN32
    localtime_s(&local, &sec);
#else
    localtime_r(&sec, &local);
#endif
    char buf[16];
    std::strftime(buf, sizeof(buf), "%H:%M:%S", &local);
    return buf;
}

inline u64 ElapsedMs(const State& s) {
    return u64(std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::steady_clock::now() - s.start)
                   .count());
}

inline std::filesystem::path ResolveLogPath() {
    const char* env = std::getenv("SPARTAN_RENDER_TRACE");
    if (!env || !env[0] || std::strcmp(env, "0") == 0) {
        return {};
    }
    if (std::strcmp(env, "1") == 0) {
        const char* user = std::getenv("SPARTAN_GPU_USER_DIR");
        return std::filesystem::path(user && user[0] ? user : "user") / "render_trace.log";
    }
    return std::filesystem::path(reinterpret_cast<const char8_t*>(env));
}

inline void EnsureLogFileLocked(State& s) {
    if (s.log_init) {
        return;
    }
    s.log_init = true;
    const auto path = ResolveLogPath();
    if (path.empty()) {
        return;
    }
    std::error_code ec;
    if (path.has_parent_path()) {
        std::filesystem::create_directories(path.parent_path(), ec);
    }
#ifdef _WIN32
    s.log_file = _wfopen(path.wstring().c_str(), L"wb");
#else
    s.log_file = std::fopen(path.c_str(), "wb");
#endif
    if (s.log_file) {
        const std::string banner = "=== Basara 4 PC Render Trace Log ===\n";
        std::fwrite(banner.data(), 1, banner.size(), s.log_file);
        s.log_bytes = banner.size();
        if (!s.gpu_summary.empty()) {
            const std::string line = "[     0 ms] gpu.info " + s.gpu_summary + "\n";
            std::fwrite(line.data(), 1, line.size(), s.log_file);
            s.log_bytes += line.size();
        }
        if (!s.gpu_caps.empty()) {
            const std::string line = "[     0 ms] gpu.caps " + s.gpu_caps + "\n";
            std::fwrite(line.data(), 1, line.size(), s.log_file);
            s.log_bytes += line.size();
        }
        if (!s.config_summary.empty()) {
            const std::string line = "[     0 ms] config " + s.config_summary + "\n";
            std::fwrite(line.data(), 1, line.size(), s.log_file);
            s.log_bytes += line.size();
        }
        for (const auto& fmt_line : s.unsupported_formats) {
            const std::string line = "[     0 ms] gpu.unsupported_format " + fmt_line + "\n";
            std::fwrite(line.data(), 1, line.size(), s.log_file);
            s.log_bytes += line.size();
        }
        std::fflush(s.log_file);
    }
}

inline void WriteLogLocked(State& s, std::string_view tag, std::string_view msg) {
    EnsureLogFileLocked(s);
    constexpr u64 kMaxBytes = 16ULL * 1024 * 1024;
    if (!s.log_file || s.log_bytes >= kMaxBytes) {
        return;
    }
    char prefix[32];
    const int n = std::snprintf(prefix, sizeof(prefix), "[%6llu ms] ",
                                static_cast<unsigned long long>(ElapsedMs(s)));
    if (n > 0) {
        std::fwrite(prefix, 1, std::size_t(n), s.log_file);
        s.log_bytes += std::size_t(n);
    }
    std::fwrite(tag.data(), 1, tag.size(), s.log_file);
    std::fwrite(" ", 1, 1, s.log_file);
    std::fwrite(msg.data(), 1, msg.size(), s.log_file);
    std::fwrite("\n", 1, 1, s.log_file);
    s.log_bytes += tag.size() + msg.size() + 2;
    std::fflush(s.log_file);
}

inline void QueueAutoCaptureLocked(State& s, std::string_view reason) {
    if (s.auto_captures_taken.load(std::memory_order_relaxed) >= 4 ||
        s.auto_capture_pending.load(std::memory_order_relaxed)) {
        return;
    }
    s.auto_capture_reason = std::string(reason);
    s.auto_capture_pending.store(true, std::memory_order_release);
}

} // namespace detail

inline bool Enabled() {
    static const bool enabled = [] {
        const char* env = std::getenv("SPARTAN_RENDER_TRACE");
        return env && env[0] && std::strcmp(env, "0") != 0;
    }();
    return enabled;
}

inline std::filesystem::path LogPath() {
    return detail::ResolveLogPath();
}

inline void Count(Counter c, u64 delta = 1) {
    detail::GetState().counters[std::size_t(c)].fetch_add(delta, std::memory_order_relaxed);
}

inline u64 GetCount(Counter c) {
    return detail::GetState().counters[std::size_t(c)].load(std::memory_order_relaxed);
}

inline void SetGpuInfo(std::string summary, std::string caps) {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    const bool summary_changed = !summary.empty() && s.gpu_summary != summary;
    const bool caps_changed = !caps.empty() && s.gpu_caps != caps;
    if (!summary.empty()) {
        s.gpu_summary = std::move(summary);
    }
    if (!caps.empty()) {
        s.gpu_caps = std::move(caps);
    }
    if (Enabled()) {
        const bool already_open = s.log_init;
        detail::EnsureLogFileLocked(s);
        if (already_open && s.log_file) {
            if (summary_changed) {
                detail::WriteLogLocked(s, "gpu.info", s.gpu_summary);
            }
            if (caps_changed) {
                detail::WriteLogLocked(s, "gpu.caps", s.gpu_caps);
            }
        }
    }
}

inline void AddUnsupportedFormat(const std::string& detail_line) {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    if (s.unsupported_formats.size() < 24) {
        s.unsupported_formats.push_back(detail_line);
    }
    if (Enabled()) {
        const bool already_open = s.log_init;
        detail::EnsureLogFileLocked(s);
        if (already_open && s.log_file) {
            detail::WriteLogLocked(s, "gpu.unsupported_format", detail_line);
        }
    }
}

inline void SetConfigSummary(std::string cfg) {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    if (s.config_summary == cfg) {
        return;
    }
    s.config_summary = std::move(cfg);
    if (Enabled()) {
        const bool already_open = s.log_init;
        detail::EnsureLogFileLocked(s);
        if (already_open && s.log_file) {
            detail::WriteLogLocked(s, "config", s.config_summary);
        }
    }
}

inline void SetSceneSize(u32 w, u32 h) {
    auto& s = detail::GetState();
    char buf[96];
    std::snprintf(buf, sizeof(buf), "raster=%ux%u half=%ux%u", w, h, w / 2, h / 2);
    std::scoped_lock lk{s.mutex};
    if (s.scene_summary == buf) {
        return;
    }
    s.scene_summary = buf;
    if (Enabled()) {
        detail::WriteLogLocked(s, "scene.size", s.scene_summary);
    }
}

inline void SetDisplayInfo(u32 guest_w, u32 guest_h, u32 frame_w, u32 frame_h, bool fsr1,
                           bool rcas, bool srgb_in, float bar_x0, float bar_y0, float bar_x1,
                           float bar_y1) {
    auto& s = detail::GetState();
    char buf[160];
    std::snprintf(buf, sizeof(buf),
                  "guest=%ux%u frame=%ux%u srgb_in=%d fsr1=%d rcas=%d movie_bars=(%.3f,%.3f,%.3f,%.3f)",
                  guest_w, guest_h, frame_w, frame_h, int(srgb_in), int(fsr1), int(rcas), bar_x0,
                  bar_y0, bar_x1, bar_y1);
    std::scoped_lock lk{s.mutex};
    if (s.display_summary == buf) {
        return;
    }
    s.display_summary = buf;
    if (Enabled()) {
        detail::WriteLogLocked(s, "display.state", s.display_summary);
    }
}

inline void Log(std::string_view tag, std::string_view msg) {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    detail::WriteLogLocked(s, tag, msg);
}

inline void LogOnce(std::string_view key, std::string_view tag, std::string_view msg) {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    if (s.seen_keys.size() >= 256) {
        return;
    }
    if (!s.seen_keys.emplace(key).second) {
        return;
    }
    detail::WriteLogLocked(s, tag, msg);
}

inline void RecordAnomaly(std::string_view tag, std::string_view detail_msg,
                          bool trigger_auto_capture = false) {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    const std::string key = std::string(tag) + ":" + std::string(detail_msg);
    const bool is_new = s.seen_keys.size() < 512 && s.seen_keys.emplace(key).second;
    if (is_new) {
        if (s.recent_anomalies.size() >= 32) {
            s.recent_anomalies.pop_front();
        }
        s.recent_anomalies.push_back("[" + detail::WallTime() + "] " + std::string(tag) + " " +
                                     std::string(detail_msg));
    }
    if (Enabled() && (is_new || tag == "device.lost" || tag == "swapchain.recreate")) {
        detail::WriteLogLocked(s, tag, detail_msg);
        if (trigger_auto_capture && !s.anomaly_auto_done.exchange(true, std::memory_order_relaxed)) {
            detail::QueueAutoCaptureLocked(s, std::string("auto_") + std::string(tag));
        }
    }
}

inline void OnFlip(u64 flip_count) {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    if (flip_count == 180 && !s.startup_auto_done.exchange(true, std::memory_order_relaxed)) {
        std::scoped_lock lk{s.mutex};
        detail::QueueAutoCaptureLocked(s, "auto_startup");
    }
}

inline void OnMovieFrame() {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    const u32 n = s.movie_frames.fetch_add(1, std::memory_order_relaxed) + 1;
    if (n == 15 && !s.movie_auto_done.exchange(true, std::memory_order_relaxed)) {
        std::scoped_lock lk{s.mutex};
        detail::QueueAutoCaptureLocked(s, "auto_movie");
    }
}

inline bool ConsumeAutoCapture(std::string& reason_out) {
    auto& s = detail::GetState();
    if (!s.auto_capture_pending.exchange(false, std::memory_order_acq_rel)) {
        return false;
    }
    std::scoped_lock lk{s.mutex};
    s.auto_captures_taken.fetch_add(1, std::memory_order_relaxed);
    reason_out = std::move(s.auto_capture_reason);
    s.auto_capture_reason.clear();
    return true;
}

inline std::string FormatHeader() {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    std::string out;
    out.reserve(2048);
    if (!s.gpu_summary.empty()) {
        out += "gpu: " + s.gpu_summary + "\n";
    }
    if (!s.gpu_caps.empty()) {
        out += "caps: " + s.gpu_caps + "\n";
    }
    if (!s.config_summary.empty()) {
        out += "settings: " + s.config_summary + "\n";
    }
    if (!s.scene_summary.empty()) {
        out += "scene: " + s.scene_summary + "\n";
    }
    if (!s.display_summary.empty()) {
        out += "display: " + s.display_summary + "\n";
    }
    out += "anomalies:";
    for (std::size_t i = 0; i < std::size_t(Counter::Count); ++i) {
        const u64 val = s.counters[i].load(std::memory_order_relaxed);
        if (i == 0 || val != 0) {
            out += " ";
            out += CounterNames[i];
            out += "=";
            out += std::to_string(val);
        }
    }
    out += "\n";
    if (!s.unsupported_formats.empty()) {
        out += "unsupported_formats:\n";
        for (const auto& f : s.unsupported_formats) {
            out += "  " + f + "\n";
        }
    }
    if (!s.recent_anomalies.empty()) {
        out += "recent_anomalies:\n";
        for (const auto& a : s.recent_anomalies) {
            out += "  " + a + "\n";
        }
    }
    return out;
}

inline void OnFrameCaptureSaved(std::string_view txt_path, std::string_view png_path,
                                 std::string_view reason) {
    if (!Enabled()) {
        return;
    }
    std::string msg = "reason=" + std::string(reason) + " trace=" + std::string(txt_path);
    if (!png_path.empty()) {
        msg += " screenshot=" + std::string(png_path);
    }
    Log("capture.saved", msg);
}

inline void WriteSessionSummary() {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    if (s.summary_written.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    std::scoped_lock lk{s.mutex};
    if (!s.log_file) {
        return;
    }
    std::string line;
    for (std::size_t i = 0; i < std::size_t(Counter::Count); ++i) {
        if (i) line += " ";
        line += CounterNames[i];
        line += "=";
        line += std::to_string(s.counters[i].load(std::memory_order_relaxed));
    }
    detail::WriteLogLocked(s, "session.summary", line);
    std::fclose(s.log_file);
    s.log_file = nullptr;
}

} // namespace Vulkan::RenderTrace
