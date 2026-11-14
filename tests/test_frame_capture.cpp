// SPDX-License-Identifier: GPL-2.0-or-later
// The frame analyzer & render trace capture (vk_frame_capture.cpp):
// - Null constant-ring buffer safety (data=null must not dereference).
// - Pass #0 Sampled() and Buffer() preservation across Armed -> Recording.
// - View mip/layer, viewport/scissor, scene note, and skipped-draw serialization.
// - Paired PNG path queue and overlay notice generation.
// - 4-flip timeout flush when no closing display pass occurs.
// No game or Vulkan device required.
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "video_core/renderer_vulkan/vk_frame_capture.h"
#include "video_core/texture_cache/image_info.h"

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
} // namespace

int main() {
#ifdef _WIN32
    _set_error_mode(_OUT_TO_STDERR);
#endif
    // 1. Null-data safety check (issue #174).
    std::array<float, 16> words{};
    words[0] = 1.5f;
    Vulkan::FrameCapture::Buffer(0x1234, 0, 0x1000, words.data(), sizeof(words));
    Vulkan::FrameCapture::Buffer(0x1234, 1, 0, nullptr, 64);

    const auto tmp_dir = std::filesystem::temp_directory_path() / "basara4_frame_capture_test";
    std::error_code ec;
    std::filesystem::remove_all(tmp_dir, ec);
    std::filesystem::create_directories(tmp_dir, ec);
    const auto dir_u8 = tmp_dir.u8string();
    const std::string dir_str(reinterpret_cast<const char*>(dir_u8.data()), dir_u8.size());
    SetEnv("SPARTAN_CAPTURE_DIR", dir_str.c_str());

    // 2. Display buffer registration & Pass #0 preservation.
    constexpr VAddr kDisplayAddr = 0xABC0000;
    Vulkan::FrameCapture::AddDisplayBuffer(kDisplayAddr);
    assert(Vulkan::FrameCapture::IsDisplayBuffer(kDisplayAddr));

    VideoCore::ImageInfo display_target{};
    display_target.guest_address = kDisplayAddr;
    display_target.pixel_format = vk::Format::eB8G8R8A8Unorm;
    display_target.size = {1920, 1080, 1};
    display_target.resources = {1, 1};
    display_target.num_samples = 1;

    VideoCore::ImageInfo depth_target{};
    depth_target.guest_address = 0xDEF0000;
    depth_target.pixel_format = vk::Format::eD32SfloatS8Uint;
    depth_target.size = {1920, 1080, 1};
    depth_target.resources = {1, 1};
    depth_target.num_samples = 1;

    VideoCore::ImageInfo sampled_tex{};
    sampled_tex.guest_address = 0x5550000;
    sampled_tex.pixel_format = vk::Format::eR16G16B16A16Sfloat;
    sampled_tex.size = {960, 540, 1};
    sampled_tex.resources = {3, 1};
    sampled_tex.num_samples = 1;

    Vulkan::FrameCapture::Request("unit_test");
    assert(Vulkan::FrameCapture::LastResult() == "Capturing the next frame...");
    Vulkan::FrameCapture::OnFlip(kDisplayAddr);
    assert(Vulkan::FrameCapture::Active());
    assert(!Vulkan::FrameCapture::IsRecording());

    // Simulate BindResources running BEFORE Pass #0 BeginPass (Armed -> Recording transition).
    Vulkan::FrameCapture::Buffer(0xAAAA, 0, 0x2000, words.data(), sizeof(words));
    Vulkan::FrameCapture::Sampled(sampled_tex, false, 1, 2, 0, 1);

    const VideoCore::ImageInfo* colors0[] = {&display_target};
    Vulkan::FrameCapture::BeginPass(colors0, 1, &depth_target, "state samples c1 d1 r1 equal",
                                    "started=1 reduced=0 scene=1920x1080 out=1920x1080");
    assert(Vulkan::FrameCapture::IsRecording());
    Vulkan::FrameCapture::Viewport(0.f, 0.f, 1920.f, 1080.f, 0.f, 1.f, 0, 0, 1920, 1080, 1.f, 1.f,
                                   false);
    Vulkan::FrameCapture::Draw(0x1111, 0x2222, 6, 1);
    Vulkan::FrameCapture::Skip("NullGraphicsPipeline", 0x3333, 0x4444);

    // A mip chain pass (one draw per level, all on the same image): each draw's CB address and
    // view level is recorded when it leaves the image base or changes. Draw 2 binds the mip 1
    // address but level 0 (the double picture), draw 3 the mip 2 address at level 2.
    VideoCore::ImageInfo chain = sampled_tex;
    chain.guest_address = 0x105f8c0000;
    const VideoCore::ImageInfo* chain_colors[] = {&chain};
    using View = Vulkan::FrameCapture::ColorView;
    const View level0[] = {{.cb_address = 0x105f8c0000, .base_level = 0, .base_layer = 0}};
    const View mip1_at_level0[] = {{.cb_address = 0x105f9c0000, .base_level = 0, .base_layer = 0}};
    const View mip2[] = {{.cb_address = 0x105fa00000, .base_level = 2, .base_layer = 0}};
    Vulkan::FrameCapture::BeginPass(chain_colors, 1, nullptr, "state chain", {}, level0);
    Vulkan::FrameCapture::Draw(0x5555, 0xa222fde5, 3, 1);
    Vulkan::FrameCapture::BeginPass(chain_colors, 1, nullptr, "state chain", {}, mip1_at_level0);
    Vulkan::FrameCapture::Draw(0x5555, 0xa222fde5, 3, 1);
    Vulkan::FrameCapture::BeginPass(chain_colors, 1, nullptr, "state chain", {}, mip1_at_level0);
    Vulkan::FrameCapture::Draw(0x5555, 0xa222fde5, 3, 1); // same view as the draw before: no line
    Vulkan::FrameCapture::BeginPass(chain_colors, 1, nullptr, "state chain", {}, mip2);
    Vulkan::FrameCapture::Draw(0x5555, 0xa222fde5, 3, 1);

    // Next frame's display pass closes the capture.
    VideoCore::ImageInfo other_display = display_target;
    other_display.guest_address = 0xABC1000;
    Vulkan::FrameCapture::AddDisplayBuffer(other_display.guest_address);
    const VideoCore::ImageInfo* colors1[] = {&other_display};
    Vulkan::FrameCapture::BeginPass(colors1, 1, nullptr, "state next");
    assert(!Vulkan::FrameCapture::Active());

    const std::string png1 = Vulkan::FrameCapture::ConsumeTraceScreenshotPath();
    assert(!png1.empty());
    assert(Vulkan::FrameCapture::ConsumeTraceScreenshotPath().empty());
    std::string notice;
    assert(Vulkan::FrameCapture::ConsumeCompletedNotice(notice));
    assert(notice.find("Render trace saved:") != std::string::npos);

    std::vector<std::filesystem::path> txts;
    for (const auto& entry : std::filesystem::directory_iterator(tmp_dir)) {
        if (entry.path().extension() == ".txt") {
            txts.push_back(entry.path());
        }
    }
    assert(txts.size() == 1);
    const std::string cap1 = ReadAll(txts[0]);
    assert(cap1.find("capture_reason: unit_test\n") != std::string::npos);
    assert(cap1.find("scene: started=1 reduced=0") != std::string::npos);
    assert(cap1.find("vp=(0.0,0.0 1920.0x1080.0") != std::string::npos);
    assert(cap1.find("samples 0x5550000 R16G16B16A16Sfloat 960x540 m3 l1 s1 view[m1+2 l0+1]") !=
           std::string::npos);
    assert(cap1.find("buffer stage 000000000000aaaa slot 0 at 0x2000") != std::string::npos);
    assert(cap1.find("skipped_total: 1") != std::string::npos);
    assert(cap1.find("skip NullGraphicsPipeline count=1 vs=0000000000003333 ps=0000000000004444") !=
           std::string::npos);
    // Per draw target views: the first draw at the image base adds no line; later changes do.
    assert(cap1.find("PASS draws 4") != std::string::npos);
    assert(cap1.find("cb0 draw 1:") == std::string::npos);
    assert(cap1.find("  cb0 draw 2: addr=0x105f9c0000 image=0x105f8c0000 level=0 layer=0\n") !=
           std::string::npos);
    assert(cap1.find("cb0 draw 3:") == std::string::npos);
    assert(cap1.find("  cb0 draw 4: addr=0x105fa00000 image=0x105f8c0000 level=2 layer=0\n") !=
           std::string::npos);
    // Passes without views (Pass #0 here) add no lines.
    assert(cap1.find("cb0 draw", cap1.find("capture_reason")) ==
           cap1.find("  cb0 draw 2:") + 2);

    // 3. 4-flip timeout flush when no display pass ever triggers or closes capture.
    Vulkan::FrameCapture::Request("timeout_test");
    bool dispatched = false;
    for (int f = 0; f < 10 && (f == 0 || Vulkan::FrameCapture::Active()); ++f) {
        Vulkan::FrameCapture::OnFlip(0);
        if (Vulkan::FrameCapture::IsRecording() && !dispatched) {
            Vulkan::FrameCapture::Dispatch(0x9999, 8, 8, 1);
            dispatched = true;
        }
    }
    assert(dispatched);
    assert(!Vulkan::FrameCapture::Active());
    const std::string png2 = Vulkan::FrameCapture::ConsumeTraceScreenshotPath();
    assert(!png2.empty());

    txts.clear();
    for (const auto& entry : std::filesystem::directory_iterator(tmp_dir)) {
        if (entry.path().extension() == ".txt") {
            txts.push_back(entry.path());
        }
    }
    assert(txts.size() == 2);
    bool found_timeout = false;
    for (const auto& p : txts) {
        const std::string txt = ReadAll(p);
        if (txt.find("capture_reason: timeout_test (4-flip timeout flush)") != std::string::npos) {
            assert(txt.find("COMPUTE dispatches 1, cs 0000000000009999") != std::string::npos);
            found_timeout = true;
        }
    }
    assert(found_timeout);

    std::filesystem::remove_all(tmp_dir, ec);
    std::puts("frame-capture-test ok");
    return 0;
}
