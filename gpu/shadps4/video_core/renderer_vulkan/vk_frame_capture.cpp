// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/renderer_vulkan/vk_frame_capture.h"
#include "video_core/renderer_vulkan/vk_render_trace.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <filesystem>
#include <format>
#include <mutex>

#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/texture_cache/image_info.h"

namespace Vulkan {

namespace {

struct Target {
    VAddr address{};
    vk::Format format{};
    u32 width{}, height{};
    u8 levels{}, layers{}, samples{};
    u8 base_mip{}, view_mips{}, base_layer{}, view_layers{};
    bool SameTarget(const Target& other) const {
        return address == other.address && format == other.format && width == other.width &&
               height == other.height && levels == other.levels && layers == other.layers &&
               samples == other.samples;
    }
    bool operator==(const Target&) const = default;
};

struct SkipStat {
    std::string reason;
    u64 vs_hash{};
    u64 ps_hash{};
    u32 count{};
};

struct Entry {
    bool compute{};
    std::vector<Target> colors;
    Target depth;
    u32 draws{};
    u64 indices{};
    std::vector<u64> shaders; ///< vs/ps pairs (graphics) or cs (compute), first few
    std::vector<std::pair<Target, bool>> sampled; ///< textures (false) and storage images (true)
    std::string state; ///< census line of the first pipeline (vk_pass_census.h)
    std::string scene_note; ///< SceneTargets / upscaler decision for this pass
    std::vector<std::string> viewports; ///< first few distinct viewport/scissor states
    std::string note;
    std::vector<std::string> buffers; ///< dumped constants, per draw
    std::vector<FrameCapture::ColorView> last_views; ///< color views of the last BeginPass
    std::vector<std::string> target_draws;          ///< "cbN draw K" lines (RecordViewsLocked)
};

// A pass that changes its target view on every draw keeps its first lines; a mip chain pass
// (one draw per level) needs a handful.
constexpr size_t kMaxTargetDrawLines = 32;

std::mutex capture_mutex;
// Guarded by capture_mutex.
std::vector<Entry> entries;
std::vector<std::pair<Target, bool>> pending_sampled;
std::vector<std::string> pending_buffers;
std::vector<SkipStat> pending_skips;
u64 total_skips = 0;
bool pass_open = false;

void AddBuffersLocked(Entry& entry) {
    // Constants of full-screen passes (post-processing: camera matrices) and of the first few draws
    // of each geometry pass.
    if (entry.draws <= 4) {
        for (auto& b : pending_buffers) {
            entry.buffers.push_back(std::move(b));
        }
    }
    pending_buffers.clear();
}

// Basara 4 PC: one line per color attachment whose CB address or view (base level/layer) leaves
// the image base on the pass's first draw, or changes from the previous draw of the pass. The
// pass groups its draws by image, so without these a mip target drawn into level 0 looks the same
// as one drawn into its own level.
void RecordViewsLocked(Entry& entry, const std::vector<Target>& colors,
                       const FrameCapture::ColorView* views) {
    if (!views) {
        return;
    }
    const bool first = entry.last_views.empty();
    for (size_t i = 0; i < colors.size(); ++i) {
        if (!colors[i].address) {
            continue;
        }
        const auto& v = views[i];
        const bool off_base = v.cb_address != colors[i].address || v.base_level || v.base_layer;
        const bool changed = !first && (i >= entry.last_views.size() || entry.last_views[i] != v);
        if ((first ? off_base : changed) && entry.target_draws.size() < kMaxTargetDrawLines) {
            entry.target_draws.push_back(std::format(
                "cb{} draw {}: addr={:#x} image={:#x} level={} layer={}", i, entry.draws + 1,
                v.cb_address, colors[i].address, v.base_level, v.base_layer));
        }
    }
    entry.last_views.assign(views, views + colors.size());
}

std::mutex display_mutex;
std::vector<VAddr> display_buffers;
// spartanport: read on every draw; the game registers a handful of display buffers once.
std::array<std::atomic<VAddr>, 16> display_list{};
std::atomic<u32> display_count{0};

Target ToTarget(const VideoCore::ImageInfo& info, u32 base_mip = 0, u32 view_mips = 0,
                u32 base_layer = 0, u32 view_layers = 0) {
    return {
        .address = info.guest_address,
        .format = info.pixel_format,
        .width = info.size.width,
        .height = info.size.height,
        .levels = u8(std::min<u32>(info.resources.levels, 255)),
        .layers = u8(std::min<u32>(info.resources.layers, 255)),
        .samples = u8(std::min<u32>(info.num_samples, 255)),
        .base_mip = u8(std::min<u32>(base_mip, 255)),
        .view_mips = u8(std::min<u32>(view_mips, 255)),
        .base_layer = u8(std::min<u32>(base_layer, 255)),
        .view_layers = u8(std::min<u32>(view_layers, 255)),
    };
}

std::string Describe(const Target& t) {
    if (!t.address) {
        return "-";
    }
    std::string out = std::format("{:#x} {} {}x{} m{} l{} s{}", t.address, vk::to_string(t.format),
                                  t.width, t.height, t.levels, t.layers, t.samples);
    if (t.view_mips > 0 && (t.base_mip != 0 || t.view_mips != t.levels || t.base_layer != 0 ||
                            t.view_layers != t.layers)) {
        out += std::format(" view[m{}+{} l{}+{}]", t.base_mip, t.view_mips, t.base_layer,
                           t.view_layers);
    }
    return out;
}

void AddSampledLocked(Entry& entry) {
    for (const auto& s : pending_sampled) {
        if (entry.sampled.size() < 32 &&
            std::ranges::find(entry.sampled, s) == entry.sampled.end()) {
            entry.sampled.push_back(s);
        }
    }
    pending_sampled.clear();
}

void AddShaderLocked(Entry& entry, u64 hash) {
    if (entry.shaders.size() < 8 && std::ranges::find(entry.shaders, hash) == entry.shaders.end()) {
        entry.shaders.push_back(hash);
    }
}

bool SameTargets(const std::vector<Target>& a, const std::vector<Target>& b) {
    if (a.size() != b.size()) {
        return false;
    }
    for (size_t i = 0; i < a.size(); ++i) {
        if (!a[i].SameTarget(b[i])) {
            return false;
        }
    }
    return true;
}

std::mutex result_mutex;
std::string result; ///< for the menu (FrameCapture::LastResult)
std::string requested_reason = "manual";
std::string active_reason = "manual";
std::string completed_notice;
std::deque<std::string> pending_screenshots;

void SetResult(std::string text, std::string notice = {}) {
    std::printf("Frame capture: %s\n", text.c_str());
    std::scoped_lock lk{result_mutex};
    result = std::move(text);
    if (!notice.empty()) {
        completed_notice = std::move(notice);
    }
}

std::string TakeActiveReason() {
    std::scoped_lock lk{result_mutex};
    return active_reason;
}

void SetActiveReason(std::string reason) {
    std::scoped_lock lk{result_mutex};
    active_reason = std::move(reason);
}

// SPARTAN_CAPTURE_DIR, else <user>/captures (SPARTAN_GPU_USER_DIR, the saves folder).
std::filesystem::path CaptureDir() {
    if (const char* dir = std::getenv("SPARTAN_CAPTURE_DIR"); dir && dir[0]) {
        return dir;
    }
    const char* user = std::getenv("SPARTAN_GPU_USER_DIR");
    return std::filesystem::path(user && user[0] ? user : "user") / "captures";
}

std::string PathToUtf8(const std::filesystem::path& p) {
    const auto u8 = p.u8string();
    return std::string(reinterpret_cast<const char*>(u8.data()), u8.size());
}

void WriteLocked(VAddr presented, bool timeout_flush, u64 flip_num) {
    const auto dir = CaptureDir();
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    const std::time_t now = std::time(nullptr);
    std::tm local{};
#ifdef _WIN32
    localtime_s(&local, &now);
#else
    localtime_r(&now, &local);
#endif
    char stamp[32];
    std::strftime(stamp, sizeof(stamp), "%Y%m%d-%H%M%S", &local);
    static u32 sequence = 0;
    const u32 seq = ++sequence;
    const auto txt_path = dir / std::format("frame_{}_{}.txt", stamp, seq);
    const auto png_path = dir / std::format("frame_{}_{}.png", stamp, seq);
    const std::string path_str = PathToUtf8(txt_path);
    const std::string png_str = PathToUtf8(png_path);

#ifdef _WIN32
    FILE* f = _wfopen(txt_path.wstring().c_str(), L"wb");
#else
    FILE* f = std::fopen(path_str.c_str(), "wb");
#endif
    if (!f) {
        std::printf("Frame capture: cannot write %s\n", path_str.c_str());
        SetResult("Capture failed: cannot write " + path_str);
        return;
    }
    const std::string reason = TakeActiveReason();
    std::fprintf(f, "capture_reason: %s%s\n", reason.c_str(),
                 timeout_flush ? " (4-flip timeout flush)" : "");
    std::fprintf(f, "flip: %llu\n", static_cast<unsigned long long>(flip_num));
    const std::string header = RenderTrace::FormatHeader();
    if (!header.empty()) {
        std::fwrite(header.data(), 1, header.size(), f);
    }
    std::fprintf(f, "presented buffer %#llx\n", static_cast<unsigned long long>(presented));
    u32 index = 0;
    for (const auto& e : entries) {
        if (e.compute) {
            std::fprintf(f, "\n#%u COMPUTE dispatches %u, cs", index++, e.draws);
        } else {
            std::fprintf(f, "\n#%u PASS draws %u (indices %llu), vs/ps", index++, e.draws,
                         static_cast<unsigned long long>(e.indices));
        }
        for (const u64 h : e.shaders) {
            std::fprintf(f, " %016llx", static_cast<unsigned long long>(h));
        }
        std::fprintf(f, "\n");
        for (size_t i = 0; i < e.colors.size(); ++i) {
            std::fprintf(f, "  color%zu %s\n", i, Describe(e.colors[i]).c_str());
        }
        for (const auto& line : e.target_draws) {
            std::fprintf(f, "  %s\n", line.c_str());
        }
        if (!e.compute) {
            std::fprintf(f, "  depth  %s\n", Describe(e.depth).c_str());
        }
        if (!e.state.empty()) {
            std::fprintf(f, "  %s\n", e.state.c_str());
        }
        if (!e.scene_note.empty()) {
            std::fprintf(f, "  scene: %s\n", e.scene_note.c_str());
        }
        for (const auto& vp : e.viewports) {
            std::fprintf(f, "  %s\n", vp.c_str());
        }
        for (const auto& [t, storage] : e.sampled) {
            std::fprintf(f, "  %s %s\n", storage ? "writes " : "samples", Describe(t).c_str());
        }
        if (!e.note.empty()) {
            std::fprintf(f, "  note: %s\n", e.note.c_str());
        }
        for (const auto& b : e.buffers) {
            std::fprintf(f, "%s\n", b.c_str());
        }
    }
    if (total_skips > 0) {
        std::fprintf(f, "\nskipped_total: %llu\n", static_cast<unsigned long long>(total_skips));
        for (const auto& sk : pending_skips) {
            std::fprintf(f, "  skip %s count=%u vs=%016llx ps=%016llx\n", sk.reason.c_str(),
                         sk.count, static_cast<unsigned long long>(sk.vs_hash),
                         static_cast<unsigned long long>(sk.ps_hash));
        }
    }
    std::fclose(f);

    {
        std::scoped_lock lk{result_mutex};
        if (pending_screenshots.size() >= 4) {
            pending_screenshots.pop_front();
        }
        pending_screenshots.push_back(png_str);
    }

    RenderTrace::OnFrameCaptureSaved(path_str, png_str, reason);
    std::printf("Frame capture: %zu passes written to %s (+ %s)\n", entries.size(),
                path_str.c_str(), png_str.c_str());
    const std::string fname = PathToUtf8(txt_path.filename());
    SetResult(std::format("Frame captured ({} passes): {}", entries.size(), path_str),
              std::format("Render trace saved: {} ({} passes)", fname, entries.size()));
}

void ResetCaptureBuffersLocked() {
    entries.clear();
    pending_sampled.clear();
    pending_buffers.clear();
    pending_skips.clear();
    total_skips = 0;
    pass_open = false;
}

} // namespace

void FrameCapture::OnFlip(VAddr presented_address) {
    last_presented.store(presented_address, std::memory_order_relaxed);
    const u64 flip_num = flips.fetch_add(1, std::memory_order_release) + 1;
    RenderTrace::OnFlip();

    static const char* trigger = std::getenv("SPARTAN_CAPTURE_TRIGGER");
    const u32 cur = state.load(std::memory_order_acquire);
    if (cur == Idle) {
        bool arm = false;
        std::string reason = "manual";
        if (requested.exchange(false, std::memory_order_acq_rel)) {
            arm = true;
            std::scoped_lock lk{result_mutex};
            reason = requested_reason;
        } else if (trigger && std::filesystem::exists(trigger)) {
            std::error_code ec;
            std::filesystem::remove(trigger, ec);
            arm = true;
            reason = "trigger_file";
        } else if (RenderTrace::ConsumeAutoCapture(reason)) {
            arm = true;
        }
        if (arm) {
            SetActiveReason(std::move(reason));
            armed_at_flip.store(flip_num, std::memory_order_release);
            state.store(Armed, std::memory_order_release);
            std::printf("Frame capture: armed (flip %llu)\n",
                        static_cast<unsigned long long>(flip_num));
        }
        return;
    }

    if (cur == Armed && flip_num - armed_at_flip.load(std::memory_order_acquire) >= 2) {
        std::scoped_lock lk{capture_mutex};
        if (state.load(std::memory_order_relaxed) == Armed) {
            ResetCaptureBuffersLocked();
            recording_at_flip.store(flip_num, std::memory_order_release);
            state.store(Recording, std::memory_order_release);
            std::printf("Frame capture: recording via flip fallback (flip %llu)\n",
                        static_cast<unsigned long long>(flip_num));
        }
        return;
    }

    if (cur == Recording && flip_num - recording_at_flip.load(std::memory_order_acquire) >= 4) {
        std::scoped_lock lk{capture_mutex};
        if (state.load(std::memory_order_relaxed) == Recording) {
            WriteLocked(presented_address, true, flip_num);
            ResetCaptureBuffersLocked();
            state.store(Idle, std::memory_order_release);
        }
    }
}

void FrameCapture::Request(const char* reason) {
    {
        std::scoped_lock lk{result_mutex};
        requested_reason = (reason && reason[0]) ? reason : "manual";
    }
    SetResult("Capturing the next frame...");
    requested.store(true, std::memory_order_release);
}

std::string FrameCapture::LastResult() {
    std::scoped_lock lk{result_mutex};
    return result;
}

std::string FrameCapture::ConsumeTraceScreenshotPath() {
    std::scoped_lock lk{result_mutex};
    if (pending_screenshots.empty()) {
        return {};
    }
    std::string out = std::move(pending_screenshots.front());
    pending_screenshots.pop_front();
    return out;
}

bool FrameCapture::ConsumeCompletedNotice(std::string& notice_out) {
    std::scoped_lock lk{result_mutex};
    if (completed_notice.empty()) {
        return false;
    }
    notice_out = std::move(completed_notice);
    completed_notice.clear();
    return true;
}

void FrameCapture::Poll() {}

bool FrameCapture::IsDisplayBuffer(VAddr address) {
    const u32 count = display_count.load(std::memory_order_acquire);
    for (u32 i = 0; i < count; ++i) {
        if (display_list[i].load(std::memory_order_relaxed) == address) {
            return true;
        }
    }
    if (count < display_list.size()) {
        return false;
    }
    std::scoped_lock lk{display_mutex};
    return std::ranges::find(display_buffers, address) != display_buffers.end();
}

void FrameCapture::AddDisplayBuffer(VAddr address) {
    std::scoped_lock lk{display_mutex};
    if (std::ranges::find(display_buffers, address) == display_buffers.end()) {
        display_buffers.push_back(address);
        const u32 count = display_count.load(std::memory_order_relaxed);
        if (count < display_list.size()) {
            display_list[count].store(address, std::memory_order_relaxed);
            display_count.store(count + 1, std::memory_order_release);
        }
    }
}

void FrameCapture::BeginPass(const VideoCore::ImageInfo* const* colors, u32 num_colors,
                             const VideoCore::ImageInfo* depth, std::string census,
                             std::string scene_note, const ColorView* views) {
    std::vector<Target> targets;
    bool display = false;
    {
        std::scoped_lock lk{display_mutex};
        for (u32 i = 0; i < num_colors; ++i) {
            targets.push_back(colors[i] ? ToTarget(*colors[i]) : Target{});
            display |= colors[i] && std::ranges::find(display_buffers, colors[i]->guest_address) !=
                                        display_buffers.end();
        }
    }
    // Frame boundaries in the command stream: the pass that writes a display buffer as its only
    // target. spartanport: SpartanPort also draws scene passes (two targets) into display buffers.
    display = display && targets.size() == 1;

    std::scoped_lock lk{capture_mutex};
    const bool new_pass = !(pass_open && !entries.empty() && !entries.back().compute &&
                            SameTargets(entries.back().colors, targets));
    if (display && new_pass) {
        if (state.load(std::memory_order_relaxed) == Recording) {
            WriteLocked(last_presented.load(std::memory_order_relaxed), false,
                        flips.load(std::memory_order_relaxed));
            ResetCaptureBuffersLocked();
            state.store(Idle, std::memory_order_release);
            return;
        }
        // Keep pending_sampled and pending_buffers from Pass #0's BindResources (which runs right
        // before BeginRendering / BeginPass).
        entries.clear();
        pending_skips.clear();
        total_skips = 0;
        pass_open = false;
        recording_at_flip.store(flips.load(std::memory_order_relaxed), std::memory_order_release);
        state.store(Recording, std::memory_order_release);
    }
    if (state.load(std::memory_order_relaxed) != Recording) {
        pending_sampled.clear();
        pending_buffers.clear();
        return;
    }
    const Target d = depth ? ToTarget(*depth) : Target{};
    if (pass_open && !entries.empty() && !entries.back().compute &&
        SameTargets(entries.back().colors, targets) && entries.back().depth.SameTarget(d)) {
        RecordViewsLocked(entries.back(), targets, views);
        return;
    }
    entries.push_back({.compute = false,
                       .colors = std::move(targets),
                       .depth = d,
                       .state = std::move(census),
                       .scene_note = std::move(scene_note)});
    RecordViewsLocked(entries.back(), entries.back().colors, views);
    pass_open = true;
}

void FrameCapture::Draw(u64 vs_hash, u64 ps_hash, u32 num_indices, u32 num_instances) {
    std::scoped_lock lk{capture_mutex};
    if (state.load(std::memory_order_relaxed) != Recording || entries.empty() ||
        entries.back().compute) {
        pending_sampled.clear();
        pending_buffers.clear();
        return;
    }
    auto& e = entries.back();
    ++e.draws;
    e.indices += u64(num_indices) * std::max(num_instances, 1u);
    AddShaderLocked(e, vs_hash);
    AddShaderLocked(e, ps_hash);
    AddSampledLocked(e);
    AddBuffersLocked(e);
}

void FrameCapture::Dispatch(u64 cs_hash, u32 x, u32 y, u32 z) {
    std::scoped_lock lk{capture_mutex};
    if (state.load(std::memory_order_relaxed) != Recording) {
        pending_sampled.clear();
        pending_buffers.clear();
        return;
    }
    if (!entries.empty() && entries.back().compute && !entries.back().shaders.empty() &&
        entries.back().shaders[0] == cs_hash) {
        ++entries.back().draws;
    } else {
        entries.push_back({.compute = true, .draws = 1, .shaders = {cs_hash}});
        entries.back().note = std::format("groups {}x{}x{}", x, y, z);
    }
    AddSampledLocked(entries.back());
    AddBuffersLocked(entries.back());
    pass_open = false;
}

void FrameCapture::Buffer(u64 stage_hash, u32 slot, VAddr address, const void* data, u64 size) {
    if (!data) {
        return;
    }
    const u64 bytes = std::min<u64>(size, 1024) & ~u64(3);
    std::string out = std::format("  buffer stage {:016x} slot {} at {:#x} size {}:", stage_hash,
                                  slot, address, size);
    const auto* words = static_cast<const float*>(data);
    for (u64 i = 0; i < bytes / 4; ++i) {
        if (i % 8 == 0) {
            out += std::format("\n    [{:3}]", i);
        }
        out += std::format(" {:12.6g}", words[i]);
    }
    std::scoped_lock lk{capture_mutex};
    pending_buffers.push_back(std::move(out));
}

void FrameCapture::Sampled(const VideoCore::ImageInfo& info, bool storage, u32 base_mip,
                           u32 view_mips, u32 base_layer, u32 view_layers) {
    std::scoped_lock lk{capture_mutex};
    if (pending_sampled.size() < 64) {
        pending_sampled.emplace_back(ToTarget(info, base_mip, view_mips, base_layer, view_layers),
                                     storage);
    }
}

void FrameCapture::Note(const char* text) {
    if (!text || !text[0]) {
        return;
    }
    std::scoped_lock lk{capture_mutex};
    if (!entries.empty()) {
        entries.back().note += text;
    }
}

void FrameCapture::Viewport(float x, float y, float w, float h, float min_z, float max_z, s32 sx,
                            s32 sy, u32 sw, u32 sh, float scale_x, float scale_y,
                            bool clip_disable) {
    std::scoped_lock lk{capture_mutex};
    if (state.load(std::memory_order_relaxed) != Recording || entries.empty() ||
        entries.back().compute) {
        return;
    }
    auto& vps = entries.back().viewports;
    if (vps.size() >= 3) {
        return;
    }
    std::string line =
        std::format("vp=({:.1f},{:.1f} {:.1f}x{:.1f} z={:.2f}..{:.2f}) sc=({},{},{}x{}) "
                    "scale=({:.3f},{:.3f}) clip_dis={}",
                    x, y, w, h, min_z, max_z, sx, sy, sw, sh, scale_x, scale_y, int(clip_disable));
    if (std::ranges::find(vps, line) == vps.end()) {
        vps.push_back(std::move(line));
    }
}

void FrameCapture::Skip(const char* reason, u64 vs_hash, u64 ps_hash) {
    if (!reason || !reason[0]) {
        return;
    }
    std::scoped_lock lk{capture_mutex};
    if (state.load(std::memory_order_relaxed) != Recording) {
        return;
    }
    ++total_skips;
    for (auto& sk : pending_skips) {
        if (sk.reason == reason && sk.vs_hash == vs_hash && sk.ps_hash == ps_hash) {
            ++sk.count;
            return;
        }
    }
    if (pending_skips.size() < 16) {
        pending_skips.push_back({reason, vs_hash, ps_hash, 1});
    }
}

} // namespace Vulkan
