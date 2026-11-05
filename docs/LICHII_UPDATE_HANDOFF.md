# Lichii core update: handoff (2026-11-05)

The Eglantine core was updated to upstream Lichii (zortos293/Lichii, branch `nx2`, commit
724aede4ea) to get its newer Ampere shader lowering and its `audsmx:u`/sysmix audio for
Ocarina of Time (0400486024DEC000). The PS5 build builds, packages and boots OoT on the
console; the intro movie plays. **Not done:** the game hangs right after the movie, and the
second half of the movie's audio is wrong.

## Where the work is

| Repo | Branch | Commits |
| --- | --- | --- |
| Eglantine (submodule) | `claude/lichii-update` | `d86188addf` merge, `e457cfd52a` build/test fixes |
| EglantinePS5 | `claude/lichii-update` | this doc plus the port changes (one commit on top of `16317b7`) |

Neither branch is pushed. `claude/lichii-update` in EglantinePS5 starts from
`claude/zelda-gpu-semaphore-bug-ee12c5` (the OoT boot work, already pushed as its own PR).

Evidence from the last console runs is in `.local/lichii-handoff/` (ignored by git):
symbolized hang reports, `eden_log.txt`, `stderr.log`, and the SPIR-V of every OoT compute
shader (`compute-spirv/<hash>.spv`).

## The merge

Two merge bases (3140648a66, 1d94552785). Taken from upstream: the whole Ampere lowering
(`lower_to_maxwell.h`, plus our IDP.4A path and a `target` parameter on `multiply_low`),
audio (`nx2_audio_device_manager.h`, `audio.cpp`, sysmix), the GPU memory registry
(`nx2_stub_device.h`, `nvhost_as_gpu.*`, `nvhost_ctrl_gpu.cpp`, `nx2_gsv.h`), the GPU
pipeline (`dma_pusher.*`, `gpu_split.*`, `gpu.cpp`, `vk_buffer_cache.cpp`,
`vk_pipeline_cache.cpp`), the VMA separate pool, the engines and the rasterizer.

Kept ours: every NUP file (`nup_container.*`, `nup_writer.*`, `loader/nup.*`, their test),
`ampere_integer_multiply.cpp`, `cache_tracking_diagnostics.h`.

Re-applied on upstream's code: `EDEN_NX2_PRESENT_HZ` (PaceToVblank), `TranslateRange` on the
registry and the `nvhost_gpu` snapshot fallback (OCARINA_BOOT.md §1), trimmed host levels
and huge BCn arrays in `vk_texture_cache`, `texture_cache.write_source = 5`, our ROM
directory layers, BC3 ASTC for Switch 2 on every card, F2I.U16 constant negate (Pokopia).

Dropped (document or bring back if something regresses):
- Our no-lock registry fix 4cd10d83f1, replaced by upstream's design: **check Mario Kart
  World does not hang again.**
- Shader module reuse, readback diagnostics, partial-write page tracking
  (`RestorePartialPages`), the dev `dispatch_draws` knob, `EDEN_VMA_BLOCK_MIB` (upstream
  fixes 16 MiB, the same value).
- Our multiplayer BSD/proxy rewrites: upstream's own descriptor table and EmulatedPoll are
  used; `multiplayer_packets.cmake` adds only the proxy queue bound.
- Upstream's NUP extract/edit APIs (not merged).
- `nx2_gpu_mapping_table.h`, `nx2_sparse_region.h` and their tests (replaced by the
  registry).

Upstream semantics accepted: IMAD.HI adds the 64-bit pair c:c+1; upstream ULEA (our
`ampere_uniform_lea` test removed); FSET from UR28 up; TXD with 3/4 channels and RZ high;
IDP.2A.

## Port changes (EglantinePS5)

- `headless/CMakeLists.txt`: anchors updated for upstream (fiber stack size, kernel
  server manager, audio mono mirroring, GPU `~Impl` glFinish, TSC block, JIT
  `EdenJitAllocator`, ProcessQueryCondition `condition_end` now after the DKB trace block);
  the RomFS parent patch is gone (upstream fixed it).
- `headless/multiplayer_packets.cmake`: upstream's sockets/bsd/proxy copied unchanged.
- `tools/prepare-vulkan-port.py`: `--report` clean on the merged core (see the anchor list in
  its diff). Two new development aids:
  - every compute pipeline build over 1 s is written to stderr
    (`EDEN_COMPUTE_BUILD done <hash> <ms> ms`); `EDEN_COMPUTE_BUILD_TRACE=1` also writes
    each start, so a build that does not end shows as a start with no done;
  - `EDEN_COMPUTE_SPIRV_DUMP=<dir>` writes each compute shader's SPIR-V as `<hash>.spv`.
