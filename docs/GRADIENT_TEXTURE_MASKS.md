# Native derivative texture channel masks

Adapts the remaining TXD channel-mask handling from original patch 0044. Native
2D and 2D-array gradient samples previously required all four output channels.
The lowerer now accepts every nonempty four-bit mask, forwards that mask to the
canonical Maxwell TXD, and packs only the selected channels into destination
registers. One result may use an odd destination; multi-result low pairs remain
aligned. One or two results require the unused high destination to be RZ.

For three or four outputs with split destination pairs, the canonical instruction
writes scratch registers and copies only the requested results back. Existing
descriptor-buffer, texture-dimension, offset, mode and scratch-register restrictions
remain. The newer descriptor-store path already accepts the encoding previously
flagged by the audit; no descriptor-store change is included here.

Run `python3 tools/check-gradient-texture-masks.py` for host-only tests using the
existing local Catch2 libraries and Clang 18. The tests are also registered in the
core's CMake test list. Cases cover all 15 nonempty masks, both dimensions,
ordinary/inverted predicates, native bit 90, direct/split outputs, binding/offset
boundaries, descriptor-buffer conflict, and invalid masks/destinations/modes.

Distinct synthetic channel values and destination sentinels check dense packing
and prevent unnoticed extra writes to partial destination pairs. This models the
canonical TXD/MOV write contract; it does not execute texture sampling or Vulkan.
The partial-mask cases failed on the previous lowerer. No FPS improvement is claimed.

All three focused host cases passed (93,652 assertions), with address and
undefined-behavior sanitizers enabled for the test translation unit and existing
Catch2 support libraries reused. PS5 syntax validation of shader_environment.cpp,
adaptation anchors and whitespace checks passed. The user confirmed Mario Kart
World is working on the console with this dev build. This is a normal-operation
smoke test, not evidence that the game exercised partial-mask TXD instructions.
