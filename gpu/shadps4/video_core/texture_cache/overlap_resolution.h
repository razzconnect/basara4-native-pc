// SPDX-License-Identifier: GPL-2.0-or-later
// Basara 4 PC: TextureCache::FindImage's overlap resolution loop (tests/test_overlap_loop.cpp).
//
// Rule (cutscene double picture): the view FindImage returns
// describes where the request lies in the image it returns, whatever order the page table lists
// the overlapping images in. Before, the loop reset the view at every iteration and kept only the
// last iteration's: a request found at mip 1 of a chain was drawn into level 0 whenever a stale
// image in the same address range was listed after the chain (a random double picture).
#pragma once

#include <span>
#include <tuple>
#include <utility>

#include "common/types.h"
#include "video_core/texture_cache/types.h"

namespace VideoCore {

struct OverlapLoopResult {
    ImageId image_id{};
    s32 view_mip = -1;
    s32 view_slice = -1;

    // What the loop returned before the fix for the same iterations (view reset every iteration),
    // and the cached image whose iteration dropped a location there. Observation only.
    s32 legacy_view_mip = -1;
    s32 legacy_view_slice = -1;
    ImageId legacy_dropped_by{};

    // The last location at mip/slice > 0 an iteration returned, and its image.
    s32 found_mip = -1;
    s32 found_slice = -1;
    ImageId found_in{};

    /// The request lies at mip/slice > 0 of the returned image, where the old loop would have
    /// ended at level/layer 0 (render trace tex.overlap_view_kept).
    bool KeptView() const {
        return (view_mip > 0 || view_slice > 0) && legacy_view_mip <= 0 && legacy_view_slice <= 0;
    }
    /// An iteration found the request at mip/slice > 0 and the result still ends at level/layer
    /// 0 (render trace tex.overlap_view_lost). With the rule above only possible when the image
    /// changed to one that does not contain the request.
    bool LostView() const {
        return (found_mip > 0 || found_slice > 0) && view_mip <= 0 && view_slice <= 0;
    }
};

/// resolve(cache_id, merged_image_id) -> {image id, view mip, view slice}, as
/// TextureCache::ResolveOverlap: a later iteration resolves the cached image against the image
/// found so far (merged), so its -1 view means "no new location", never "level 0 of the request".
/// locate(image_id) -> {mip, slice} of the request in that image (ImageInfo::MipOf/SliceOf, as
/// ResolveOverlap's right overlap), or {-1, -1} when the request is not one of its subresources.
///
/// - An iteration that keeps the image (none returned, or the same image without a location)
///   keeps the request's location in it.
/// - An iteration that returns another image takes that image's location.
/// - The returned location is the request's own location in the final image when it is one of
///   its subresources; otherwise the location carried with the final image.
template <typename Resolve, typename Locate>
OverlapLoopResult RunOverlapLoop(std::span<const ImageId> image_ids, Resolve&& resolve,
                                 Locate&& locate) {
    OverlapLoopResult r;
    for (const ImageId cache_id : image_ids) {
        const auto [overlap_image_id, overlap_view_mip, overlap_view_slice] =
            resolve(cache_id, r.image_id);

        const bool legacy_had_location = r.legacy_view_mip > 0 || r.legacy_view_slice > 0;
        r.legacy_view_mip = overlap_image_id ? overlap_view_mip : -1;
        r.legacy_view_slice = overlap_image_id ? overlap_view_slice : -1;
        if (legacy_had_location && r.legacy_view_mip <= 0 && r.legacy_view_slice <= 0) {
            r.legacy_dropped_by = cache_id;
        }

        if (overlap_image_id &&
            (overlap_image_id != r.image_id || overlap_view_mip >= 0 || overlap_view_slice >= 0)) {
            r.image_id = overlap_image_id;
            r.view_mip = overlap_view_mip;
            r.view_slice = overlap_view_slice;
        }
        if (overlap_image_id && (overlap_view_mip > 0 || overlap_view_slice > 0)) {
            r.found_mip = overlap_view_mip;
            r.found_slice = overlap_view_slice;
            r.found_in = overlap_image_id;
        }
    }
    if (r.image_id) {
        const auto [mip, slice] = locate(r.image_id);
        if (mip >= 0 && slice >= 0) {
            r.view_mip = mip;
            r.view_slice = slice;
        }
    }
    return r;
}

} // namespace VideoCore
