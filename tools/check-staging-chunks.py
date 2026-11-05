#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 Oliv3rmoon
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host check of large transfers staged in pieces (headless/staging_chunks.h).

From Nightshade (Oliv3rmoon/Nightshade tools/check-staging-chunks.py at 7f1a5cbf), without its
replay of the port's replacements: tools/prepare-vulkan-port.py's adapt() refuses an anchor that
does not match this core's source exactly once, so the build checks that.

Runs the chunker under ASan and UBSan on the transfers that ended sessions: Tears of the Kingdom's
texture array of 121 layers (1024x1024, 11 levels) as BC3 (161 MiB, staged in one 176 MiB buffer
on v1.010.006) and decoded (645 MiB), a 3D image, and buffer transfers of 161 MiB. Every staging
buffer asked for holds at most 64 MiB, every layer and byte lands exactly once where the GPU
reads it, and the data is the transfer's. A staging buffer refused above 20 MiB is asked for again
at half the size; refused at every size, the refusal goes on after 4 MiB.
"""
from pathlib import Path
import ast, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[1]

code = r"""
#include "staging_chunks.h"
#include <cassert>
#include <cstdio>
#include <map>
#include <stdexcept>
#include <vector>
using namespace Eden::StagingChunks;
struct SubresourceLayers { int base_level = 0; int base_layer = 0; int num_layers = 1; };
struct Offset3D { int x = 0, y = 0, z = 0; };
struct Extent3D { unsigned width = 0, height = 0, depth = 0; };
struct BufferImageCopy {
    size_t buffer_offset; size_t buffer_size; unsigned buffer_row_length; unsigned buffer_image_height;
    SubresourceLayers image_subresource; Offset3D image_offset; Extent3D image_extent;
};
struct Staging { std::vector<std::uint8_t>* memory = nullptr; std::span<std::uint8_t> mapped_span; };
constexpr std::uint64_t MiB = std::uint64_t{1} << 20;

// The copies Eden's UnswizzleImage makes: one per level, its layers one after another.
static std::vector<BufferImageCopy> Levels(unsigned size, unsigned layers, unsigned levels, unsigned block_bytes,
                                           unsigned block, unsigned depth = 1) {
    std::vector<BufferImageCopy> copies;
    size_t offset = 0;
    for (unsigned level = 0; level < levels; ++level) {
        const unsigned extent = (std::max)(1u, size >> level), d = (std::max)(1u, depth >> level);
        const size_t per_layer = size_t((extent + block - 1) / block) * ((extent + block - 1) / block) * block_bytes * d;
        copies.push_back(BufferImageCopy{offset, per_layer * layers, extent, extent,
                                         {int(level), 0, int(layers)}, {0, 0, 0}, {extent, extent, d}});
        offset += per_layer * layers;
    }
    return copies;
}
static std::uint8_t Byte(std::uint64_t at) { return std::uint8_t(at * 2654435761u >> 13); }

