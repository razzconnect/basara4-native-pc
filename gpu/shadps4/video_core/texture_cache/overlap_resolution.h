// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC: TextureCache::FindImage's overlap resolution loop, moved here unchanged so the
// subresource location it ends with can be compared with what its iterations found (render trace
// tex.overlap_view_lost) and tested without a GPU (tests/test_overlap_loop.cpp).
#pragma once

#include <span>
#include <tuple>

#include "common/types.h"
#include "video_core/texture_cache/types.h"

namespace VideoCore {

struct OverlapLoopResult {
    ImageId image_id{};
    s32 view_mip = -1;
    s32 view_slice = -1;

    // The last subresource location (mip or slice above 0) an iteration found, and the cached
    // image whose iteration came after it and replaced it. Observation only.
    s32 found_mip = -1;
    s32 found_slice = -1;
    ImageId found_in{};
    ImageId replaced_by{};

    /// An iteration placed the request at mip/slice > 0 of an image, and the loop ended at base
    /// level/layer 0: the request would be drawn or sampled at level 0 instead.
    bool LostView() const {
        return (found_mip > 0 || found_slice > 0) && view_mip <= 0 && view_slice <= 0;
    }
};

/// resolve(cache_id, merged_image_id) -> {image id, view mip, view slice}, as
/// TextureCache::ResolveOverlap. Iterates exactly as FindImage always did: the view is reset at
/// the start of every iteration and replaced only when the iteration returns an image.
template <typename Resolve>
OverlapLoopResult RunOverlapLoop(std::span<const ImageId> image_ids, Resolve&& resolve) {
    OverlapLoopResult r;
    for (const ImageId cache_id : image_ids) {
        const bool had_location = r.view_mip > 0 || r.view_slice > 0;
        r.view_mip = -1;
        r.view_slice = -1;

        const auto [overlap_image_id, overlap_view_mip, overlap_view_slice] =
            resolve(cache_id, r.image_id);
        if (overlap_image_id) {
            r.image_id = overlap_image_id;
            r.view_mip = overlap_view_mip;
            r.view_slice = overlap_view_slice;
        }

        if (r.view_mip > 0 || r.view_slice > 0) {
            r.found_mip = r.view_mip;
            r.found_slice = r.view_slice;
            r.found_in = r.image_id;
            r.replaced_by = {};
        } else if (had_location) {
            r.replaced_by = cache_id;
        }
    }
    return r;
}

} // namespace VideoCore
