#!/usr/bin/env python3
"""Check the actual NUP rate sampler with deterministic progress intervals."""
from pathlib import Path
import subprocess
import tempfile
r = Path(__file__).resolve().parents[1]
source = (r/'headless/eglantineps5/pe/ui/nup_conversion.cpp').read_text()
start = source.index('    const auto now = steady_clock::now();', source.index('void Launcher::poll_nup_conversion()'))
end = source.index('\n}\n', start)
body = source[start:end]
code = r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdint>
struct Clock {
    using time_point = std::chrono::steady_clock::time_point;
    static inline time_point time{};
    static time_point now() { return time; }
    static void Set(int seconds) { time = time_point{} + std::chrono::seconds(seconds); }
};
struct Model {
    std::atomic<uint64_t> nup_done_{0};
    std::atomic<bool> nup_validating_{false};
    bool nup_sample_verifying_{};
    uint64_t nup_sample_bytes_{};
    double nup_speed_{};
    Clock::time_point nup_sample_clock_{};
    void Sample() {
        using steady_clock = Clock;
        using std::chrono::duration;
''' + body + r'''
    }
};
int main() {
    Model m;
    Clock::Set(4); m.nup_done_ = 4ULL << 20; m.Sample();
    assert(m.nup_speed_ == (1ULL << 20));
    for (int i=5; i<20; ++i) { Clock::Set(i); m.Sample(); }
    assert(m.nup_speed_ == (1ULL << 20));
    Clock::Set(20); m.nup_done_ = 20ULL << 20; m.Sample();
    assert(m.nup_speed_ == (1ULL << 20));
    Clock::Set(21); m.nup_validating_ = true; m.nup_done_ = 0; m.Sample();
    assert(m.nup_speed_ == 0 && m.nup_sample_bytes_ == 0);
    Clock::Set(23); m.nup_done_ = 2ULL << 20; m.Sample();
    assert(m.nup_speed_ == (1ULL << 20));
    Clock::Set(24); m.nup_done_ = 0; m.Sample();
    assert(m.nup_speed_ == 0);
}
'''
with tempfile.TemporaryDirectory(prefix='nup-estimator-') as tmp:
    p = Path(tmp)
    (p/'test.cpp').write_text(code)
    subprocess.run(['clang++-18','-std=c++20','-fsanitize=address,undefined',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('Actual NUP rate sampler: sparse updates, full-interval rates, phase change and reset PASS')
