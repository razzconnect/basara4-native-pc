// SPDX-License-Identifier: GPL-2.0-or-later
// FindImage's overlap loop (texture_cache/overlap_resolution.h, the code FindImage runs) with
// ImageInfo's own MipOf/SliceOf. Ticket B4-20261114-texcache-regresi, cutscene double picture:
// a render target for mip 1 of a 3-level RGBA16F chain, with a stale image left at the mip 1
// address. No game or Vulkan device required.
//
//   overlap-loop-test                 the loop as it is today, and the tex.overlap_view_lost
//                                     observation of it: passes.
//   overlap-loop-test --expect-fixed  what the loop should do (mip 1 kept in every order):
//                                     EXPECTED TO FAIL until the loop is fixed.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <tuple>
#include <vector>

#include "video_core/amdgpu/resource.h"
#include "video_core/texture_cache/image_info.h"
#include "video_core/texture_cache/overlap_resolution.h"

using namespace VideoCore;

namespace {

constexpr VAddr kChainAddress = 0x105f8c0000;

ImageInfo Make(VAddr address, u32 width, u32 height, u32 levels, vk::Format format, u32 bits,
               u32 pitch = 0) {
    ImageInfo info{};
    info.guest_address = address;
    info.size = {width, height, 1};
    info.pitch = pitch ? pitch : width;
    info.pixel_format = format;
    info.num_bits = bits;
    info.type = AmdGpu::ImageType::Color2D;
    info.resources.levels = levels;
    info.resources.layers = 1;
    info.UpdateSize();
    return info;
}

// The branches of TextureCache::ResolveOverlap (texture_cache.cpp) that this layout reaches, with
// their return values; the address geometry is ImageInfo's own MipOf/SliceOf.
//  - equal address, other texel size: "allocation from a pool" -> {merged, -1, -1}
//  - right overlap, request inside the cached image: {cached, mip, slice}; else {none, -1, -1}
//  - left overlap, cached image inside the request: always ends {merged, -1, -1}
std::tuple<ImageId, int, int> ResolveLikeTextureCache(const std::vector<ImageInfo>& images,
                                                      const ImageInfo& request, ImageId cache_id,
                                                      ImageId merged_id) {
    const ImageInfo& merged_info = merged_id ? images[merged_id.index] : request;
    const ImageInfo& cached = images[cache_id.index];
    if (merged_info.guest_address == cached.guest_address) {
        if (merged_info.num_bits != cached.num_bits) {
            return {merged_id, -1, -1};
        }
        return {merged_id ? merged_id : cache_id, -1, -1};
    }
    if (merged_info.guest_address > cached.guest_address) {
        if (const s32 mip = merged_info.MipOf(cached); mip >= 0) {
            if (const s32 slice = merged_info.SliceOf(cached, mip); slice >= 0) {
                return {cache_id, mip, slice};
            }
        }
        return {ImageId{}, -1, -1};
    }
    return {merged_id, -1, -1};
}

OverlapLoopResult Run(const std::vector<ImageInfo>& images, const ImageInfo& request,
                      std::vector<ImageId> order) {
    return RunOverlapLoop(std::span<const ImageId>{order.data(), order.size()},
                          [&](ImageId cache_id, ImageId merged_id) {
                              return ResolveLikeTextureCache(images, request, cache_id, merged_id);
                          });
}

void Print(const char* name, const OverlapLoopResult& r) {
    std::printf("%-26s image=%u view=(m%d s%d) found=(m%d s%d in %u) replaced_by=%u lost=%d\n",
                name, r.image_id.index, r.view_mip, r.view_slice, r.found_mip, r.found_slice,
                r.found_in.index, r.replaced_by.index, int(r.LostView()));
}

} // namespace

int main(int argc, char** argv) {
    const bool expect_fixed = argc == 2 && !std::strcmp(argv[1], "--expect-fixed");

    // id 0: the bloom/DoF chain, RGBA16F 960x540, 3 levels (PASS ps a222fde5 at 1080p).
    // id 1: a stale 1-level image left at the mip 1 address by pool reuse (other texel size, so
    //       FindImage's perfect match skips it and the overlap loop runs).
    std::vector<ImageInfo> images;
    images.push_back(Make(kChainAddress, 960, 540, 3, vk::Format::eR16G16B16A16Sfloat, 64));
    const VAddr mip1_address = kChainAddress + images[0].mips_layout[1].offset;
    images.push_back(Make(mip1_address, 480, 270, 1, vk::Format::eR8G8B8A8Unorm, 32));
    // The render target the second draw binds: mip 1 of the chain.
    const ImageInfo request = Make(mip1_address, 480, 270, 1, vk::Format::eR16G16B16A16Sfloat, 64,
                                   images[0].mips_layout[1].pitch);
    assert(request.MipOf(images[0]) == 1);

    const ImageId chain{0}, stale{1};
    const auto only_chain = Run(images, request, {chain});
    const auto stale_first = Run(images, request, {stale, chain});
    const auto chain_first = Run(images, request, {chain, stale});
    Print("only chain", only_chain);
    Print("stale image, then chain", stale_first);
    Print("chain, then stale image", chain_first);

    if (expect_fixed) {
        // The request is mip 1 of the chain whatever order the page table lists the images in.
        assert(only_chain.image_id == chain && only_chain.view_mip == 1);
        assert(stale_first.image_id == chain && stale_first.view_mip == 1);
        assert(chain_first.image_id == chain && chain_first.view_mip == 1 &&
               "mip 1 lost: the target is drawn into level 0 (the double picture)");
        std::puts("overlap-loop-test --expect-fixed ok");
        return 0;
    }

    // Today's loop: correct with the chain alone or listed last ...
    assert(only_chain.image_id == chain && only_chain.view_mip == 1 && !only_chain.LostView());
    assert(stale_first.image_id == chain && stale_first.view_mip == 1 &&
           !stale_first.LostView());
    // ... but the chain listed first ends at level 0 of the chain, and the observation names it.
    assert(chain_first.image_id == chain && chain_first.view_mip == -1);
    assert(chain_first.LostView());
    assert(chain_first.found_mip == 1 && chain_first.found_slice == 0);
    assert(chain_first.found_in == chain && chain_first.replaced_by == stale);
    std::puts("overlap-loop-test ok (defect reproduced: chain then stale image loses mip 1)");
    return 0;
}
