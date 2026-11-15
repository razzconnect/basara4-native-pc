// SPDX-License-Identifier: GPL-2.0-or-later
// FindImage's overlap loop (texture_cache/overlap_resolution.h, the code FindImage runs) with
// ImageInfo's own MipOf/SliceOf (the cutscene double picture).
//
// Rule: the view FindImage returns is the request's location in the returned image, whatever
// order the page table lists the overlapping images in. Checked for every order of every set of
// stale images around a 3-level RGBA16F chain (requests for mip 1 and mip 2), for an array slice,
// and for an iteration that replaces the image (ExpandImage). The loop before the fix
// (legacy_view_*) is counted alongside: the orders it got wrong are the ones the render trace
// reports as tex.overlap_view_kept.
//
// No game or Vulkan device required. "--expect-fixed" (the command from before the fix) runs the
// same checks.
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <tuple>
#include <utility>
#include <vector>

#include "video_core/amdgpu/resource.h"
#include "video_core/texture_cache/image_info.h"
#include "video_core/texture_cache/overlap_resolution.h"

using namespace VideoCore;

namespace {

constexpr VAddr kChainAddress = 0x105f8c0000;

ImageInfo Make(VAddr address, u32 width, u32 height, u32 levels, vk::Format format, u32 bits,
               u32 pitch = 0, u32 layers = 1) {
    ImageInfo info{};
    info.guest_address = address;
    info.size = {width, height, 1};
    info.pitch = pitch ? pitch : width;
    info.pixel_format = format;
    info.num_bits = bits;
    info.type = AmdGpu::ImageType::Color2D;
    info.resources.levels = levels;
    info.resources.layers = layers;
    info.UpdateSize();
    return info;
}

// The branches of TextureCache::ResolveOverlap (texture_cache.cpp) these layouts reach, with their
// return values; the address geometry is ImageInfo's own MipOf/SliceOf. All images are Color2D,
// so its view type checks pass.
//  - equal address: other texel size ("allocation from a pool") or other 2D size (v1.0.7 size
//    check) -> {merged, -1, -1}; otherwise "use image view" -> {merged ? merged : cached, -1, -1}
//  - right overlap: {cached, mip, slice} when merged_info is inside it, else {none, -1, -1}
//  - left overlap: always ends {merged, -1, -1}
std::tuple<ImageId, int, int> ResolveLikeTextureCache(const std::vector<ImageInfo>& images,
                                                      const ImageInfo& request, ImageId cache_id,
                                                      ImageId merged_id) {
    const ImageInfo& merged_info = merged_id ? images[merged_id.index] : request;
    const ImageInfo& cached = images[cache_id.index];
    if (merged_info.guest_address == cached.guest_address) {
        if (merged_info.num_bits != cached.num_bits || merged_info.size != cached.size) {
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

// FindImage's locate: the request's own location in an image.
std::pair<s32, s32> Locate(const std::vector<ImageInfo>& images, const ImageInfo& request,
                           ImageId id) {
    const s32 mip = request.MipOf(images[id.index]);
    return {mip, mip >= 0 ? request.SliceOf(images[id.index], mip) : -1};
}

OverlapLoopResult Run(const std::vector<ImageInfo>& images, const ImageInfo& request,
                      const std::vector<ImageId>& order) {
    return RunOverlapLoop(
        std::span<const ImageId>{order.data(), order.size()},
        [&](ImageId cache_id, ImageId merged_id) {
            return ResolveLikeTextureCache(images, request, cache_id, merged_id);
        },
        [&](ImageId id) { return Locate(images, request, id); });
}

struct Tally {
    u32 orders = 0;
    u32 legacy_wrong = 0;
};

// Every order of every subset of `stale` together with `target`: the request must end at
// (target, mip, slice) every time, and tex.overlap_view_kept must fire exactly where the old
// loop ended elsewhere.
Tally CheckAllOrders(const std::vector<ImageInfo>& images, const ImageInfo& request,
                     ImageId target, const std::vector<ImageId>& stale, s32 mip, s32 slice) {
    Tally tally;
    const u32 subsets = 1u << stale.size();
    for (u32 mask = 0; mask < subsets; ++mask) {
        std::vector<ImageId> order{target};
        for (size_t i = 0; i < stale.size(); ++i) {
            if (mask & (1u << i)) {
                order.push_back(stale[i]);
            }
        }
        std::sort(order.begin(), order.end());
        do {
            const OverlapLoopResult r = Run(images, request, order);
            ++tally.orders;
            if (r.image_id != target || r.view_mip != mip || r.view_slice != slice) {
                std::fprintf(stderr, "order");
                for (const ImageId id : order) {
                    std::fprintf(stderr, " %u", id.index);
                }
                std::fprintf(stderr, ": image %u view (m%d s%d), want image %u view (m%d s%d)\n",
                             r.image_id.index, r.view_mip, r.view_slice, target.index, mip,
                             slice);
                assert(!"the view depends on the order of the overlapping images");
            }
            const bool legacy_right =
                r.legacy_view_mip == mip && r.legacy_view_slice == slice;
            tally.legacy_wrong += !legacy_right;
            assert(!r.LostView());
            assert(r.KeptView() == (!legacy_right && (mip > 0 || slice > 0)));
        } while (std::next_permutation(order.begin(), order.end()));
    }
    return tally;
}

void Report(const char* name, const Tally& t) {
    std::printf("%-44s %3u orders: all keep the location; the old loop lost it in %u\n", name,
                t.orders, t.legacy_wrong);
}

} // namespace

int main() {
    // The bloom/DoF chain (PASS ps a222fde5 at 1080p) and stale images left by pool reuse in its
    // address range. None is identical to a request, so FindImage's perfect match skips them and
    // the overlap loop runs.
    std::vector<ImageInfo> images;
    images.push_back(Make(kChainAddress, 960, 540, 3, vk::Format::eR16G16B16A16Sfloat, 64));
    const ImageInfo chain_info = images[0]; // a copy: images grows below
    const VAddr mip1 = kChainAddress + chain_info.mips_layout[1].offset;
    const VAddr mip2 = kChainAddress + chain_info.mips_layout[2].offset;
    images.push_back(Make(kChainAddress, 1920, 1080, 1, vk::Format::eR8G8B8A8Unorm, 32)); // 1
    images.push_back(Make(mip1, 480, 270, 1, vk::Format::eR8G8B8A8Unorm, 32));            // 2
    images.push_back(Make(mip1, 240, 135, 1, vk::Format::eR16G16B16A16Sfloat, 64));       // 3
    images.push_back(Make(mip2, 240, 135, 1, vk::Format::eR8G8B8A8Unorm, 32));            // 4
    const ImageId chain{0};
    const std::vector<ImageId> stale{ImageId{1}, ImageId{2}, ImageId{3}, ImageId{4}};

    // Render targets of the chain pass: mip 1 and mip 2.
    const ImageInfo request_mip1 = Make(mip1, 480, 270, 1, vk::Format::eR16G16B16A16Sfloat, 64,
                                        chain_info.mips_layout[1].pitch);
    const ImageInfo request_mip2 = Make(mip2, 240, 135, 1, vk::Format::eR16G16B16A16Sfloat, 64,
                                        chain_info.mips_layout[2].pitch);
    assert(request_mip1.MipOf(chain_info) == 1 && request_mip2.MipOf(chain_info) == 2);

    const Tally t1 = CheckAllOrders(images, request_mip1, chain, stale, 1, 0);
    Report("mip 1 target, chain + 0-4 stale images", t1);
    const Tally t2 = CheckAllOrders(images, request_mip2, chain, stale, 2, 0);
    Report("mip 2 target, chain + 0-4 stale images", t2);
    // The defect this fixes: the old loop got some orders wrong for both targets.
    assert(t1.legacy_wrong > 0 && t2.legacy_wrong > 0);

    // A level-0 request at the chain address keeps level 0 (nothing to keep).
    const ImageInfo request_level0 = Make(kChainAddress, 960, 540, 1,
                                          vk::Format::eR16G16B16A16Sfloat, 64,
                                          chain_info.mips_layout[0].pitch);
    {
        const auto r = Run(images, request_level0, {chain, ImageId{2}, ImageId{4}});
        assert(r.image_id == chain && r.view_mip <= 0 && r.view_slice <= 0 && !r.KeptView());
    }

    // Array slices: layer 2 of a 4-layer RGBA8 array, with a stale RGBA16F image at that layer.
    {
        std::vector<ImageInfo> arr;
        arr.push_back(Make(0x2000000000, 256, 256, 1, vk::Format::eR8G8B8A8Unorm, 32, 0, 4));
        const VAddr layer2 = 0x2000000000 + 2 * (arr[0].mips_layout[0].size / 4);
        arr.push_back(Make(layer2, 128, 128, 1, vk::Format::eR16G16B16A16Sfloat, 64));
        const ImageInfo request_layer2 = Make(layer2, 256, 256, 1, vk::Format::eR8G8B8A8Unorm, 32);
        const Tally t = CheckAllOrders(arr, request_layer2, ImageId{0}, {ImageId{1}}, 0, 2);
        Report("layer 2 target, array + stale image", t);
    }

    // An iteration that replaces the image (ExpandImage: a new image with the same layout as the
    // merged one, returned with no location): the request is located in the new image.
    {
        std::vector<ImageInfo> with_expand = images;
        with_expand.push_back(chain_info); // 5: the expanded copy of the chain
        const std::vector<ImageId> order{chain, ImageId{1}};
        const auto r = RunOverlapLoop(
            std::span<const ImageId>{order.data(), order.size()},
            [&](ImageId cache_id, ImageId merged_id) -> std::tuple<ImageId, int, int> {
                if (merged_id == chain && cache_id == ImageId{1}) {
                    return {ImageId{5}, -1, -1};
                }
                return ResolveLikeTextureCache(with_expand, request_mip1, cache_id, merged_id);
            },
            [&](ImageId id) { return Locate(with_expand, request_mip1, id); });
        assert(r.image_id == ImageId{5} && r.view_mip == 1 && r.view_slice == 0);
        assert(r.legacy_view_mip == -1 && r.KeptView() && !r.LostView());
        std::printf("%-44s image replaced (ExpandImage): mip 1 located in the new image\n",
                    "mip 1 target, chain expanded");
    }

    // A request that is not inside the image the loop ends with keeps what the loop returned.
    {
        const std::vector<ImageId> order{ImageId{2}};
        const auto r = Run(images, request_mip2, order);
        assert(!r.image_id && r.view_mip == -1 && !r.KeptView() && !r.LostView());
    }

    std::puts("overlap-loop-test ok");
    return 0;
}
