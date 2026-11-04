# Native shader conversion and comparison forms

Ports two focused gaps from original patch 0048 while retaining the newer lowerer.

- I2F opcode 0x312 accepts signed as well as unsigned 64-bit register pairs and
  forwards signedness to canonical Maxwell bit 13. All four rounding modes and
  ordinary instruction predicates are preserved. Existing pair alignment and
  scratch-register limits remain in place.
- FSET opcode 0xc0a uses the existing uniform operand materialization. The uniform
  prepass reserves its emulated register bank for the entire program, including
  instructions before the comparison. Comparison, boolean combination, predicate,
  FTZ and operand modifier handling use the existing FSET path.

The new core tests are registered in src/tests/CMakeLists.txt. A host-only subset
can also be run with `python3 tools/check-shader-conversion-comparison.py` using
Clang 18 and the existing Catch2 libraries in .local/nx2-core-check. It includes
the new forms plus nearby F2I and uniform LEA regressions. It does not build the
emulator or run Vulkan.

The regressions check canonical instruction encodings, uniform mapping boundaries,
modifier equivalence with the existing register form, invalid modes and register
aliases. Both missing forms failed on the previous lowerer. This is translation
coverage, not an end-to-end numeric execution test or proof a game uses the forms.

All 37 focused host test cases passed (12,497 assertions) with address and
undefined-behavior sanitizers enabled for the test translation units. The existing
Catch2 support libraries were reused. PS5 syntax validation of shader_environment.cpp,
adaptation anchors and whitespace checks passed. After correcting source-cache
hashing, the user confirmed the build and Mario Kart World console run are working.
This is a smoke test and does not prove the game exercised either new form.
No FPS improvement is claimed.

The port also fixes source-cache hashing of untracked core files. Git returns
core-relative paths, so their hashes must be calculated from the core directory.
This was exposed by the two new untracked regression files during build preparation.
The actual source-sync script also passed an isolated fixture check from an
unrelated working directory, covering untracked names with spaces/newlines, stable
stamps, staged and modified content, and deletion propagation.
