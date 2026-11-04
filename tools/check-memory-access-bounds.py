#!/usr/bin/env python3
"""Run actual VFS read and buffer lookup methods; require missing guards to fail controls."""
from pathlib import Path
import os
import resource
import subprocess
import tempfile
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
root = Path(__file__).resolve().parents[1]
core = root / 'Eglantine'
vfs = 'src/core/file_sys/vfs/vfs_vector.cpp'
buffer = 'src/video_core/buffer_cache/buffer_cache.h'
def read_method(text):
    start = text.index('std::size_t VectorVfsFile::Read(')
    return text[start:text.index('\n}\n', start) + 3]
def find_method(text):
    start = text.index('template <class P>\nBufferId BufferCache<P>::FindBuffer(')
    return text[start:text.index('\ntemplate <class P>', start + 1)]
current = [read_method((core / vfs).read_text()), find_method((core / buffer).read_text())]
vfs_guard = """    if (length == 0 || offset >= data.size()) {
        return 0;
    }
"""
assert current[0].count(vfs_guard) == 1
assert current[1].count(' || page >= page_table.size()') == 1
# Remove one guard at a time so controls remain useful after the fixes are committed.
baseline = [current[0].replace(vfs_guard, ''),
            current[1].replace(' || page >= page_table.size()', '')]
prefix = r'''#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <vector>
using u8 = std::uint8_t;
using u32 = std::uint32_t;
using u64 = std::uint64_t;
using DAddr = u64;
using BufferId = u32;
constexpr BufferId NULL_BUFFER_ID = 0;
struct VectorVfsFile {
    std::vector<u8> data;
    size_t Read(u8*, size_t, size_t) const;
};
namespace Settings {
bool accurate = false;
bool IsGPUFenceBehaviorAccurate() { return accurate; }
}
struct Buffer {
    bool fits = true, sparse = true;
    bool IsInBounds(DAddr, u32) { return fits; }
    bool IsSparseCompatible() { return sparse; }
};
struct CheckedTable {
    std::array<BufferId, 8> entries{};
    unsigned reads{};
    size_t size() const { return entries.size(); }
    BufferId operator[](size_t i) { assert(i < entries.size()); ++reads; return entries[i]; }
};
template <class P> struct BufferCache {
    static constexpr unsigned CACHING_PAGEBITS = 16;
    CheckedTable page_table;
    std::array<Buffer, 2> slot_buffers{};
    unsigned creates{}, syncs{};
    BufferId FindBuffer(DAddr, u32, bool);
    BufferId CreateBuffer(DAddr, u32, bool) { ++creates; return 42; }
    void SynchronizeBufferWrites(Buffer&) { ++syncs; }
};
'''
main = r'''
int main() {
    constexpr auto max = std::numeric_limits<size_t>::max();
    VectorVfsFile empty;
    std::array<u8, 6> output{91, 91, 91, 91, 91, 91};
    assert(empty.Read(output.data(), 4, 1) == 0);
    assert(empty.Read(nullptr, 0, max) == 0);
    VectorVfsFile file{{1, 2, 3, 4}};
    for (size_t offset : {size_t{4}, size_t{5}, max}) {
        assert(file.Read(output.data(), 4, offset) == 0);
        assert(output == (std::array<u8, 6>{91, 91, 91, 91, 91, 91}));
    }
    assert(file.Read(nullptr, 0, 2) == 0);
    assert(file.Read(output.data() + 1, max, 2) == 2);
    assert(output == (std::array<u8, 6>{91, 3, 4, 91, 91, 91}));
    assert(file.Read(output.data(), 2, 0) == 2);
    assert(output[0] == 1 && output[1] == 2);

    BufferCache<int> cache;
    constexpr DAddr limit = DAddr{8} << cache.CACHING_PAGEBITS;
    for (DAddr address : {DAddr{0}, limit, limit + 1, std::numeric_limits<DAddr>::max()}) {
        assert(cache.FindBuffer(address, 1, false) == NULL_BUFFER_ID);
        assert(cache.page_table.reads == 0 && cache.creates == 0 && cache.syncs == 0);
    }
    cache.page_table.entries[7] = 1;
    assert(cache.FindBuffer(limit - 1, 1, false) == 1);
    assert(cache.page_table.reads == 1 && cache.creates == 0);
    assert(cache.FindBuffer(1, 1, false) == 42);
    assert(cache.creates == 1);
    Settings::accurate = true;
    cache.slot_buffers[1].sparse = false;
    assert(cache.FindBuffer(limit - 1, 1, true) == 42);
    assert(cache.creates == 2 && cache.syncs == 1);
    cache.slot_buffers[1].fits = false;
    assert(cache.FindBuffer(limit - 1, 1, false) == 42);
    assert(cache.creates == 3);
}
'''
with tempfile.TemporaryDirectory(prefix='eden-memory-bounds-') as tmp:
    tmp = Path(tmp)
    for name, methods in [('current', current), ('old-vfs', [baseline[0], current[1]]),
                          ('old-buffer', [current[0], baseline[1]])]:
        source = tmp / (name + '.cpp')
        source.write_text(prefix + '\n'.join(methods) + main)
        exe = tmp / name
        subprocess.run(['clang++-18', '-std=c++20', '-fsanitize=address,undefined',
                        str(source), '-o', str(exe)], check=True)
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:disable_coredump=1')
        result = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
        if name == 'current':
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, 'Old method unexpectedly passed: ' + name
            assert ('AddressSanitizer' in result.stderr or 'Assertion' in result.stderr), result.stderr
print('Actual read/lookup bounds, unchanged valid behavior and both negative controls PASS')
