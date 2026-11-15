// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC (Razz): lightweight rendering telemetry and on-demand trace support.
// Cost when SPARTAN_RENDER_TRACE is off: Count() is one relaxed atomic add; Log/LogOnce return before
// building their text; RecordAnomaly builds its detail text and takes the mutex for at most
// kUntracedEventsPerTag events per tag per session (for the recent-anomalies list of an overlay
// capture), after that one relaxed atomic load per event. Detail text is passed as a callable so it
// is only formatted when it will be used. When SPARTAN_RENDER_TRACE=<file|1> is active, writes a
// timestamped render_trace.log (capped at kMaxLogBytes) and requests up to kMaxAutoCaptures auto
// frame captures.
#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
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
#include <system_error>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "common/types.h"

namespace Vulkan::RenderTrace {

enum class Counter : std::size_t {
    FramesPresented = 0,
    PresentSkips,
    AcquireTimeouts,
    AcquireErrors,
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
    OverlapViewLost,
    OverlapViewKept,
    VmaBudgetFallbacks,
    AvPlayerErrors,
    Count
};

inline constexpr std::array<const char*, std::size_t(Counter::Count)> CounterNames = {{
    "presented",
    "present_skips",
    "acquire_timeouts",
    "acquire_errors",
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
    "overlap_view_lost",
    "overlap_view_kept",
    "vma_fallback",
    "avplayer_err",
}};
static_assert(CounterNames.back() != nullptr, "every Counter needs a name");

// Every RecordAnomaly tag. Each tag has its own budgets (below), so one tag that fires often can
// never stop a different tag from being recorded or from queuing its auto capture.
enum class Anomaly : std::size_t {
    AvPlayerVideoOpenFail = 0,
    AvPlayerDemuxFail,
    AvPlayerSwsFail,
    AvPlayerSendPacketFail,
    AvPlayerReceiveFrameFail,
    VideoOutFlipQueueFull,
    VmaBufferFallback,
    VmaImageFallback,
    VmaPresenterFallback,
    ComputePipelineCreateFail,
    GraphicsPipelineCreateFail,
    GraphicsPipelineAsyncFail,
    SquarePipelineFail,
    UnsupportedShaderStage,
    ShaderCaptureFail,
    ShaderCompileFail,
    DeviceLost,
    CapturePngNoMemory,
    CapturePngWriteFail,
    ScreenshotUnsupportedFormat,
    PresentSkip,
    SwapchainCreateFail,
    SwapchainRecreate,
    TexInvalidTSharp,
    TexDepthAsColor,
    TexFormatRetry,
    TexFormatUnsupported,
    TexOverlapFail,
    TexOverlapViewLost,
    TexOverlapViewKept,
    TexFindDepthSubstitute,
    Count
};

struct AnomalyInfo {
    const char* name;
    // Lifecycle events whose repetitions matter (how often the device was lost or the swapchain
    // rebuilt): logged on every event while tracing, not only for a new detail text.
    bool log_every_event;
};

inline constexpr std::array<AnomalyInfo, std::size_t(Anomaly::Count)> AnomalyInfos = {{
    {"avplayer.video_open_fail", false},
    {"avplayer.demux_fail", false},
    {"avplayer.sws_fail", false},
    {"avplayer.send_pkt_fail", false},
    {"avplayer.recv_frame_fail", false},
    {"vo.flip_queue_full", false},
    {"vma.buffer_fallback", false},
    {"vma.image_fallback", false},
    {"vma.presenter_fallback", false},
    {"pipeline.cs_create_fail", false},
    {"pipeline.gfx_create_fail", false},
    {"pipeline.gfx_async_fail", false},
    {"pipeline.square_fail", false},
    {"pipeline.unsupported_stage", false},
    {"shader.capture_fail", false},
    {"shader.compile_fail", false},
    {"device.lost", true},
    {"capture.png_nomem", false},
    {"capture.png_write_fail", false},
    {"screenshot.unsupported_fmt", false},
    {"present.skip", false},
    {"swapchain.create_fail", false},
    {"swapchain.recreate", true},
    {"tex.invalid_tsharp", false},
    {"tex.depth_as_color", false},
    {"tex.format_retry", false},
    {"tex.format_unsupported", false},
    {"tex.overlap_fail", false},
    {"tex.overlap_view_lost", false},
    {"tex.overlap_view_kept", false},
    {"tex.find_depth_sub", false},
}};
static_assert(AnomalyInfos.back().name != nullptr, "every Anomaly needs a name");

inline constexpr const char* AnomalyName(Anomaly tag) {
    return AnomalyInfos[std::size_t(tag)].name;
}

// Trace off: events per tag whose detail is formatted into the recent-anomalies list (the only
// consumer while off is the header of a capture started from the overlay). The list keeps
// kRecentAnomalyLines lines, so 8 per tag leaves room for several tags, and the off-path cost is at
// most 8 formats + 8 lock/unlock per tag per session. Checked before formatting.
inline constexpr u32 kUntracedEventsPerTag = 8;
// Trace on: distinct detail texts recorded (log line + recent list) per tag. 64 examples of one tag
// show its pattern (addresses, formats, sizes); more only grow the log and the dedupe set. Every
// event is still counted by the caller's Counter, and tags that reach this budget are listed in
// session.summary. Checked before formatting.
inline constexpr u32 kTracedDetailsPerTag = 64;
// Distinct keys per LogOnce tag. Today's caller keys by image-type pairs (a handful of values); the
// cap only stops a future caller that keys by address from growing the set without bound.
inline constexpr std::size_t kLogOnceKeysPerTag = 64;
// Newest anomaly lines kept for the capture header: enough context for a bug report and still short
// enough to read at the top of the capture file.
inline constexpr std::size_t kRecentAnomalyLines = 32;
// Unsupported formats kept for the log start and capture header. Format support is fixed per device,
// so this only bounds the list printed for a device that lacks many formats.
inline constexpr std::size_t kMaxUnsupportedFormats = 24;
// render_trace.log stops growing at this size so a long session cannot fill the disk.
inline constexpr u64 kMaxLogBytes = 16ULL * 1024 * 1024;
// Each auto trigger (startup, movie, first anomaly) fires at most once, so at most 3 captures are
// queued; this cap is a backstop so a future trigger cannot fill user\captures.
inline constexpr u32 kMaxAutoCaptures = 4;
// Startup auto capture: the first flip at least this long after the first flip, so the capture
// records the first seconds of rendering on this GPU instead of the black boot screen. Measured in
// time, not flips: it was flip 180 (3 s at 60 fps), but with an unlocked frame rate the launcher
// runs vblank at 480 Hz (SPARTAN_VBLANK_HZ=0) and flip 180 came 0.4-0.9 s after the first one,
// still on the black boot screen (13 of 13 such sessions; at 60 fps it came at 3.7 s and showed
// the game, 1 of 1).
inline constexpr u64 kStartupCaptureDelayMs = 3000;
// Movie auto capture at this decoded movie picture: about 0.5 s into the first movie at 30 fps, so
// playback and the movie compositing path (bars, scaling) are running when the frame is captured.
inline constexpr u32 kMovieCaptureFrame = 15;

namespace detail {

struct TagBudget {
    std::atomic<u32> untraced_events{0};
    std::atomic<u32> traced_details{0};
};

struct State {
    std::array<std::atomic<u64>, std::size_t(Counter::Count)> counters{};
    std::array<TagBudget, std::size_t(Anomaly::Count)> budgets{};
    std::atomic<bool> auto_capture_pending{false};
    std::atomic<u32> auto_captures_taken{0};
    std::atomic<u32> movie_frames{0};
    std::atomic<u64> first_flip_ms{0}; ///< ElapsedMs of the first flip + 1 (0: no flip yet)
    std::atomic<bool> startup_auto_done{false};
    std::atomic<bool> movie_auto_done{false};
    std::atomic<bool> anomaly_auto_done{false};
    std::atomic<bool> summary_written{false};
    std::atomic<bool> present_summary_written{false};

