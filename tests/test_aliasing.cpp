// SPDX-License-Identifier: GPL-2.0-or-later
// Copy extents between images that share guest memory (texture_cache/aliasing.h); adapted from
// cuesta4/shadPS4 (eltutz) tests/test_aliasing.cpp. No game, Vulkan device or window required.
#include <cassert>
#include <cstdio>

#include "video_core/amdgpu/resource.h"
#include "video_core/texture_cache/aliasing.h"
#include "video_core/texture_cache/image_info.h"

using namespace VideoCore;

static ImageInfo Make(Extent3D size, vk::Format format = vk::Format::eR8G8B8A8Unorm) {
    ImageInfo info{};
    info.guest_address = 0x100000;
    info.num_bits = 32;
    info.pixel_format = format;
    info.type = AmdGpu::ImageType::Color2D;
    info.pitch = size.width;
    info.size = size;
    return info;
}

int main() {
    // Same format: the smaller image is fully covered by the larger one.
    {
        const ImageInfo src = Make({1920, 1088, 1}), dst = Make({1920, 1080, 1});
        assert(GetAliasCopyExtent(src, dst) == dst.size);
        assert(GetAliasCopyExtent(dst, src) == dst.size);
    }
    // Compatible views of other formats: the common rows.
    {
        const ImageInfo src = Make({1920, 1088, 1}, vk::Format::eR8G8B8A8Uint);
        const ImageInfo dst = Make({1920, 1080, 1}, vk::Format::eR8G8B8A8Unorm);
        const Extent3D rows{1920, 1080, 1};
        assert(GetAliasCopyExtent(src, dst) == rows);
        assert(GetAliasCopyExtent(dst, src) == rows);
    }
    // Other memory layouts never alias.
    {
        const ImageInfo src = Make({1920, 1088, 1});
        ImageInfo dst = Make({1920, 1080, 1});
        dst.guest_address += 0x1000;
        assert(!GetAliasCopyExtent(src, dst));
        dst = Make({1920, 1080, 1});
        ++dst.pitch;
        assert(!GetAliasCopyExtent(src, dst));
        dst = Make({1920, 1080, 1});
        dst.props.is_depth = true;
        assert(!GetAliasCopyExtent(src, dst));
    }
    // Incompatible intersections.
    {
        const ImageInfo src = Make({1920, 1088, 1}, vk::Format::eR8G8B8A8Uint);
        ImageInfo dst = Make({1280, 1080, 1}, vk::Format::eR8G8B8A8Unorm);
        assert(!GetAliasCopyExtent(src, dst));
        dst = Make({1920, 1080, 1}, vk::Format::eR16G16B16A16Sfloat);
        assert(!GetAliasCopyExtent(src, dst));
        dst = Make({1920, 1080, 1}, vk::Format::eR8G8B8A8Unorm);
        dst.resources.levels = 2;
        assert(!GetAliasCopyExtent(src, dst));
    }
    // A Color1D texture read over a one-row Color2D render target (Basara 4 ulti LUT, 256x1):
    // the row is the whole image, so the GPU-written texels must reach the 1D image.
    {
        const ImageInfo target = Make({256, 1, 1});
        ImageInfo lut = Make({256, 1, 1});
        lut.type = AmdGpu::ImageType::Color1D;
        const Extent3D row{256, 1, 1};
        assert(GetAliasCopyExtent(target, lut) == row);
        assert(GetAliasCopyExtent(lut, target) == row);
        // Fields that only place rows after the first do not matter for one row.
        lut.pitch = 512;
        lut.props.is_pow2 = !target.props.is_pow2;
        lut.bank_swizzle = 3;
        assert(GetAliasCopyExtent(target, lut) == row);
        assert(GetAliasCopyExtent(lut, target) == row);
        // A compatible view format of the same texel size.
        ImageInfo lut_uint = lut;
        lut_uint.pixel_format = vk::Format::eR8G8B8A8Uint;
        assert(GetAliasCopyExtent(target, lut_uint) == row);
        assert(GetAliasCopyExtent(lut_uint, target) == row);
    }
    // The 1D/2D pair needs the same texels in the same memory order: anything else stays separate.
    {
        const ImageInfo target = Make({256, 1, 1});
        ImageInfo lut = Make({256, 1, 1});
        lut.type = AmdGpu::ImageType::Color1D;
        ImageInfo other = lut;
        other.tile_mode = AmdGpu::TileMode::Thin1DThin; // other texel order within the row
        assert(!GetAliasCopyExtent(target, other) && !GetAliasCopyExtent(other, target));
        other = lut;
        other.size.width = 128;
        assert(!GetAliasCopyExtent(target, other) && !GetAliasCopyExtent(other, target));
        other = lut;
        other.pixel_format = vk::Format::eR16G16B16A16Sfloat;
        assert(!GetAliasCopyExtent(target, other) && !GetAliasCopyExtent(other, target));
        other = lut;
        other.guest_address += 0x100;
        assert(!GetAliasCopyExtent(target, other) && !GetAliasCopyExtent(other, target));
        other = lut;
        other.alt_tile = true;
        assert(!GetAliasCopyExtent(target, other) && !GetAliasCopyExtent(other, target));
        const ImageInfo tall = Make({256, 2, 1}); // more than one row
        assert(!GetAliasCopyExtent(tall, lut) && !GetAliasCopyExtent(lut, tall));
        ImageInfo levels = lut;
        levels.resources.levels = 2;
        assert(!GetAliasCopyExtent(target, levels) && !GetAliasCopyExtent(levels, target));
        ImageInfo volume = target;
        volume.type = AmdGpu::ImageType::Color3D; // only the 1D/2D pair
        assert(!GetAliasCopyExtent(volume, lut) && !GetAliasCopyExtent(lut, volume));
    }
    std::puts("aliasing-test: OK");
    return 0;
}
