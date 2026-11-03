// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>

// Included only by the dev build's derived svc_address_arbiter.cpp. Never read guest
// memory here: inspecting a GPU-written page can itself change synchronization.
namespace Eden::AddressWaitTrace {
using U64 = unsigned long long;
struct Slot {
    std::atomic<U64> address{}, signals{}, last_signaller{}, slow_waits{};
};
inline std::array<Slot, 1024> slots{};
inline std::atomic<unsigned> lines{};
static_assert(std::atomic<U64>::is_always_lock_free);

inline long long NowNs() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

inline Slot* Find(U64 address, bool create) {
    if (address == 0) return nullptr;
    const auto first = static_cast<unsigned>((address >> 2) ^ (address >> 12));
    for (unsigned i = 0; i < slots.size(); ++i) {
        auto& slot = slots[(first + i) % slots.size()];
        auto key = slot.address.load(std::memory_order_relaxed);
        if (key == address) return &slot;
        if (key == 0) {
            if (!create) return nullptr;
            if (slot.address.compare_exchange_strong(key, address, std::memory_order_relaxed)
                || key == address) return &slot;
        }
    }
    return nullptr;
}

// Signal attempts, not proof that a waiter was woken. Record before entering the kernel:
// a successful signal can resume its waiter before SignalToAddress returns.
inline void Signal(U64 address, U64 tid) {
    if (auto* slot = Find(address, false)) {
        slot->last_signaller.store(tid, std::memory_order_relaxed);
        slot->signals.fetch_add(1, std::memory_order_relaxed);
    }
}

struct Wait {
    Slot* slot;
    U64 signals;
    long long start;
    Wait(U64 address) : slot{Find(address, true)},
        signals{slot ? slot->signals.load(std::memory_order_relaxed) : 0}, start{NowNs()} {}

    void Finish(U64 tid, U64 address, unsigned type, int value, long long timeout_ns,
                unsigned result, U64 pc, U64 lr) const {
        const auto elapsed = NowNs() - start;
        if (!slot || elapsed < 100'000'000) return;
        const auto n = slot->slow_waits.fetch_add(1, std::memory_order_relaxed) + 1;
        // First four, then powers of two per address; a hard ceiling for the whole run.
        if (n > 4 && (n & (n - 1)) != 0) return;
        if (lines.fetch_add(1, std::memory_order_relaxed) >= 512) return;
        std::printf("EDEN_DEV_ADDRESS_WAIT tid=%llu address=0x%llx type=%u value=%d "
                    "requested_ns=%lld elapsed_ns=%lld result=0x%x pc=0x%llx lr=0x%llx "
                    "signal_attempts=%llu last_signaller=%llu slow_waits=%llu\n",
                    tid, address, type, value, timeout_ns, elapsed, result, pc, lr,
                    slot->signals.load(std::memory_order_relaxed) - signals,
                    slot->last_signaller.load(std::memory_order_relaxed), n);
    }
};
} // namespace Eden::AddressWaitTrace
