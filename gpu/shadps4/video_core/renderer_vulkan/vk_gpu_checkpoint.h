// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC (Razz): find the draw that kills the GPU.
//
// When the NVIDIA driver loses the device (Windows event nvlddmkm 153) the game used to wait forever on
// a semaphore at 100% CPU, with nothing in the log. VK_NV_device_diagnostic_checkpoints lets the GPU
// remember the last marker it passed in each queue. Every draw and dispatch sets a marker whose value is an
// id into a small ring of descriptions (shader hashes, counts); on VK_ERROR_DEVICE_LOST the markers the GPU
// reached are read back and written to the log.

#pragma once

#include <array>
#include <chrono>
#include <ctime>
#include <string>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <vector>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/renderer_vulkan/vk_render_trace.h"

namespace Vulkan::GpuCheckpoint {

struct Img {
    u64 addr{};
    u32 fmt{};
    u16 width{}, height{};
    u8 levels{}, layers{}, flags{}; ///< flags: 1 depth, 2 render target, 4 forced general
};

struct Entry {
    std::atomic<u64> id{0};
    char kind[12]{};
    u64 a{}, b{}, c{}, d{};
    Img imgs[6]{};
    u8 num_imgs{};
};

inline constexpr u32 RingSize = 1 << 14;

inline std::array<Entry, RingSize>& Ring() {
    static std::array<Entry, RingSize> ring;
    return ring;
}

inline std::atomic<bool>& EnabledFlag() {
    static std::atomic<bool> enabled{false};
    return enabled;
}
inline std::atomic<u64>& Counter() {
    static std::atomic<u64> counter{0};
    return counter;
}

[[nodiscard]] inline bool Enabled() {
    return EnabledFlag().load(std::memory_order_relaxed);
}

/// Wall-clock time of day, so a GPU warning can be matched to what the audio log says was playing.
inline std::string WallClock() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t seconds = std::chrono::system_clock::to_time_t(now);
    std::tm local{};
    localtime_s(&local, &seconds);
    char text[16];
    std::strftime(text, sizeof(text), "%H:%M:%S", &local);
    return text;
}

/// Records a description and returns the marker for it (0 when checkpoints are unavailable).
inline u64 Note(const char* kind, u64 a = 0, u64 b = 0, u64 c = 0, u64 d = 0) {
    if (!Enabled()) {
        return 0;
    }
    const u64 id = Counter().fetch_add(1, std::memory_order_relaxed) + 1;
    auto& e = Ring()[id % RingSize];
    e.id.store(0, std::memory_order_relaxed);
    std::snprintf(e.kind, sizeof(e.kind), "%s", kind);
    e.a = a;
    e.b = b;
    e.c = c;
    e.d = d;
    e.num_imgs = 0;
    e.id.store(id, std::memory_order_release);
    return id;
}

/// The images (textures, storage images, render targets) the draw with this marker binds.
inline void AttachImage(u64 id, u64 addr, u32 fmt, u32 width, u32 height, u32 levels, u32 layers, u32 flags) {
    if (!id) {
        return;
    }
    auto& e = Ring()[id % RingSize];
    if (e.id.load(std::memory_order_relaxed) != id || e.num_imgs >= 6) {
        return;
    }
    e.imgs[e.num_imgs++] = Img{addr, fmt, static_cast<u16>(width), static_cast<u16>(height), static_cast<u8>(levels),
                               static_cast<u8>(layers), static_cast<u8>(flags)};
}

inline void PrintImages(const Entry& e) {
    for (u32 i = 0; i < e.num_imgs; ++i) {
        const auto& m = e.imgs[i];
        std::printf("        image %#llx %s %ux%u levels %u layers %u%s%s%s\n", static_cast<unsigned long long>(m.addr),
                    vk::to_string(static_cast<vk::Format>(m.fmt)).c_str(), m.width, m.height, m.levels, m.layers,
                    (m.flags & 1) ? " depth" : "", (m.flags & 2) ? " target" : "", (m.flags & 4) ? " general" : "");
    }
}

inline void Mark(vk::CommandBuffer cmdbuf, u64 id) {
    if (id) {
        cmdbuf.setCheckpointNV(reinterpret_cast<const void*>(static_cast<uintptr_t>(id)));
    }
}

/// Logs where the GPU stopped.  Call once the device is known to be lost.
inline void ReportLost(vk::Queue queue, const char* where) {
    static std::atomic<bool> reported{false};
    if (reported.exchange(true)) {
        return;
    }
    std::fprintf(stderr, "GPU: DEVICE LOST in %s (Windows event nvlddmkm 153 / a driver-reported GPU error)\n", where);
    std::fprintf(stdout, "GPU: DEVICE LOST in %s (Windows event nvlddmkm 153 / a driver-reported GPU error)\n", where);
    RenderTrace::RecordAnomaly(RenderTrace::Anomaly::DeviceLost, where, false);
    RenderTrace::WriteSessionSummary();
    if (!Enabled()) {
        std::printf("GPU: checkpoints were not available, the faulting draw is unknown\n");
        std::fflush(stdout);
        return;
    }
    const auto data = queue.getCheckpointDataNV();
    std::printf("GPU: %zu checkpoint(s) read back from the queue; last draws/dispatches issued before it, newest id last: ", data.size());
    std::printf("\n");
    for (const auto& cp : data) {
        const u64 id = static_cast<u64>(reinterpret_cast<uintptr_t>(cp.pCheckpointMarker));
        const auto& e = Ring()[id % RingSize];
        if (e.id.load(std::memory_order_acquire) == id) {
            std::printf("  stage %s: #%llu %s a=%#llx b=%#llx c=%llu d=%llu\n", vk::to_string(cp.stage).c_str(),
                        static_cast<unsigned long long>(id), e.kind, static_cast<unsigned long long>(e.a),
                        static_cast<unsigned long long>(e.b), static_cast<unsigned long long>(e.c),
                        static_cast<unsigned long long>(e.d));
            PrintImages(e);
        } else {
            std::printf("  stage %s: #%llu (description already overwritten)\n", vk::to_string(cp.stage).c_str(),
                        static_cast<unsigned long long>(id));
        }
    }
    const u64 last = Counter().load();
    std::printf("GPU: the newest 12 descriptions (most recent last):\n");
    for (u64 id = last > 12 ? last - 11 : 1; id <= last; ++id) {
        const auto& e = Ring()[id % RingSize];
        if (e.id.load(std::memory_order_acquire) == id) {
            std::printf("  #%llu %s a=%#llx b=%#llx c=%llu d=%llu\n", static_cast<unsigned long long>(id), e.kind,
                        static_cast<unsigned long long>(e.a), static_cast<unsigned long long>(e.b),
                        static_cast<unsigned long long>(e.c), static_cast<unsigned long long>(e.d));
            PrintImages(e);
        }
    }
    std::fflush(stdout);
}

} // namespace Vulkan::GpuCheckpoint