// Stages one image transfer and checks that each level's layers (and slices) land once, with the
// transfer's own bytes. Returns the largest staging buffer asked for.
static std::uint64_t Image(const std::vector<BufferImageCopy>& copies, std::uint64_t refuse_above, unsigned* batches) {
    std::uint64_t total = 0;
    for (const auto& copy : copies) total = (std::max<std::uint64_t>)(total, copy.buffer_offset + copy.buffer_size);
    std::vector<std::uint8_t> data(total);
    for (std::uint64_t i = 0; i < total; ++i) data[i] = Byte(i);
    std::vector<std::vector<std::uint8_t>> buffers;
    std::uint64_t largest = 0;
    std::map<std::tuple<int, int, int>, int> seen;  // (level, layer, slice)
    const auto request = [&](std::uint64_t bytes) {
        if (bytes > refuse_above) throw std::runtime_error("refused");
        largest = (std::max)(largest, bytes);
        buffers.emplace_back(bytes);
        return Staging{&buffers.back(), std::span<std::uint8_t>(buffers.back().data(), bytes)};
    };
    const auto upload = [&](Staging& staging, std::span<const BufferImageCopy> batch) {
        for (const auto& copy : batch) {
            assert(copy.buffer_offset % kAlign == 0 || copy.buffer_offset == 0);
            assert(copy.buffer_offset + copy.buffer_size <= staging.mapped_span.size());
            const auto& whole = copies[size_t(copy.image_subresource.base_level)];
            const std::uint64_t layers = std::uint64_t(copy.image_subresource.num_layers);
            const std::uint64_t slices = copy.image_extent.depth;
            assert(copy.buffer_size % (layers * slices) == 0);
            const std::uint64_t unit = copy.buffer_size / (layers * slices);
            for (std::uint64_t l = 0; l < layers; ++l)
                for (std::uint64_t s = 0; s < slices; ++s) {
                    const int layer = copy.image_subresource.base_layer + int(l), slice = copy.image_offset.z + int(s);
                    ++seen[{copy.image_subresource.base_level, layer, slice}];
                    const std::uint64_t source = whole.buffer_offset +
                        (std::uint64_t(layer) * whole.image_extent.depth + std::uint64_t(slice)) * unit;
                    const std::uint64_t staged = copy.buffer_offset + (l * slices + s) * unit;
                    for (std::uint64_t b = 0; b < unit; b += 4093)
                        if (staging.mapped_span[staged + b] != Byte(source + b)) {
                            std::fprintf(stderr, "level %d layer %d slice %d: wrong bytes at %llu\n",
                                         copy.image_subresource.base_level, layer, slice, (unsigned long long)b);
                            std::abort();
                        }
                }
        }
    };
    *batches = unsigned(StageImage<BufferImageCopy>(std::span<const std::uint8_t>(data), copies.data(), copies.size(),
                                                    request, upload));
    for (const auto& copy : copies)
        for (int layer = 0; layer < copy.image_subresource.num_layers; ++layer)
            for (unsigned slice = 0; slice < copy.image_extent.depth; ++slice)
                if (seen[{copy.image_subresource.base_level, layer, int(slice)}] != 1) {
                    std::fprintf(stderr, "level %d layer %d slice %u staged %d times\n", copy.image_subresource.base_level,
                                 layer, slice, seen[{copy.image_subresource.base_level, layer, int(slice)}]);
                    std::abort();
                }
    return largest;
}

// Stages buffer transfers (offset, size) and checks every byte lands once.
static std::uint64_t Buffer(const std::vector<std::pair<std::uint64_t, std::uint64_t>>& ranges, std::uint64_t refuse_above,
                            unsigned* batches) {
    std::map<std::uint64_t, std::uint64_t> covered;
    std::uint64_t largest = 0;
    std::vector<std::vector<std::uint8_t>> buffers;
    const auto request = [&](std::uint64_t bytes) {
        if (bytes > refuse_above) throw std::runtime_error("refused");
        largest = (std::max)(largest, bytes);
        buffers.emplace_back(bytes);
        return Staging{&buffers.back(), std::span<std::uint8_t>(buffers.back().data(), bytes)};
    };
    const auto run = [&](Staging& staging, const std::vector<Piece>& pieces) {
        for (const auto& piece : pieces) {
            assert(piece.staged % kAlign == 0 && piece.staged + piece.size <= staging.mapped_span.size());
            covered[piece.at] += piece.size;
        }
    };
    *batches = unsigned(StageBuffer(ranges.size(), [&](std::size_t i) { return ranges[i]; }, request, run));
    for (const auto& [at, size] : ranges) {
        std::uint64_t done = 0;
        for (auto it = covered.lower_bound(at); it != covered.end() && it->first < at + size; ++it) done += it->second;
        if (done != size) { std::fprintf(stderr, "range at %llu: %llu of %llu bytes\n", (unsigned long long)at,
                                         (unsigned long long)done, (unsigned long long)size); std::abort(); }
    }
    return largest;
}

