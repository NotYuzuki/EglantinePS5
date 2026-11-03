#!/usr/bin/env python3
"""Exercise the actual layer-group method with sparse indices and staging lifetimes."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = (root / 'Eglantine/src/video_core/texture_cache/texture_cache.h').read_text()
start = source.index('template <class P>\nbool TextureCache<P>::UploadLayerBatches(')
end = source.index('template <class P>\nvoid TextureCache<P>::UploadLayers(', start)
method = source[start:end]
code = r'''#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <vector>
using u32 = std::uint32_t;
using u64 = std::uint64_t;
#define LOG_INFO(...) ((void)0)
size_t budget;
size_t TextureUploadBatchBytes() { return budget; }
struct Image { u64 gpu_addr{}; };
template <class P> struct TextureCache {
    std::vector<std::vector<u32>> groups;
    std::vector<u32> observed;
    size_t stride{};
    struct Runtime {
        bool pending{};
        size_t finishes{};
        void Finish() { assert(pending); pending = false; ++finishes; }
    } runtime;
    bool UploadLayerBatches(Image&, std::span<const u32>, size_t);
    void UploadLayers(Image& image, std::span<const u32> layers) {
        if (UploadLayerBatches(image, layers, stride)) return;
        assert(!runtime.pending); // previous staging users must finish before reuse
        assert(layers.size() == 1 || stride <= budget / layers.size());
        groups.emplace_back(layers.begin(), layers.end());
        observed.insert(observed.end(), layers.begin(), layers.end());
        runtime.pending = true;
    }
};
'''
code += method
code += r'''
int main() {
    Image image;
    const std::vector<u32> layers{19, 2, 41, 3, 90};
    for (size_t target : {size_t{1}, size_t{8}, size_t{32}}) {
        for (size_t stride : {size_t{1}, size_t{3}, size_t{8}, size_t{33},
                              std::numeric_limits<size_t>::max()}) {
            budget = target;
            TextureCache<int> cache; cache.stride = stride;
            const bool split = cache.UploadLayerBatches(image, layers, stride);
            if (split) {
                assert(cache.observed == layers); // preserve order, no omitted/duplicate layers
                assert(cache.runtime.finishes == cache.groups.size());
                assert(!cache.runtime.pending);
                const auto per_group = std::max(size_t{1}, target / stride);
                assert(cache.groups.size() == (layers.size() + per_group - 1) / per_group);
            } else {
                assert(cache.groups.empty() && cache.runtime.finishes == 0);
            }
        }
    }
    TextureCache<int> cache;
    budget = 0;
    assert(!cache.UploadLayerBatches(image, layers, 4));
    budget = 8;
    assert(!cache.UploadLayerBatches(image, layers, 0));
    assert(!cache.UploadLayerBatches(image, {}, 4));
    assert(!cache.UploadLayerBatches(image, std::span{layers}.first(1), 99));
    assert(!cache.UploadLayerBatches(image, std::span{layers}.first(2), 4));
    assert(cache.groups.empty());
}
'''
with tempfile.TemporaryDirectory(prefix='eden-upload-batches-') as tmp:
    tmp = Path(tmp)
    (tmp / 'check.cpp').write_text(code)
    subprocess.run(['clang++-18', '-std=c++20', '-fsanitize=address,undefined',
                    str(tmp / 'check.cpp'), '-o', str(tmp / 'check')], check=True)
    subprocess.run([str(tmp / 'check')], check=True)
print('Layer grouping, sparse layer order, staging completion, oversized layer and opt-out checks PASS')