    std::mutex mutex;
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    std::string gpu_summary;
    std::string gpu_caps;
    std::string config_summary;
    std::string scene_summary;
    std::string display_summary;
    std::string present_mode;         ///< swapchain present mode in use (empty: none created)
    std::string last_acquire_failure; ///< result of the newest acquire that gave no image
    std::string auto_capture_reason;
    std::string log_error;
    std::vector<std::string> unsupported_formats;
    std::deque<std::string> recent_anomalies;
    // Dedupe sets: one per anomaly tag, and one per LogOnce tag, so neither can fill the other.
    std::array<std::unordered_set<std::string>, std::size_t(Anomaly::Count)> anomaly_details;
    std::unordered_map<std::string, std::unordered_set<std::string>> log_once_keys;
    FILE* log_file = nullptr;
    bool log_init = false;
    u64 log_bytes = 0;
};

// Never destroyed: WriteSessionSummary runs from std::atexit and decoder/render threads can still
// record anomalies while the process exits, so the state must outlive every static destructor.
inline State& GetState() {
    static State& state = *new State;
    return state;
}

// Detail and message arguments are either text (string_view-convertible) or a callable returning
// text; a callable is only invoked when the text will be used.
template <typename Text>
inline std::string Materialize(Text&& text) {
    if constexpr (std::is_invocable_v<Text&>) {
        return std::string(text());
    } else {
        return std::string(std::string_view(text));
    }
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

inline std::string PathText(const std::filesystem::path& path) {
    const auto u8 = path.u8string();
    return std::string(reinterpret_cast<const char*>(u8.data()), u8.size());
}

// The trace was requested but cannot be written: say so on stdout (last_run.log) and keep the reason
// for the capture header, instead of silently running without a log.
inline void ReportLogErrorLocked(State& s, std::string message) {
    std::printf("Render trace: %s\n", message.c_str());
    std::fflush(stdout);
    if (s.log_error.empty()) {
        s.log_error = std::move(message);
    }
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
    if (path.has_parent_path()) {
        std::error_code ec;
        std::filesystem::create_directories(path.parent_path(), ec);
        if (ec) {
            ReportLogErrorLocked(s, "cannot create the log folder " + PathText(path.parent_path()) +
                                        ": " + ec.message());
        }
    }
#ifdef _WIN32
    s.log_file = _wfopen(path.wstring().c_str(), L"wb");
#else
    s.log_file = std::fopen(path.c_str(), "wb");
#endif
    if (!s.log_file) {
        const int err = errno;
        ReportLogErrorLocked(s, "cannot open " + PathText(path) + ": " +
                                    std::generic_category().message(err) +
                                    "; the render trace is not written this session");
        return;
    }
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

inline void WriteLogLocked(State& s, std::string_view tag, std::string_view msg) {
    EnsureLogFileLocked(s);
    if (!s.log_file || s.log_bytes >= kMaxLogBytes) {
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
    if (s.auto_captures_taken.load(std::memory_order_relaxed) >= kMaxAutoCaptures ||
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

// An acquire that gave no swapchain image. Timeout: the presentation engine held every image for
// the whole acquire wait (eTimeout, eNotReady). Error: the swapchain or surface has to be rebuilt
// (out of date, surface or exclusive fullscreen lost, device lost). Called only on failure, so the
// mutex is never taken for a frame that presents.
enum class AcquireFailure { Timeout, Error };

inline void NoteAcquireFailure(AcquireFailure kind, std::string_view result) {
    Count(kind == AcquireFailure::Timeout ? Counter::AcquireTimeouts : Counter::AcquireErrors);
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    s.last_acquire_failure.assign(result);
}

// The present mode the swapchain was created with (set again when VSync changes).
inline void SetPresentMode(std::string mode) {
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    s.present_mode = std::move(mode);
}

// One line for last_run.log, trace on or off: whether the game kept presenting frames, how many it
// skipped because no swapchain image came back, and why the last acquire failed. A log that ends
// without it was not closed by the game (Force stop, crash).
inline std::string FormatPresentSummary() {
    auto& s = detail::GetState();
    std::string line = "Present summary:";
    for (const Counter c : {Counter::FramesPresented, Counter::PresentSkips,
                            Counter::AcquireTimeouts, Counter::AcquireErrors,
                            Counter::SwapchainRecreates}) {
        line += " ";
        line += CounterNames[std::size_t(c)];
        line += "=";
        line += std::to_string(GetCount(c));
    }
    std::scoped_lock lk{s.mutex};
    line += " last_acquire_failure=";
    line += s.last_acquire_failure.empty() ? "none" : s.last_acquire_failure;
    line += " present_mode=";
    line += s.present_mode.empty() ? "none" : s.present_mode;
    line += "\n";
    return line;
}

// Writes FormatPresentSummary() to out once per process.
inline void WritePresentSummary(std::FILE* out) {
    auto& s = detail::GetState();
    if (s.present_summary_written.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    const std::string line = FormatPresentSummary();
    std::fputs(line.c_str(), out);
    std::fflush(out);
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
    if (s.unsupported_formats.size() < kMaxUnsupportedFormats) {
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

// msg: text or a callable returning text; not evaluated when the trace is off.
template <typename Message>
inline void Log(std::string_view tag, Message&& msg) {
    if (!Enabled()) {
        return;
    }
    const std::string text = detail::Materialize(std::forward<Message>(msg));
    auto& s = detail::GetState();
    std::scoped_lock lk{s.mutex};
    detail::WriteLogLocked(s, tag, text);
}

// Logs the first message for each key of a tag (up to kLogOnceKeysPerTag keys per tag). key and msg:
// text or callables returning text; not evaluated when the trace is off.
template <typename Key, typename Message>
inline void LogOnce(Key&& key, std::string_view tag, Message&& msg) {
    if (!Enabled()) {
        return;
    }
    std::string key_text = detail::Materialize(std::forward<Key>(key));
    auto& s = detail::GetState();
    {
        std::scoped_lock lk{s.mutex};
        auto& keys = s.log_once_keys[std::string(tag)];
        if (keys.size() >= kLogOnceKeysPerTag || !keys.emplace(std::move(key_text)).second) {
            return;
        }
    }
    const std::string text = detail::Materialize(std::forward<Message>(msg));
    std::scoped_lock lk{s.mutex};
    detail::WriteLogLocked(s, tag, text);
}

// Records one anomaly event. detail_text: text or a callable returning text. The per-tag budgets
// are checked before detail_text is evaluated and before the mutex is taken (see kUntracedEventsPerTag and
// kTracedDetailsPerTag); a new detail text of a tag goes to the recent list and, while tracing, to the
// log, and the first such anomaly with trigger_auto_capture queues an auto capture.
template <typename Detail>
inline void RecordAnomaly(Anomaly tag, Detail&& detail_text, bool trigger_auto_capture = false) {
    const std::size_t index = std::size_t(tag);
    const AnomalyInfo& info = AnomalyInfos[index];
    auto& s = detail::GetState();
    auto& budget = s.budgets[index];
    const bool tracing = Enabled();
    if (tracing) {
        if (!info.log_every_event &&
            budget.traced_details.load(std::memory_order_relaxed) >= kTracedDetailsPerTag) {
            return;
        }
    } else {
        if (budget.untraced_events.load(std::memory_order_relaxed) >= kUntracedEventsPerTag ||
            budget.untraced_events.fetch_add(1, std::memory_order_relaxed) >=
                kUntracedEventsPerTag) {
            return;
        }
    }
    const std::string text = detail::Materialize(std::forward<Detail>(detail_text));
    std::scoped_lock lk{s.mutex};
    auto& seen = s.anomaly_details[index];
    bool is_new = false;
    if (seen.size() < kTracedDetailsPerTag) {
        is_new = seen.emplace(text).second;
        budget.traced_details.store(u32(seen.size()), std::memory_order_relaxed);
    }
    if (is_new) {
        if (s.recent_anomalies.size() >= kRecentAnomalyLines) {
            s.recent_anomalies.pop_front();
        }
        s.recent_anomalies.push_back("[" + detail::WallTime() + "] " + info.name + " " + text);
    }
    if (tracing && (is_new || info.log_every_event)) {
        detail::WriteLogLocked(s, info.name, text);
        if (trigger_auto_capture && !s.anomaly_auto_done.exchange(true, std::memory_order_relaxed)) {
            detail::QueueAutoCaptureLocked(s, std::string("auto_") + info.name);
        }
    }
}

namespace detail {
/// A flip at now_ms (ElapsedMs): queues the startup capture on the first flip at least
/// kStartupCaptureDelayMs after the first one, whatever the flip rate.
inline void OnFlipAt(State& s, u64 now_ms) {
    if (s.startup_auto_done.load(std::memory_order_relaxed)) {
        return;
    }
    u64 first = 0;
    if (s.first_flip_ms.compare_exchange_strong(first, now_ms + 1, std::memory_order_relaxed)) {
        return;
    }
    if (now_ms + 1 - first >= kStartupCaptureDelayMs &&
        !s.startup_auto_done.exchange(true, std::memory_order_relaxed)) {
        std::scoped_lock lk{s.mutex};
        QueueAutoCaptureLocked(s, "auto_startup");
    }
}
} // namespace detail

inline void OnFlip() {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    detail::OnFlipAt(s, detail::ElapsedMs(s));
}

inline void OnMovieFrame() {
    if (!Enabled()) {
        return;
    }
    auto& s = detail::GetState();
    const u32 n = s.movie_frames.fetch_add(1, std::memory_order_relaxed) + 1;
    if (n == kMovieCaptureFrame && !s.movie_auto_done.exchange(true, std::memory_order_relaxed)) {
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
    if (!s.log_error.empty()) {
        out += "trace_log_error: " + s.log_error + "\n";
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

// The end of a session: every exit the game controls calls this (window closed or Stop game, the
// guest's exit() through std::atexit, device lost, presenter teardown). The present summary goes to
// last_run.log in every session; session.summary only to render_trace.log while tracing.
inline void WriteSessionSummary() {
    WritePresentSummary(stdout);
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
    std::string saturated;
    for (std::size_t i = 0; i < std::size_t(Anomaly::Count); ++i) {
        if (s.anomaly_details[i].size() >= kTracedDetailsPerTag) {
            saturated += saturated.empty() ? "" : ",";
            saturated += AnomalyInfos[i].name;
        }
    }
    if (!saturated.empty()) {
        line += " details_budget_reached=" + saturated;
    }
    detail::WriteLogLocked(s, "session.summary", line);
    std::fclose(s.log_file);
    s.log_file = nullptr;
}

} // namespace Vulkan::RenderTrace