int main() {
    unsigned batches = 0;
    // TOTK's array as BC3: 16 bytes for every 4x4 block, 161 MiB in all.
    const auto bc3 = Levels(1024, 121, 11, 16, 4);
    std::uint64_t total = 0;
    for (const auto& copy : bc3) total += copy.buffer_size;
    assert(total > 160 * MiB && total < 162 * MiB && Chunked(total));
    std::uint64_t largest = Image(bc3, ~std::uint64_t{0}, &batches);
    std::printf("TOTK texture array as BC3: %llu MiB in %u staging buffers, the largest %llu MiB\n",
                (unsigned long long)(total >> 20), batches, (unsigned long long)(largest >> 20));
    assert(largest <= kLimit && batches >= 3);
    // Refused above 20 MiB: asked for again at half the size.
    largest = Image(bc3, 20 * MiB, &batches);
    std::printf("  refused above 20 MiB: %u staging buffers, the largest %llu MiB\n", batches,
                (unsigned long long)(largest >> 20));
    assert(largest <= 16 * MiB);
    // Refused at every size: the refusal goes on.
    bool threw = false;
    try { (void)Image(bc3, 0, &batches); } catch (const std::runtime_error&) { threw = true; }
    assert(threw);
    // Decoded: 4 bytes a pixel, 645 MiB.
    const auto rgba = Levels(1024, 121, 11, 4, 1);
    largest = Image(rgba, ~std::uint64_t{0}, &batches);
    std::printf("TOTK texture array decoded: %u staging buffers, the largest %llu MiB\n", batches,
                (unsigned long long)(largest >> 20));
    assert(largest <= kLimit && batches >= 10);
    // Decoded copies as Eden's ConvertImage left them before this tree set their sizes: each at its
    // compressed size (16 bytes a 4x4 block), laid out decoded. Sized() gives each the bytes up to
    // the next one's data, so every layer is staged whole.
    {
        auto stale = rgba;
        for (std::size_t k = 0; k < stale.size(); ++k) stale[k].buffer_size = bc3[k].buffer_size;
        std::uint64_t rgba_total = 0;
        for (const auto& copy : rgba) rgba_total += copy.buffer_size;
        const auto sized = Sized<BufferImageCopy>(stale.data(), stale.size(), rgba_total);
        for (std::size_t k = 0; k < sized.size(); ++k) assert(sized[k].buffer_size == rgba[k].buffer_size);
        largest = Image(sized, ~std::uint64_t{0}, &batches);
        assert(largest <= kLimit && batches >= 10);
        std::printf("  with the sizes ConvertImage left: repaired, %u staging buffers\n", batches);
    }
    // A 3D image (one layer, 256 slices of 512x512 RGBA): staged by slices.
    const auto volume = Levels(512, 1, 1, 4, 1, 256);
    largest = Image(volume, ~std::uint64_t{0}, &batches);
    std::printf("3D image of 256 MiB: %u staging buffers, the largest %llu MiB\n", batches,
                (unsigned long long)(largest >> 20));
    assert(largest <= kLimit && batches == 4);
    // A small transfer is not staged in pieces at all.
    assert(!Chunked(64 * MiB) && Chunked(64 * MiB + 1));
    // Buffers: 161 MiB in two ranges, and a refusal above 40 MiB.
    const std::vector<std::pair<std::uint64_t, std::uint64_t>> ranges{{0, 100 * MiB}, {200 * MiB, 61 * MiB + 12345}};
    largest = Buffer(ranges, ~std::uint64_t{0}, &batches);
    std::printf("buffer transfer of 161 MiB: %u staging buffers, the largest %llu MiB\n", batches,
                (unsigned long long)(largest >> 20));
    assert(largest <= kLimit && batches == 3);
    largest = Buffer(ranges, 40 * MiB, &batches);
    assert(largest <= 32 * MiB);
    threw = false;
    try { (void)Buffer(ranges, 0, &batches); } catch (const std::runtime_error&) { threw = true; }
    assert(threw);
    std::printf("staging chunks: PASS\n");
    return 0;
}
"""

with tempfile.TemporaryDirectory(prefix='eden-chunks-') as tmp:
    exe = Path(tmp) / 'chunks'
    compiler = shutil.which('clang++-18') or shutil.which('clang++') or 'c++'
    subprocess.run([compiler, '-std=c++20', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=undefined', '-I' + str(root / 'headless'), '-x', 'c++', '-', '-o', str(exe)],
                   input=code, text=True, check=True)
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
    sys.stdout.write(run.stdout)
    if run.returncode != 0:
        sys.stderr.write(run.stderr[-4000:])
        sys.exit(f'staging chunks check failed: exit {run.returncode}')
