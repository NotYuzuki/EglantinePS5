# Zero-product IMAD.X carry handling

Adapts a focused remaining form from original patch 0047. With both factors RZ,
IMAD.X should produce its addend (complemented by native bit 75) plus the carry
predicate (inverted by bit 90). The target's special case excluded bit 75, bit 90,
and an RZ addend. These forms reached a generic move fallback that lost the carry
and complement behavior.

The special case now accepts those forms and complements the addend with a
predicated XOR before adding the materialized carry. Existing uniform-carry
handling, register reservations and supported-mode limits are retained. Other
IMAD modes and the generic zero-product fallback are not revised here.

The existing integer-machine host tests now execute canonical SEL and immediate
XOR. The new regression checks mathematical results across complemented/plain
addends, inverted/plain carry predicates, every ordinary carry predicate including
PT, RZ addends, destination aliases, true/false execution guards and integer boundary
values. Untouched native registers and predicate state must remain unchanged.
The numeric regression failed against the previous lowerer.

Run `python3 tools/check-imad-zero-product-carry.py` using Clang 18 and the existing
local Catch2 libraries. It runs the integer multiplication and uniform LEA suites
as host binaries, without building the emulator or executing Vulkan. This covers
ordinary IMAD.X for the new forms; new complemented uniform-carry forms are not
independently executed by the host model.

All 10 focused host cases passed (3,588,129 assertions), with address and
undefined-behavior sanitizers enabled for the test translation units and the
existing Catch2 support libraries reused. PS5 syntax validation of
shader_environment.cpp, adaptation anchors and whitespace checks passed.
The user confirmed Mario Kart World is working on the console with this dev
build. This is a normal-operation smoke test, not proof the affected IMAD.X forms
were exercised. No FPS improvement is claimed.