- `src/host_memory.cpp`: `MarkUsed`/`MarkDirty`/`ForgetContents` no-ops are no longer behind
  `EDEN_HOST_MEMORY_BACKING_MODE` (upstream dropped `BackingMode` but kept them), and
  `HintCold` is a no-op.

Checks that pass: `make dev DEV_TITLE=0400486024dec000` (package checks PASS); host tests
`ampere_f2i`, `ampere_integer_multiply`, `ampere_uniform_fset`, `ampere_gradient_texture`,
`ampere_matrix`, `ampere_i2f64`, `ampere_returns`; `tools/check-software-hmma.py` (with
`EDEN_CATCH2_BUILD=/home/esotericizm/exploitizmPS5/.local/nx2-core-check` in a worktree).
The host tests are compiled one file at a time against that check's Catch2:

```bash
B=/home/esotericizm/exploitizmPS5/.local/nx2-core-check
clang++-18 -std=c++20 -O1 -DARCHITECTURE_x86_64=1 -pthread -IEglantine/src -I$B/_deps/catch2-src/src -I$B/_deps/catch2-build/generated-includes Eglantine/src/tests/video_core/ampere_f2i.cpp $B/_deps/catch2-build/src/libCatch2Main.a $B/_deps/catch2-build/src/libCatch2.a -o /tmp/ampere_f2i
```

## What the console showed

Console `dev-settings.txt` (in the installed package) for these runs:

```
replay=off
env=EDEN_COMPUTE_BUILD_TRACE=1
env=EDEN_COMPUTE_SPIRV_DUMP=/data/eglantineps5/logs/oot-spirv
env=EDEN_NX2_SHADER_DUMP=/data/eglantineps5/logs/oot-shaders
```

1. **First boot, no driver cache: three compute pipelines take minutes in RADV.**
   `8c29514395041853`, `eff3ccbe1438679d`, `f9ea14a22394f5b1` (0.84-1.3 MB of SPIR-V, 600-1000
   subgroup shuffles each); the other 11 build in 0 ms. While they build, the GPU thread
   waits in `KeplerCompute::ProcessNativeReportSemaphore` and the game shows nothing (hang
   report 19:27, `hang-20261105-192734.sym`), and on the next launch the loader sits at
   "Building shaders 11/14". They do finish: on the following launch all 14 came from RADV's
   pipeline cache in 0 ms. Probably the old core rejected these programs (it rejected 159
   shaders after the movie), so they never reached RADV; not checked.
   The shuffle counts suggest the software HMMA expansion (458 Maxwell instructions per
   HMMA.16816); the HMMA expansion size is the same in the old and new core, so the new
   lowering is reaching shaders it used to reject. Not confirmed yet that these three
   contain HMMA.
2. **Intro movie plays correctly**, with audio problems in its second half.
3. **Hang after the movie** (434 s after start, 10300 frames shown, `hang-20261105-195534.sym`):
   the GPU thread waits on the host GPU in `TextureCacheRuntime::ReleaseTranscodes` (called
   from `TranscodeAstcToBcn`), and the Vulkan worker is inside `vkQueueSubmit2` →
   `radv_ps5_submit`. Just before, `eden_log.txt` logs three times
   `format_lookup_table.cpp:280 assert texture format=0 srgb=false components={2 2 2 2}`
   (an unknown texture format; upstream `format_lookup_table` may be missing a Switch 2
   format). Either a GPU hang from a bad draw/dispatch or a submit that never completes.

## Next steps

1. The post-movie hang. Start from `hang-20261105-195534.sym` and the format=0 asserts: find
   which texture descriptor carries format 0 (TIC word) and whether the following
   draw/dispatch faults the GPU. `ReleaseTranscodes` waits on the scheduler tick; our
   `.wait = false` change in `TranscodeAstcToBcn` (e457cfd52a) means the pending list relies
   on that tick — check it is not waiting on a tick that was never submitted.
2. The slow compute builds: confirm the three shaders contain HMMA (compile
   `.local/lichii-handoff/compute-spirv/*.spv` on the host with RADV's null device,
   `RADV_FORCE_FAMILY=navi10`, or check the lowered programs). If they do, port
   heydemoura/EglantinePS5 `35b485dc2a` (native HMMA lowering, patch 0013: Bananza's HMMA
   shaders shrank 69%). heydemoura's `2f4408bd79` enables software HMMA for Bananza only and
   skips HMMA shaders in other titles; `dbbb23b2ab` (compute shaders as NIR, -30% compile) is
   a later option.
3. Movie audio, second half: compare with upstream's sysmix/audsmx path now in the core
   (`nx2_audio_device_manager.h`, `audio.cpp`).
4. Check Mario Kart World (registry fix dropped) and Pokopia (F2I.U16) still work.
5. Push both `claude/lichii-update` branches (submodule first) and open the PR.
