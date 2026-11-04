// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>
#include <filesystem>
#include <optional>

namespace Eden::NupSpace {
inline std::optional<std::uint64_t> Available(const std::filesystem::path& path) {
#if defined(__PROSPERO__) || defined(PS5_NATIVE)
    // Native titles cannot query statfs safely; unknown is not a full disk.
    (void)path;
    return std::nullopt;
#else
    std::error_code error;
    const auto space = std::filesystem::space(path, error);
    if (error || space.available == static_cast<std::uintmax_t>(-1)) return std::nullopt;
    return space.available;
#endif
}
inline bool Insufficient(std::optional<std::uint64_t> available, std::uint64_t required,
                         std::uint64_t reusable) {
    return available && *available < required && reusable < required - *available;
}
}
