# Unsigned IMAD.WIDE carry outputs

Adapts the unsigned WIDE carry-output portion of original patch 0047 to the current
multiply helpers. Opcodes 0x225, 0x625 and 0x825 now accept a carry output P0–P6.
They compute the low 64 bits of the unsigned product plus the 64-bit addend, and
set the output predicate when that sum exceeds 64 bits. PT retains the existing
no-output path. Signed products with carry outputs remain rejected.

The lowerer records low-word carry separately and collects overflow from the two
high-word additions. It writes the output predicate after both destination words,
so a predicate that also guards the instruction cannot suppress unfinished work.
Existing register/constant-buffer/immediate forms, alignment, scratch reservations,
signed no-output behavior and native register aliases are retained.

Run `python3 tools/check-imad-wide-carry-output.py` for the existing integer multiply
and uniform LEA host suites. It uses Clang 18 and the existing local Catch2 libraries;
it does not build the emulator. New numeric cases compare against a 128-bit host
sum across all seven output predicates, input/output aliases, ordinary/inverted
guards, initial CC states, boundary values and deterministic random inputs.
The unchanged native registers and all other predicate outputs are checked. The
new acceptance cases failed on the previous lowerer.

The host interpreter executes the canonical emitted integer instructions, rather
than executing a GPU shader. The original patch's HI addend handling remains a
separate review item and is unchanged here. No FPS improvement is claimed.

All 12 focused host cases passed (7,189,407 assertions), with address and
undefined-behavior sanitizers enabled for the test translation units and existing
Catch2 support libraries reused. PS5 syntax validation of shader_environment.cpp,
adaptation anchors and whitespace checks passed. The user confirmed Mario Kart
World is working on the console with this dev build. This is a normal-operation
smoke test, not proof the affected WIDE carry-output instructions were exercised.
