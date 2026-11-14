// SPDX-License-Identifier: GPL-2.0-or-later
// Symbols image_info.cpp references only from its constructors that read guest descriptors
// (color/depth buffers, T#s, video out buffers). tests/test_overlap_loop.cpp builds ImageInfo by
// hand and never reaches them: each aborts if called, so the test cannot pass on a stub.
#include <cstdio>
#include <cstdlib>

#include "core/libraries/kernel/process.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"

namespace {
[[noreturn]] void NotInThisTest(const char* name) {
    std::fprintf(stderr, "overlap-loop-test: %s called; the test only uses hand-built ImageInfo\n",
                 name);
    std::abort();
}
} // namespace

namespace Libraries::Kernel {
s32 PS4_SYSV_ABI sceKernelIsNeoMode() {
    NotInThisTest("sceKernelIsNeoMode");
}
} // namespace Libraries::Kernel

namespace Vulkan::LiverpoolToVK {
vk::Format SurfaceFormat(AmdGpu::DataFormat, AmdGpu::NumberFormat) {
    NotInThisTest("LiverpoolToVK::SurfaceFormat");
}
vk::Format DepthFormat(AmdGpu::DepthBuffer::ZFormat, AmdGpu::DepthBuffer::StencilFormat) {
    NotInThisTest("LiverpoolToVK::DepthFormat");
}
} // namespace Vulkan::LiverpoolToVK
