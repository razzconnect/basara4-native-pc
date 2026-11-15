// SPDX-License-Identifier: GPL-2.0-or-later
// spartanport: frame analyzer & render trace capture. Creating the file named by
// SPARTAN_CAPTURE_TRIGGER, pressing F11 (when enabled), clicking "Capture render trace" in the
// Insert menu, or hitting a RenderTrace auto-trigger records the next full frame: every render
// pass (targets, depth, draw count, shaders, sampled textures, viewports, scene proxy state),
// compute dispatch, skipped draws, and a paired pre-overlay PNG screenshot.

#pragma once

#include <atomic>
#include <cstdio>
#include <string>
#include <vector>

#include "common/types.h"

namespace VideoCore {
struct ImageInfo;
}

namespace Vulkan {

class FrameCapture {
public:
    /// VideoOut thread, per flip: frame boundary, presented buffer, trigger & timeout checks.
    static void OnFlip(VAddr presented_address);

    /// VideoOut: a display buffer the game registered. A pass drawing into one starts a frame
    /// in the GPU command stream (the flip itself runs a frame behind).
    static void AddDisplayBuffer(VAddr address);
    static bool IsDisplayBuffer(VAddr address);

    /// GPU thread: whether passes are being looked at (armed or recording; cheap).
    static bool Active() {
        return state.load(std::memory_order_relaxed) != Idle;
    }
    /// GPU thread: whether a frame is being recorded right now.
    static bool IsRecording() {
        return state.load(std::memory_order_relaxed) == Recording;
    }
    /// GPU thread, at every draw/dispatch: starts or ends a recording at frame boundaries.
    static void Poll();

    /// Where a color attachment of a draw points: the guest CB address and the view's base
    /// level/layer in the image the texture cache found for it (Basara 4 PC: shows a mip
    /// target drawn into level 0).
    struct ColorView {
        VAddr cb_address{};
        u32 base_level{};
        u32 base_layer{};
        bool operator==(const ColorView&) const = default;
    };

    /// `state`: the pass's census line (vk_pass_census.h), kept with the first pipeline that
    /// opened the pass. `scene_note`: optional SceneTargets / upscaler info for the new pass.
    /// `views` (num_colors entries, optional): recorded per draw as "cbN draw K" lines whenever
    /// they leave the image base or change within the pass.
    static void BeginPass(const VideoCore::ImageInfo* const* colors, u32 num_colors,
                          const VideoCore::ImageInfo* depth, std::string census,
                          std::string scene_note = {}, const ColorView* views = nullptr);
    static void Draw(u64 vs_hash, u64 ps_hash, u32 num_indices, u32 num_instances);
    static void Dispatch(u64 cs_hash, u32 x, u32 y, u32 z);
    static void Sampled(const VideoCore::ImageInfo& info, bool storage, u32 base_mip = 0,
                        u32 view_mips = 0, u32 base_layer = 0, u32 view_layers = 0);
    /// Contents of a bound buffer (first 1 KiB), kept for small passes and a pass's first draw.
    static void Buffer(u64 stage_hash, u32 slot, VAddr address, const void* data, u64 size);
    static void Note(const char* text);
    static void Viewport(float x, float y, float w, float h, float min_z, float max_z, s32 sx,
                         s32 sy, u32 sw, u32 sh, float scale_x, float scale_y, bool clip_disable);
    static void Skip(const char* reason, u64 vs_hash = 0, u64 ps_hash = 0);

    /// In-game menu / hotkey / auto trigger: records the next full frame.
    static void Request(const char* reason = "manual");
    /// What the last request did, for the menu: empty, "Capturing...", the file or an error.
    static std::string LastResult();

    /// Consumes a queued paired PNG path for Presenter::Present (empty if none pending).
    static std::string ConsumeTraceScreenshotPath();
    /// Consumes a one-shot overlay notice when a trace finishes writing.
    static bool ConsumeCompletedNotice(std::string& notice_out);

private:
    enum : u32 { Idle, Armed, Recording };
    static inline std::atomic<u32> state{Idle};
    static inline std::atomic<u64> flips{0};
    static inline std::atomic<u64> armed_at_flip{0};
    static inline std::atomic<u64> recording_at_flip{0};
    static inline std::atomic<VAddr> last_presented{0};
    static inline std::atomic<bool> requested{false};
};

} // namespace Vulkan
